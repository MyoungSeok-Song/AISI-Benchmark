"""판정 실행기: 배치의 응답을 판정기에 넘기고 06_judgments.csv에 기록한다.

배치 폴더마다
  1. 판정 틀의 행(성공 응답마다 turn, 다중턴 실행마다 conversation)을 만든다.
  2. 행마다 판정 입력을 만들어 judge_inputs.jsonl에 쓴다(내부 중간 산출물).
  3. 판정기를 부르고, 사람 재채점 표본(규칙 파일 human_review_sample.rate 비율, 시드 고정)을 표시한다.
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
import sys
from pathlib import Path

from . import csv_io, fileio, ids, judge_io, paths, validate
from .context import prepare
from .exitcodes import EXIT_INVALID, EXIT_NOTHING, EXIT_OK                   # noqa: F401 (테스트가 이 모듈 이름으로 쓴다)
from .judge_io import MOCK_WARNING                                         # noqa: F401 (옛 위치 재수출)
from .judges import create_judge
from .layout import JUDGE_MANIFEST_FILE                                     # noqa: F401 (옛 위치 재수출)
from .vocab import REVIEW_NOT_SELECTED, REVIEW_SELECTED_PENDING, VERDICT_FAIL, VERDICT_INCONCLUSIVE, VERDICT_PASS


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("batches", nargs="+", type=Path, help="배치 폴더 (…/RBATCH-YYYYMMDD-###)")
    p.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR,
                   help="01_items.csv · 02_item_tags.csv · 03_prompts.csv가 있는 폴더")
    p.add_argument("--judge", default="mock-judge", help="config/judges.yaml의 judge_id (--inputs-only·--validate-only에서는 쓰지 않음)")
    mode = p.add_mutually_exclusive_group()       # 둘 다 주면 --inputs-only만 조용히 돌던 문제: argparse 사용 오류(종료 2)
    mode.add_argument("--inputs-only", action="store_true", help="judge_inputs.jsonl만 만들고 끝낸다")
    mode.add_argument("--validate-only", action="store_true", help="이미 있는 06_judgments.csv를 검증만 한다")
    return p


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
        human_review_status=REVIEW_SELECTED_PENDING if selected else REVIEW_NOT_SELECTED,
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


def _prepare_inputs(view, rules):
    """판정 틀 행을 만들고 판정 입력(judge_inputs.jsonl)을 쓴다. 출력은 하지 않는다. 반환: (틀 행, 판정 입력)."""
    rows = judge_io.template_rows(view)
    return rows, judge_io.write_judge_inputs(view, rules, rows)


def judge_batch(env, view, entry, judge, allocator):
    """배치 1개를 판정한다. 반환: 새로 쓴 행 수. 검증 오류면 아무것도 쓰지 않고 None."""
    codebook, rules, batch = env.codebook, env.rules, view.batch
    rows, inputs = _prepare_inputs(view, rules)
    self_identifying = report_blinding(view, rules)

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
    if new_rows:
        print(f"  새 판정 verdict 분포 — " + describe_verdicts(new_rows))
    if report(judge_io.validate_batch(codebook, rules, view, existing + new_rows)):
        return None
    if new_rows:
        csv_io.append_rows(codebook, judge_io.TABLE, batch.dir / ids.JUDGMENTS_FILE, new_rows)
        _record_judge_run(view, entry, judge, rules, rows, new_rows, sample, self_identifying)
    return len(new_rows)


def verdict_counts(rows):
    """판정 행의 범위별 verdict 분포. 반환: {scope: {verdict: 건수}} (회신 ④: 판정 단계에서도 보류 수를 남긴다)."""
    out = {}
    for row in rows:
        bucket = out.setdefault(row["evaluation_scope"], {VERDICT_PASS: 0, VERDICT_FAIL: 0, VERDICT_INCONCLUSIVE: 0, "unfinished": 0})
        bucket[row["verdict"] if row["verdict"] in bucket else "unfinished"] += 1
    return out


def describe_verdicts(rows):
    counts = verdict_counts(rows)
    total = sum(sum(b.values()) for b in counts.values())
    inconclusive = sum(b[VERDICT_INCONCLUSIVE] for b in counts.values())
    parts = [f"{scope}: " + "/".join(f"{k} {v}" for k, v in bucket.items() if v) for scope, bucket in counts.items()]
    return "; ".join(parts) + (f"; 보류 {inconclusive}/{total} = {inconclusive / total:.3f}" if total else "")


def _record_judge_run(view, entry, judge, rules, rows, new_rows, sample, self_identifying):
    """무엇으로 판정했는지 judge_manifest.json(판정 실행마다 한 항목)과 보조 로그에 남긴다.

    self_identifying: report_blinding이 이미 찾은 {response_id: [패턴]} (응답 전체를 다시 훑지 않는다).
    """
    batch = view.batch
    inputs_digest = fileio.sha256_file(batch.dir / judge_io.JUDGE_INPUTS_FILE)
    record = {
        "judged_at": batch.now(),
        "judge_id": entry.judge_id, "judge_type": entry.judge_type, "judge_version": entry.judge_version,
        "judge_prompt_id": entry.judge_prompt_id, "judge_prompt_version": entry.judge_prompt_version,
        "production": entry.production,
        "warning": "" if entry.production else MOCK_WARNING,
        "judge_info": judge.describe(),
        "rows_written": len(new_rows),
        "judgment_slots": len(rows),
        "verdict_distribution": verdict_counts(new_rows),
        "human_review_sample": {**rules["human_review_sample"], "selected": len(sample)},
        "aggregation_rule_id": rules.rule_id, "aggregation_rule_version": rules.rule_version,
        "rules_sha256": rules.sha256,
        "judge_inputs_file": judge_io.JUDGE_INPUTS_FILE, "judge_inputs_sha256": inputs_digest,
        # 눈가림 점검: 응답 본문이 스스로 모델명을 말한 것으로 보이는 응답 (원문은 고치지 않음)
        "self_identifying_responses": self_identifying,
    }
    path = batch.dir / JUDGE_MANIFEST_FILE
    manifest = fileio.read_json(path) if path.exists() else {"judge_runs": []}
    manifest["judge_runs"].append(record)
    fileio.write_json(path, manifest)                # 원자적 쓰기: 끊겨도 잘린 manifest가 남지 않는다
    batch.log_event("judgments_written", judge_id=entry.judge_id, production=entry.production,
                    rows_written=len(new_rows))


def main(argv=None):
    args = build_parser().parse_args(argv)
    prepared = prepare(args.input, args.batches)
    if prepared is None:
        return EXIT_INVALID
    env, views = prepared.env, prepared.views

    if args.inputs_only:
        for view in views:
            _, inputs = _prepare_inputs(view, env.rules)
            print(f"{view.batch.run_batch_id}: 판정 입력 {len(inputs)}건 → {view.batch.dir / judge_io.JUDGE_INPUTS_FILE}")
            report_blinding(view, env.rules)
        return EXIT_OK

    if args.validate_only:
        return validate_only(env, views)

    entry = env.rules.judges.get(args.judge)
    if entry is None:
        print(f"판정기 '{args.judge}'는 등록되지 않았습니다 (config/judges.yaml)")
        return EXIT_INVALID
    try:
        judge = create_judge(entry, env.rules)
    except ValueError as exc:                     # 모르는 adapter·판정기 자체의 설정 오류
        print(f"판정기를 만들 수 없습니다: {exc}")
        return EXIT_INVALID
    if not entry.production:
        print(f"주의: {MOCK_WARNING}")

    # judgment_id는 출력 루트 단위로 전역 고유다. 루트마다 발급기를 하나씩 둔다.
    allocators, written, failed = {}, 0, False
    for view in views:
        root = view.batch.dir.parent
        try:
            if root not in allocators:            # 실패한 발급기는 남기지 않는다 — 깨진 루트는 전역 고유를 확인할 수 없어 배치마다 실패가 맞다
                allocators[root] = ids.judgment_id_allocator(env.codebook, root)
            count = judge_batch(env, view, entry, judge, allocators[root])
        except csv_io.CsvFormatError as exc:      # 이 배치나 형제 배치의 06이 깨짐
            print(f"{view.batch.run_batch_id}: {exc}")
            failed = True
            continue
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
            print(f"{view.batch.run_batch_id}: 06_judgments {len(judgments)}행")
            if not judgments:
                codes.append(EXIT_NOTHING)
                continue
            errors = report(judge_io.validate_batch(env.codebook, env.rules, view, judgments))
        except csv_io.CsvFormatError as exc:      # 이 배치나 형제 배치(전역 고유 검사)의 06이 깨짐
            print(f"{view.batch.run_batch_id}: {exc}")
            codes.append(EXIT_INVALID)
            continue
        codes.append(EXIT_INVALID if errors else EXIT_OK)
    return max(codes)


if __name__ == "__main__":
    sys.exit(main())
