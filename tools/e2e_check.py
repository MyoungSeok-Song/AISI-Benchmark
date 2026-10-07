"""종단 시험(tools/e2e_judge_aggregate.sh)의 결과 확인 단계.

  .venv/bin/python tools/e2e_check.py <e2e 폴더> <현재 규칙 판본> <비교 판본>   (runner/ 폴더에서)

확인하는 것
  06   완료 행의 critical_failure_code 빈칸 0, 검증 오류 0
  07   결과 폴더 2개(count_as_refusal · exclude), 코드북 위반 0, validate_results 문제 0, 분모 행과 result_id 1:1,
       risk_group 행에 ORR, 분모 불변식(validate_denominators), 규칙 판본이 [현재, 비교] 순서
종료 코드: 0 통과, 1 확인 실패, 2 입력·설정 오류.
"""
import argparse
import csv
import sys
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNNER_DIR))

from kyab_runner import csv_io, fileio, ids, judge_io, metrics, paths, validate   # noqa: E402
from kyab_runner.context import prepare                                          # noqa: E402
from kyab_runner.layout import DENOMINATORS_FILE, NOTES_FILE, RESULTS_FILE        # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_INVALID = 0, 1, 2


def check_judgments(env, views):
    """06 전체: 완료 행의 CFC 빈칸 수, 검증 오류 수. 반환: (행 수, 완료 행 수, 빈칸 수, 오류 수)."""
    rows06 = finished = blank = errors = 0
    for view in views:
        rows = judge_io.load_judgments(env.codebook, view.batch.dir)
        rows06 += len(rows)
        finished += sum(1 for r in rows if not env.rules.is_unfinished(r))
        blank += sum(1 for r in rows if not env.rules.is_unfinished(r) and r["critical_failure_code"] == "")
        errors += len(validate.errors_of(judge_io.validate_judgments(env.codebook, env.rules, view, rows)))
    return rows06, finished, blank, errors


def check_results(env, results_dir):
    """결과 폴더 1개. 반환: (요약 한 줄, 통과 여부, 규칙 판본)."""
    rows = csv_io.read_table(env.codebook, "07_results", results_dir / RESULTS_FILE)
    notes = fileio.read_json(results_dir / NOTES_FILE)
    violations = sum(len(env.codebook.check_row("07_results", r)) for r in rows)
    issues = metrics.validate_results(env.codebook, env.rules, rows, {k: v["fr_valid_units"] for k, v in notes["rows"].items()})
    with open(results_dir / DENOMINATORS_FILE, encoding="utf-8-sig", newline="") as f:
        denominators = list(csv.DictReader(f))
    one_to_one = {r["result_id"] for r in denominators} == {r["result_id"] for r in rows}
    orr_rows = [r for r in rows if r["slice_level"] == "risk_group" and r["over_refusal_rate"] != ""]
    version = rows[0]["aggregation_rule_version"]
    summary = (f"{results_dir.name}: 07 {len(rows)}행 코드북 위반 {violations} 검증 문제 {len(issues)} | 분모 {len(denominators)}행 "
               f"result_id 1:1 {one_to_one} | risk_group 행 중 ORR 있음 {len(orr_rows)} | 정책 {notes['provider_block_policy']} "
               f"규칙 {version} | 모의 {notes['mock_judge_used']}")
    ok = violations == 0 and not issues and one_to_one and bool(orr_rows) and metrics.validate_denominators(notes) == []
    return summary, ok, version


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("e2e_dir", type=Path, help="종단 시험 출력 폴더(배치·결과 폴더가 있는 곳)")
    parser.add_argument("current_version", help="현재 규칙 판본(첫 결과 폴더)")
    parser.add_argument("variant_version", help="비교용 규칙 판본(둘째 결과 폴더)")
    parser.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR, help="입력 3종이 있는 폴더")
    args = parser.parse_args(argv)
    prepared = prepare(args.input, ids.batch_dirs(args.e2e_dir))     # 진입점 공통 준비(명세·설정·규칙·입력·배치), 실패는 종료 2
    if prepared is None:
        return EXIT_INVALID
    env, views = prepared.env, prepared.views

    rows06, finished, blank, errors = check_judgments(env, views)
    print(f"06: {rows06}행, 완료 행 {finished}건 중 CFC 빈칸 {blank}건, 검증 오류 {errors}건")
    failed = blank or errors
    results = ids.results_dirs(args.e2e_dir)
    if len(results) != 2:
        print(f"결과 폴더가 2개여야 합니다: {[d.name for d in results]}")
        return EXIT_FAILED
    versions = []
    for results_dir in results:
        summary, ok, version = check_results(env, results_dir)
        print(summary)
        failed = failed or not ok
        versions.append(version)
    if versions != [args.current_version, args.variant_version]:
        print(f"규칙 판본 순서가 다릅니다: {versions} (기대 [{args.current_version}, {args.variant_version}])")
        failed = True
    print("e2e 확인 통과" if not failed else "e2e 확인 실패")
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
