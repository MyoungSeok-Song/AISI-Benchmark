"""판정 결과로 04_runs.csv의 first_fail_turn · first_cfc_turn을 채운다 (구조 검토 S1). 명령행은 tools/apply_judgments.py.

두 필드는 판정에서 나오는 값이라 실행기는 비워 둔다. 이 도구가 판정 뒤에 채운다.
기준은 주 판정 집합의 turn 행이다(config/aggregation_rules.yaml primary_judgment_set).

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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import csv_io, judge_io, paths, validate
from .context import prepare
from .exitcodes import EXIT_INVALID, EXIT_OK
from .records import RUNNER_STAGES, RUNS_FILE
from .vocab import MODE_MULTI, SCOPE_TURN, VERDICT_INCONCLUSIVE

TARGET_FIELDS = ("first_fail_turn", "first_cfc_turn")


@dataclass(frozen=True)
class Plan:
    """배치 1개에 쓸 값과 요약 수치. main이 화면 요약을, apply가 기록을 여기서 읽는다."""
    view: object
    rows: list                      # 두 필드를 채운 04 행 전체(순서 그대로)
    fail_count: int                 # first_fail_turn이 채워진 실행 수
    cfc_count: int                  # first_cfc_turn이 채워진 실행 수
    inconclusive_before_fail: int   # 회신 ④: 보류 턴은 실패로 세지 않으므로 그 뒤의 실패가 '최초'로 잡힌 실행 수(다중턴만)


def plan(env, view):
    """배치 1개에 쓸 값을 계산한다. 반환: (Plan, 멈춘 이유 목록) — 이유가 있으면 Plan은 None."""
    batch = view.batch
    reasons = []
    judgments = judge_io.load_judgments(env.codebook, batch.dir)
    if not judgments:
        return None, ["06_judgments.csv가 없거나 비어 있음"]
    issues = judge_io.validate_batch(env.codebook, env.rules, view, judgments)
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
    if errors:                                             # 검증에 실패한 행으로 주 판정을 고르면 형식 오류(evaluated_at 등)가 예외로 샌다
        return None, reasons

    primary = judge_io.select_primary(env.rules, view, judgments)
    values, incomplete = judge_io.first_turns(env.rules, view, primary)
    if incomplete:
        reasons.append(f"주 판정이 없는 성공 응답이 있는 실행 {len(incomplete)}건 (예: {incomplete[0]})")
    if reasons:
        return None, reasons

    updated = [{**run, "first_fail_turn": values[run["run_id"]][0], "first_cfc_turn": values[run["run_id"]][1]} for run in runs]
    for row in updated:                                    # 쓰기 전에 코드북 검사
        problems = env.codebook.check_row("04_runs", row, stages=RUNNER_STAGES)
        if problems:
            return None, [f"{row['run_id']}: 코드북 검사 실패 {problems}"]
    return Plan(view=view, rows=updated,
                fail_count=sum(1 for r in updated if r["first_fail_turn"]),
                cfc_count=sum(1 for r in updated if r["first_cfc_turn"]),
                inconclusive_before_fail=sum(1 for run in runs
                                             if inconclusive_precedes(view, primary, run, values[run["run_id"]][0]))), []


def inconclusive_precedes(view, primary, run, first_fail_turn):
    """first_fail_turn 앞(실패가 없으면 전체)에 보류 판정 턴이 있는 다중턴 실행인가. 단일턴은 '앞 턴'이 없어 세지 않는다."""
    if view.item_of(run)["conversation_mode"] != MODE_MULTI:
        return False
    limit = int(first_fail_turn) if first_fail_turn else None
    for response in view.successes(run["run_id"]):
        turn = view.turn_index(response)
        if limit is not None and turn >= limit:
            break
        row = primary.get((SCOPE_TURN, response["response_id"]))
        if row is not None and row["verdict"] == VERDICT_INCONCLUSIVE:
            return True
    return False


def apply(env, plan):
    """원본을 백업하고 04_runs.csv를 다시 쓴다. 반환: 백업 파일 경로."""
    batch = plan.view.batch
    path = batch.dir / RUNS_FILE
    backup = path.with_name(f"{RUNS_FILE}.bak-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
    shutil.copy2(path, backup)
    csv_io.rewrite_table(env.codebook, "04_runs", path, plan.rows)
    batch.log_event("judgments_applied", fields=list(TARGET_FIELDS), backup=backup.name,
                    first_fail_turn=plan.fail_count, first_cfc_turn=plan.cfc_count)
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batches", nargs="+", type=Path, help="배치 폴더")
    parser.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR, help="입력 3종이 있는 폴더")
    parser.add_argument("--dry-run", action="store_true", help="계산·검사만 하고 쓰지 않는다")
    args = parser.parse_args(argv)

    prepared = prepare(args.input, args.batches)
    if prepared is None:
        return EXIT_INVALID
    env, views = prepared

    plans, stopped = [], False
    for view in views:
        try:
            planned, reasons = plan(env, view)
        except csv_io.CsvFormatError as exc:              # 이 배치나 형제 배치의 06_judgments.csv가 깨짐
            planned, reasons = None, [str(exc)]
        if reasons:
            stopped = True
            for reason in reasons:
                print(f"{view.batch.run_batch_id}: 멈춤 — {reason}")
        plans.append(planned)
    if stopped:
        print("하나 이상의 배치가 통과하지 못해 아무것도 쓰지 않았습니다.")
        return EXIT_INVALID

    for planned in plans:
        summary = (f"실행 {len(planned.rows)}건 중 first_fail_turn {planned.fail_count}건, first_cfc_turn {planned.cfc_count}건, "
                   f"앞 턴에 보류가 있는 실행 {planned.inconclusive_before_fail}건")
        if args.dry_run:
            print(f"{planned.view.batch.run_batch_id}: (dry-run) {summary}")
        else:
            backup = apply(env, planned)
            print(f"{planned.view.batch.run_batch_id}: {summary} 기록, 백업 {backup.name}")
    return EXIT_OK
