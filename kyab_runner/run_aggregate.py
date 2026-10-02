"""집계 실행기: 배치들의 실행(04·05)과 판정(06)에서 지표를 계산해 07_results.csv를 만든다.

  1. 배치마다 06_judgments.csv를 검증한다. 오류가 있으면 집계하지 않는다.
  2. 본평가에 쓸 수 없는 판정기(모의)의 판정이 섞여 있으면 거부한다(--allow-mock-judge로만 허용).
  3. 모델 × 슬라이스마다 지표를 계산한다(metrics.py, 규칙은 config/aggregation_rules.yaml).
  4. 결과를 검증한 뒤 <출력 루트>/RESULTS-YYYYMMDD-###/ 에 쓴다.
       07_results.csv       코드북 35열
       results_notes.json   07에 칸이 없는 보조 기록(분모, 제외·가상 판정 건수, 코드북에 없는 분해,
                            코드북 협의 후보). 코드북 표가 아니다.

집계할 때마다 새 폴더와 새 result_id가 생긴다. 이미 쓴 결과 행은 고치지 않는다.

사용 예 (runner/ 폴더에서)
  .venv/bin/python -m kyab_runner.run_aggregate samples/output/RBATCH-20261002-001 samples/output/RBATCH-20261002-002
  .venv/bin/python -m kyab_runner.run_aggregate --allow-mock-judge <배치 폴더> ...     # 모의 판정으로 경로 확인
  .venv/bin/python -m kyab_runner.run_aggregate --rules <다른 규칙 파일> <배치 폴더> ...   # 규칙을 바꿔 비교

종료 코드: 0 정상, 1 집계할 실행이 없음, 2 입력·검증 오류 또는 거부.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import csv_io, ids, judge_io, metrics, paths, validate
from .context import RecordsError, load_environment, open_views
from .run_judge import MOCK_WARNING, foreign_judgment_ids

NOTES_FILE = "results_notes.json"
EXIT_OK, EXIT_NOTHING, EXIT_INVALID = 0, 1, 2

# 코드북 07에 칸이 없어 results_notes.json에만 두는 항목. 열을 추가하지 않고 협의 후보로만 적는다 (S7).
CODEBOOK_CANDIDATES = [
    "대조 문항의 설계 위험군 연결 필드 부재 → 위험군별 ORR 산출 불가 (대조 문항은 primary_risk가 공란이라 risk_group·risk_age_turn 행에 들어가지 않음)",
    "slice_level에 case_type·성별(user_gender)·컴패니언 구분 없음 → 이 분해는 extra_slices에만 있음 (수행계획서 v1.1의 성별 보고 요구)",
    "유효 평가 대상 수(FR 분모)와 inconclusive·판정 없음 건수를 적을 열 없음 → rows.<result_id>에만 있음",
    "집계에서 뺀 실행 수(stop_reason별)와 차단을 거절로 센 건수를 적을 열 없음",
    "판정기 식별(judge_id)이 07에 없어 모의 판정·실제 판정으로 만든 결과를 07만으로는 구분할 수 없음",
    "CRRI 임계값을 적을 열 없음(aggregation_rule_id·version으로만 추적)",
    "06: judge_status가 failed·needs_review인 행의 verdict·점수 필수성 (현재 가정 J4로 빈값 허용)",
    "06: 사람 검토가 끝났을 때 llm 행의 human_review_status를 고칠지(append-only와 충돌) — 현재는 human 행의 존재로 판단",
]


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("batches", nargs="+", type=Path, help="배치 폴더 (…/RBATCH-YYYYMMDD-###)")
    p.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR,
                   help="01_items.csv · 02_item_tags.csv · 03_prompts.csv가 있는 폴더")
    p.add_argument("--out", type=Path, help="결과 폴더를 만들 출력 루트 (기본: 배치 폴더들의 상위 폴더)")
    p.add_argument("--rules", type=Path, default=paths.AGGREGATION_RULES_YAML,
                   help="판정·집계 규칙 파일 (기본: config/aggregation_rules.yaml)")
    p.add_argument("--allow-mock-judge", action="store_true",
                   help="본평가에 쓸 수 없는 판정기(모의)의 판정으로도 집계한다 (경로 확인용)")
    return p


def load_valid_judgments(env, views):
    """배치마다 06을 읽고 검증한다. 반환: ([(view, 판정 행)], 경고 수) — 오류가 있으면 None."""
    loaded, warnings, failed = [], 0, False
    for view in views:
        judgments = judge_io.load_judgments(env.codebook, view.batch.dir)
        if not judgments:
            print(f"{view.batch.run_batch_id}: 06_judgments.csv가 없거나 비어 있습니다.")
            failed = True
            continue
        issues = judge_io.validate_judgments(env.codebook, env.rules, view, judgments,
                                             foreign_judgment_ids(env.codebook, view))
        errors = validate.errors_of(issues)
        for issue in errors:
            print(issue)
        if errors:
            print(f"{view.batch.run_batch_id}: 06 검증 오류 {len(errors)}건")
            failed = True
        warnings += len(issues) - len(errors)
        loaded.append((view, judgments))
    return (None if failed else loaded), warnings


def judges_in_primary(env, loaded):
    """주 판정 집합에 쓰인 판정기별 행 수."""
    counts = Counter()
    for view, judgments in loaded:
        counts.update(row["judge_id"] for row in judge_io.select_primary(env.rules, view, judgments).values())
    return counts


def output_root(args):
    if args.out:
        return args.out
    parents = {batch.resolve().parent for batch in args.batches}
    if len(parents) != 1:
        sys.exit("배치 폴더들의 상위 폴더가 서로 다릅니다. --out으로 출력 루트를 지정하세요.")
    return parents.pop()


def print_summary(rows):
    """화면 요약. 빈값은 '-'로 보인다. 전체 값은 07_results.csv에 있다."""
    shown = (("FR", "failure_rate"), ("CFR", "critical_failure_rate"), ("MRS", "mean_rubric_score"),
             ("ORR", "over_refusal_rate"), ("TRR", "referral_rate"), ("CRRI", "crri_mean"))
    print(f"\n{'model_id':<22} {'slice':<14} {'key':<36} runs  " + " ".join(f"{name:<8}" for name, _ in shown))
    for row in rows:
        key = ",".join(json.loads(row["slice_key_json"]).values()) or "-"
        print(f"{row['model_id']:<22} {row['slice_level']:<14} {key:<36} {row['n_runs']:>4}  "
              + " ".join(f"{row[field] or '-':<8}" for _, field in shown))


def main(argv=None):
    args = build_parser().parse_args(argv)
    env = load_environment(args.rules)
    rules = env.rules
    try:
        _, views, notices = open_views(env, args.input, args.batches)
        loaded, judgment_warnings = load_valid_judgments(env, views)
    except (csv_io.CsvFormatError, FileNotFoundError, RecordsError) as exc:
        print(f"배치 또는 입력을 읽을 수 없습니다: {exc}")
        return EXIT_INVALID
    for notice in notices:
        print(f"주의: {notice}")
    if loaded is None:
        print("판정 기록에 문제가 있어 집계하지 않습니다.")
        return EXIT_INVALID

    # 모의 판정기 차단: 주 판정 집합에 본평가용이 아닌 판정기의 행이 있으면 기본으로 거부한다.
    judges = judges_in_primary(env, loaded)
    mock_used = sorted(set(judges) & rules.non_production_judges())
    if mock_used and not args.allow_mock_judge:
        print(f"집계를 거부합니다: 본평가에 쓸 수 없는 판정기의 판정이 있습니다 {mock_used}. "
              "경로 확인용이면 --allow-mock-judge를 주세요.")
        return EXIT_INVALID
    if mock_used:
        print(f"주의: {MOCK_WARNING}")

    cases = [case for view, judgments in loaded for case in metrics.build_cases(rules, view, judgments)]
    if not any(case.included for case in cases):
        print("집계할 실행이 없습니다.")
        return EXIT_NOTHING

    timezone = ZoneInfo(env.config["timezone"])
    calculated_at = datetime.now(timezone).isoformat(timespec="milliseconds")
    root = output_root(args)
    allocator = ids.result_id_allocator(env.codebook, root)
    rows, notes = metrics.aggregate(env.codebook, rules, cases, allocator.new, calculated_at)

    valid_units = {result_id: note["fr_valid_units"] for result_id, note in notes["rows"].items()}
    issues = metrics.validate_results(env.codebook, rules, rows, valid_units)
    for issue in issues:
        print(issue)
    if validate.errors_of(issues):
        print("07 결과가 검증을 통과하지 못해 쓰지 않습니다.")
        return EXIT_INVALID

    results_dir = root / ids.new_results_dir_name(root, datetime.now(timezone).strftime("%Y%m%d"))
    results_dir.mkdir(parents=True)
    csv_io.append_rows(env.codebook, metrics.TABLE, results_dir / ids.RESULTS_FILE, rows)
    record = {
        "note": "07_results.csv의 보조 기록. 코드북 7 CSV에 속하지 않는다. 07에 열을 추가하지 않고 여기에 둔다.",
        "calculated_at": calculated_at,
        "aggregation_rule_id": rules.rule_id, "aggregation_rule_version": rules.rule_version,
        "rules_file": str(args.rules), "rules_sha256": rules.sha256,
        "provider_block_policy": rules["aggregation"]["provider_block_policy"],
        "mock_judge_used": bool(mock_used),
        "warning": MOCK_WARNING if mock_used else "",
        "primary_judgments_by_judge": dict(judges),
        "source_batches": [{"run_batch_id": view.batch.run_batch_id, "model_id": view.batch.manifest["model_id"],
                            "protocol_id": view.batch.protocol_id, "runs": len(view.runs),
                            "judgment_rows": len(judgments)} for view, judgments in loaded],
        "judgment_validation_warnings": judgment_warnings,
        **notes,
        "codebook_candidates": CODEBOOK_CANDIDATES,
    }
    (results_dir / NOTES_FILE).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    print_summary(rows)
    print(f"\n07_results {len(rows)}행 → {results_dir / ids.RESULTS_FILE}")
    print(f"보조 기록 → {results_dir / NOTES_FILE}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
