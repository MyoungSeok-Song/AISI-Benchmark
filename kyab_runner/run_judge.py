"""판정 실행기: 배치의 응답을 판정기에 넘기고 06_judgments.csv에 기록한다.

배치 폴더마다
  1. 판정 틀의 행(성공 응답마다 turn, 다중턴 실행마다 conversation)을 만든다.
  2. 행마다 판정 입력을 만들어 judge_inputs.jsonl에 쓴다(내부 중간 산출물).
  3. 판정기를 부르고, 사람 재채점 표본(20%, 시드 고정)을 표시한다.
  4. 기존 행과 새 행을 함께 검증한 뒤, 문제가 없을 때만 06_judgments.csv에 덧붙인다.
  5. judge_manifest.json과 보조 로그에 무엇으로 판정했는지 남긴다.

이미 쓴 판정 행은 고치지 않는다(append-only). 같은 판정기가 같은 태그 판본으로 이미 판정한
자리는 건너뛰므로, 중간에 끊겨도 다시 실행하면 빠진 자리만 채운다.

지금 등록된 판정기는 모의 판정기(mock-judge)뿐이다. 모의 판정 결과는 본평가에 쓸 수 없다.

사용 예 (runner/ 폴더에서)
  .venv/bin/python -m kyab_runner.run_judge samples/output/RBATCH-20261002-001
  .venv/bin/python -m kyab_runner.run_judge --inputs-only  <배치 폴더> ...     # 판정 입력만 만든다
  .venv/bin/python -m kyab_runner.run_judge --validate-only <배치 폴더> ...    # 채워진 06을 검증만 한다

종료 코드: 0 정상, 1 판정할 것·검증할 것이 없음, 2 입력·검증 오류.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import csv_io, ids, judge_io, paths, validate
from .context import RecordsError, load_environment, open_views
from .judges import create_judge

JUDGE_MANIFEST_FILE = "judge_manifest.json"
MOCK_WARNING = "모의 판정기 결과입니다. 실제 채점이 아니므로 본평가·보고에 쓸 수 없습니다."

EXIT_OK, EXIT_NOTHING, EXIT_INVALID = 0, 1, 2


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("batches", nargs="+", type=Path, help="배치 폴더 (…/RBATCH-YYYYMMDD-###)")
    p.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR,
                   help="01_items.csv · 02_item_tags.csv · 03_prompts.csv가 있는 폴더")
    p.add_argument("--judge", default="mock-judge", help="config/judges.yaml의 judge_id")
    p.add_argument("--inputs-only", action="store_true", help="judge_inputs.jsonl만 만들고 끝낸다")
    p.add_argument("--validate-only", action="store_true", help="이미 있는 06_judgments.csv를 검증만 한다")
    return p


def foreign_judgment_ids(codebook, view):
    """같은 출력 루트의 다른 배치에서 쓰인 judgment_id."""
    by_batch = ids.judgment_ids_by_batch(codebook, view.batch.dir.parent)
    return [i for name, used in by_batch.items() if name != view.batch.dir.name for i in used]


def judgment_row(columns, template_row, result, entry, rules, judgment_id, selected, evaluated_at):
    """판정 틀의 행 + 판정 결과 -> 06_judgments 행."""
    row = dict.fromkeys(columns, "")
    row.update(template_row)                                # evaluation_scope, response_id, tag_revision, rubric_id
    row.update(result.scores)                               # d1~d6, crri 4축 (None은 빈값이 된다)
    row.update(
        judgment_id=judgment_id,
        rubric_version=rules.rubric_version(template_row["rubric_id"]),
        judge_type=entry.judge_type, judge_id=entry.judge_id, judge_version=entry.judge_version,
        judge_prompt_id=entry.judge_prompt_id, judge_prompt_version=entry.judge_prompt_version,
        critical_failure_code=result.critical_failure_code, verdict=result.verdict,
        over_refusal=result.over_refusal, referral_present=result.referral_present, reason=result.reason,
        human_review_status="selected_pending" if selected else "not_selected",
        judge_status=result.judge_status, evaluated_at=evaluated_at,
    )
    return {name: csv_io.to_cell(value) for name, value in row.items()}


def report(issues):
    """Issue를 출력하고 오류 목록을 돌려준다. 옛 형식(빈 CFC) 행이 있으면 재생성 안내를 덧붙인다."""
    for issue in issues:
        print(issue)
    errors = validate.errors_of(issues)
    print(f"  06 검증: 오류 {len(errors)}건, 경고 {len(issues) - len(errors)}건")
    hint = judge_io.legacy_hint(issues)
    if hint:
        print(f"  {hint}")
    return errors


def report_blinding(view, rules):
    """눈가림 점검: 응답 본문이 스스로 모델명을 말한 건수를 알린다. 반환: {response_id: [패턴]}."""
    found = judge_io.self_identifying_responses(view, rules)
    if found:
        sample = ", ".join(f"{rid}({'·'.join(hits)})" for rid, hits in list(found.items())[:3])
        print(f"주의: {view.batch.run_batch_id}: 응답 본문에 모델 자기 지칭으로 보이는 표현 {len(found)}건 "
              f"— 판정 입력의 눈가림이 깨질 수 있음 (예: {sample}). 원문은 그대로 둔다.")
    return found


def judge_batch(env, view, entry, judge, allocator):
    """배치 1개를 판정한다. 반환: 새로 쓴 행 수. 검증 오류면 아무것도 쓰지 않고 None."""
    codebook, rules, batch = env.codebook, env.rules, view.batch
    rows = judge_io.template_rows(view)
    inputs = judge_io.write_judge_inputs(view, rules, rows)
    report_blinding(view, rules)

    existing = judge_io.load_judgments(codebook, batch.dir)
    done = {(judge_io.judgment_key(r), r["tag_revision"]) for r in existing if r["judge_id"] == entry.judge_id}
    sample_spec = rules["human_review_sample"]
    sample = judge_io.select_human_sample([judge_io.judgment_key(r) for r in rows],
                                          sample_spec["rate"], sample_spec["seed"])
    columns = codebook.columns(judge_io.TABLE)
    new_rows = []
    for template_row, judge_input in zip(rows, inputs):
        key = judge_io.judgment_key(template_row)
        if (key, template_row["tag_revision"]) in done:
            continue                                        # 이 판정기가 이 태그 판본으로 이미 판정한 자리
        result = judge.judge(judge_input)
        new_rows.append(judgment_row(columns, template_row, result, entry, rules, allocator.new(),
                                     key in sample, batch.now()))

    print(f"{batch.run_batch_id}: 판정 자리 {len(rows)}개, 새 판정 {len(new_rows)}개, 건너뜀 {len(rows) - len(new_rows)}개")
    if report(judge_io.validate_judgments(codebook, rules, view, existing + new_rows,
                                          foreign_judgment_ids(codebook, view))):
        return None
    if new_rows:
        csv_io.append_rows(codebook, judge_io.TABLE, batch.dir / ids.JUDGMENTS_FILE, new_rows)
        _record_judge_run(view, entry, judge, rules, rows, new_rows, sample)
    return len(new_rows)


def _record_judge_run(view, entry, judge, rules, rows, new_rows, sample):
    """무엇으로 판정했는지 judge_manifest.json(판정 실행마다 한 항목)과 보조 로그에 남긴다."""
    batch = view.batch
    inputs_digest = hashlib.sha256((batch.dir / judge_io.JUDGE_INPUTS_FILE).read_bytes()).hexdigest()
    record = {
        "judged_at": batch.now(),
        "judge_id": entry.judge_id, "judge_type": entry.judge_type, "judge_version": entry.judge_version,
        "judge_prompt_id": entry.judge_prompt_id, "judge_prompt_version": entry.judge_prompt_version,
        "production": entry.production,
        "warning": "" if entry.production else MOCK_WARNING,
        "judge_info": judge.describe(),
        "rows_written": len(new_rows),
        "judgment_slots": len(rows),
        "human_review_sample": {**rules["human_review_sample"], "selected": len(sample)},
        "aggregation_rule_id": rules.rule_id, "aggregation_rule_version": rules.rule_version,
        "rules_sha256": rules.sha256,
        "judge_inputs_file": judge_io.JUDGE_INPUTS_FILE, "judge_inputs_sha256": inputs_digest,
        # 눈가림 점검: 응답 본문이 스스로 모델명을 말한 것으로 보이는 응답 (원문은 고치지 않음)
        "self_identifying_responses": judge_io.self_identifying_responses(view, rules),
    }
    path = batch.dir / JUDGE_MANIFEST_FILE
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"judge_runs": []}
    manifest["judge_runs"].append(record)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    batch.log_event("judgments_written", judge_id=entry.judge_id, production=entry.production,
                    rows_written=len(new_rows))


def main(argv=None):
    args = build_parser().parse_args(argv)
    env = load_environment()
    try:
        _, views, notices = open_views(env, args.input, args.batches)
    except (csv_io.CsvFormatError, FileNotFoundError, RecordsError) as exc:
        print(f"배치 또는 입력을 읽을 수 없습니다: {exc}")
        return EXIT_INVALID
    for notice in notices:
        print(f"주의: {notice}")

    if args.inputs_only:
        for view in views:
            inputs = judge_io.write_judge_inputs(view, env.rules, judge_io.template_rows(view))
            print(f"{view.batch.run_batch_id}: 판정 입력 {len(inputs)}건 → {view.batch.dir / judge_io.JUDGE_INPUTS_FILE}")
            report_blinding(view, env.rules)
        return EXIT_OK

    if args.validate_only:
        return validate_only(env, views)

    entry = env.rules.judges.get(args.judge)
    if entry is None:
        sys.exit(f"판정기 '{args.judge}'는 등록되지 않았습니다 (config/judges.yaml)")
    judge = create_judge(entry, env.rules)
    if not entry.production:
        print(f"주의: {MOCK_WARNING}")

    # judgment_id는 출력 루트 단위로 전역 고유다. 루트마다 발급기를 하나씩 둔다.
    allocators, written, failed = {}, 0, False
    for view in views:
        root = view.batch.dir.parent
        if root not in allocators:
            allocators[root] = ids.judgment_id_allocator(env.codebook, root)
        count = judge_batch(env, view, entry, judge, allocators[root])
        if count is None:
            failed = True
            print(f"{view.batch.run_batch_id}: 검증 오류가 있어 기록하지 않았습니다.")
        else:
            written += count
    if failed:
        return EXIT_INVALID
    return EXIT_OK if written else EXIT_NOTHING


def validate_only(env, views):
    """배치마다 06_judgments.csv를 검증한다. 오류가 하나라도 있으면 EXIT_INVALID."""
    codes = []
    for view in views:
        try:
            judgments = judge_io.load_judgments(env.codebook, view.batch.dir)
        except csv_io.CsvFormatError as exc:
            print(f"{view.batch.run_batch_id}: {exc}")
            codes.append(EXIT_INVALID)
            continue
        print(f"{view.batch.run_batch_id}: 06_judgments {len(judgments)}행")
        if not judgments:
            codes.append(EXIT_NOTHING)
            continue
        errors = report(judge_io.validate_judgments(env.codebook, env.rules, view, judgments,
                                                    foreign_judgment_ids(env.codebook, view)))
        codes.append(EXIT_INVALID if errors else EXIT_OK)
    return max(codes)


if __name__ == "__main__":
    sys.exit(main())
