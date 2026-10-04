"""판정 결과로 04_runs.csv의 first_fail_turn · first_cfc_turn을 채운다 (구조 검토 S1).

두 필드는 판정에서 나오는 값이라 실행기는 비워 둔다. 이 도구가 판정 뒤에 채운다.
기준은 주 판정 집합의 turn 행이다(config/aggregation_rules.yaml primary_judgment_set).

  runner/.venv/bin/python tools/apply_judgments.py <배치 폴더> [배치 폴더 ...] [--input 입력 폴더] [--dry-run]

지키는 것
  * 고치는 열은 first_fail_turn, first_cfc_turn 둘뿐이다. 다른 열과 행 순서는 그대로다.
  * 쓰기 전에 원본을 04_runs.csv.bak-<시각>으로 복사해 둔다.
  * 두 필드 중 하나라도 이미 값이 있는 행이 있으면 덮어쓰지 않고 멈춘다.
  * 06_judgments.csv에 검증 오류가 있거나, 성공 응답 중 주 판정이 없는 것이 있으면 멈춘다.
  * 배치 여러 개를 주면 전부 검사한 뒤, 모두 통과했을 때만 쓴다.

모의 판정기의 판정으로 채운 값은 실제 채점 결과가 아니다. 본평가 배치에는 쓰지 않는다.

종료 코드: 0 기록함(또는 --dry-run 통과), 2 멈춤(이유를 출력).
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNNER_DIR))

from kyab_runner import csv_io, judge_io, paths, validate          # noqa: E402
from kyab_runner.context import SETUP_ERRORS, RecordsError, load_environment, open_views   # noqa: E402
from kyab_runner.records import RUNS_FILE                           # noqa: E402
from kyab_runner.run_judge import foreign_judgment_ids             # noqa: E402

TARGET_FIELDS = ("first_fail_turn", "first_cfc_turn")
RUN_STAGES = ("실행 자동기록 필수",)        # 04_runs에서 필수 검사를 하는 생성 단계 (session.RUNNER_STAGES와 같음)


def plan(env, view):
    """배치 1개에 쓸 값을 계산한다. 반환: (고친 04 행 목록, 멈춘 이유 목록)."""
    batch = view.batch
    reasons = []
    judgments = judge_io.load_judgments(env.codebook, batch.dir)
    if not judgments:
        return None, ["06_judgments.csv가 없거나 비어 있음"]
    issues = judge_io.validate_judgments(env.codebook, env.rules, view, judgments,
                                         foreign_judgment_ids(env.codebook, view))
    errors = validate.errors_of(issues)
    if errors:
        reasons.append(f"06 검증 오류 {len(errors)}건 (예: {errors[0]})")
        hint = judge_io.legacy_hint(issues)
        if hint:
            reasons.append(hint)

    runs = batch.recorded_runs()
    filled = [r["run_id"] for r in runs if any(r[f] for f in TARGET_FIELDS)]
    if filled:
        reasons.append(f"이미 값이 있는 실행 {len(filled)}건 (예: {filled[0]}) — 덮어쓰지 않음")

    primary = judge_io.select_primary(env.rules, view, judgments)
    values, incomplete = judge_io.first_turns(env.rules, view, primary)
    if incomplete:
        reasons.append(f"주 판정이 없는 성공 응답이 있는 실행 {len(incomplete)}건 (예: {incomplete[0]})")
    if reasons:
        return None, reasons

    updated = []
    for run in runs:
        fail, cfc = values[run["run_id"]]
        updated.append({**run, "first_fail_turn": fail, "first_cfc_turn": cfc})
    # 회신 ④: 보류 턴은 실패로 세지 않으므로 그 뒤의 실패가 '최초'로 잡힌다. 그런 실행 수를 함께 보인다.
    view.inconclusive_before_fail = sum(1 for run in runs if _inconclusive_precedes(view, primary, run, values[run["run_id"]][0]))
    for row in updated:                                    # 쓰기 전에 코드북 검사
        problems = env.codebook.check_row("04_runs", row, stages=RUN_STAGES)
        if problems:
            return None, [f"{row['run_id']}: 코드북 검사 실패 {problems}"]
    return updated, []


def _inconclusive_precedes(view, primary, run, first_fail_turn):
    """first_fail_turn 앞(실패가 없으면 전체)에 보류 판정 턴이 있는 실행인가."""
    limit = int(first_fail_turn) if first_fail_turn else None
    for response in view.successes(run["run_id"]):
        turn = view.turn_index(response)
        if limit is not None and turn >= limit:
            break
        row = primary.get(("turn", response["response_id"]))
        if row is not None and row["verdict"] == "inconclusive":
            return True
    return False


def apply(env, view, updated):
    """원본을 백업하고 04_runs.csv를 다시 쓴다. 반환: 백업 파일 경로."""
    batch = view.batch
    path = batch.dir / RUNS_FILE
    backup = path.with_name(f"{RUNS_FILE}.bak-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
    shutil.copy2(path, backup)
    csv_io.rewrite_table(env.codebook, "04_runs", path, updated)
    batch.log_event("judgments_applied", fields=list(TARGET_FIELDS), backup=backup.name,
                    first_fail_turn=sum(1 for r in updated if r["first_fail_turn"]),
                    first_cfc_turn=sum(1 for r in updated if r["first_cfc_turn"]))
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batches", nargs="+", type=Path, help="배치 폴더")
    parser.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR, help="입력 3종이 있는 폴더")
    parser.add_argument("--dry-run", action="store_true", help="계산·검사만 하고 쓰지 않는다")
    args = parser.parse_args(argv)

    try:
        env = load_environment()
    except SETUP_ERRORS as exc:
        print(f"명세·설정을 읽을 수 없습니다: {type(exc).__name__}: {exc}")
        return 2
    try:
        _, views, notices = open_views(env, args.input, args.batches)
    except (csv_io.CsvFormatError, FileNotFoundError, RecordsError) as exc:
        print(f"배치 또는 입력을 읽을 수 없습니다: {exc}")
        return 2
    for notice in notices:
        print(f"주의: {notice}")

    plans, stopped = [], False
    for view in views:
        updated, reasons = plan(env, view)
        if reasons:
            stopped = True
            for reason in reasons:
                print(f"{view.batch.run_batch_id}: 멈춤 — {reason}")
        plans.append((view, updated))
    if stopped:
        print("하나 이상의 배치가 통과하지 못해 아무것도 쓰지 않았습니다.")
        return 2

    for view, updated in plans:
        fails = sum(1 for r in updated if r["first_fail_turn"])
        cfcs = sum(1 for r in updated if r["first_cfc_turn"])
        summary = (f"실행 {len(updated)}건 중 first_fail_turn {fails}건, first_cfc_turn {cfcs}건, "
                   f"앞 턴에 보류가 있는 실행 {view.inconclusive_before_fail}건")
        if args.dry_run:
            print(f"{view.batch.run_batch_id}: (dry-run) {summary}")
        else:
            backup = apply(env, view, updated)
            print(f"{view.batch.run_batch_id}: {summary} 기록, 백업 {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
