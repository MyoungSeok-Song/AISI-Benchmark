"""두 실행기가 함께 쓰는 명령행 처리와 배치 진행.

run_single.py와 run_multiturn.py는 '대화 1건을 어떻게 진행하는가'(conduct 함수)만 다르다.
입력 읽기·검증, 실행 대상 선정, 배치 준비, 반복·재시작, 요약은 이 모듈이 맡는다.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

from . import clock, csv_io, fileio, judge_io, paths, validate
from .adapters import create_adapter
from .codebook import fixed_value as codebook_fixed_value, load_codebook
from .context import SETUP_ERRORS, setup_error_message
from .config import load_config
from .exitcodes import EXIT_INTERRUPTED, EXIT_INVALID, EXIT_NOTHING, EXIT_OK
from .ids import IdAllocator
from .issues import report_issues                                           # noqa: F401 (이 모듈의 옛 이름으로 재수출)
from .provenance import git_state, library_version                          # noqa: F401 (옛 위치, export 등이 썼다)
from .records import INPUT_FILES, MANIFEST_FILE, RUN_PARAM_FIELDS, InputIndex, load_inputs   # noqa: F401  (INPUT_FILES는 테스트가 쓴다)
from .session import Batch, RunSession
from .taxonomy import load_taxonomy
from .vocab import MODE_MULTI, MODE_SINGLE, check_vocabulary                 # noqa: F401 (실행기가 conversation_mode 값을 가져다 쓴다)

# 재시작할 때 처음 실행과 같아야 하는 값. 하나라도 다르면 같은 배치로 이어 쓸 수 없다.
_MANIFEST_LOCKED = ("protocol_id", "model_id", "dataset_version", "system_prompt_hash", "input_sha256", "run_params")

EXIT_NOTHING_TO_RUN, EXIT_INVALID_INPUT = EXIT_NOTHING, EXIT_INVALID        # 이 모듈의 옛 이름(테스트가 쓴다)


class PreflightError(Exception):
    """실행 전 점검·모델 선택에서 멈출 때. main이 잡아 한 줄 메시지와 종료 코드 2로 끝낸다(모델 호출 없음)."""


# ── 명령행 ──────────────────────────────────────────────────────────────
def build_parser(description, default_protocol):
    p = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR,
                   help="01_items.csv · 02_item_tags.csv · 03_prompts.csv가 있는 폴더")
    p.add_argument("--out", type=Path, default=paths.DEFAULT_OUTPUT_DIR,
                   help="출력 루트. 이 아래에 <run_batch_id>/ 폴더가 생긴다")
    p.add_argument("--model", default="mock-echo", help="config/models.yaml의 model_id")
    p.add_argument("--protocol", default=default_protocol, help="실행할 protocol_id")
    p.add_argument("--rollouts", type=int, help="문항당 반복 횟수 (기본: runner.yaml default_rollouts)")
    p.add_argument("--items", help="실행할 item_id를 쉼표로 나열 (기본: 프로토콜에 맞는 전체)")
    p.add_argument("--batch-id", help="중단된 배치를 이어서 실행할 때 그 run_batch_id")
    p.add_argument("--allow-unverified", action="store_true",
                   help="검토 미통과·비활성 문항도 실행 (개발 샘플 전용)")
    p.add_argument("--validate-only", action="store_true", help="입력 검증만 하고 끝낸다")
    p.add_argument("--mock-scenario", default="normal", help="모의 어댑터의 기본 시나리오")
    p.add_argument("--mock-plan", type=Path, help="호출별 모의 시나리오 계획 YAML (adapters/mock.py 참고)")
    return p


# ── 준비 단계 ───────────────────────────────────────────────────────────
def select_items(items, config, protocol_id, only_ids, allow_unverified):
    """이 실행기가 돌릴 문항을 고른다. 반환: (선정 문항, 제외 사유별 개수)."""
    selected, skipped = [], Counter()
    for item in items:
        if item["protocol_id"] != protocol_id:
            skipped["다른 프로토콜"] += 1
        elif only_ids and item["item_id"] not in only_ids:
            skipped["--items에 없음"] += 1
        elif not allow_unverified and not _is_eligible(item, config):
            skipped["검토 미통과·비활성 (--allow-unverified 필요)"] += 1
        else:
            selected.append(item)
    return selected, skipped


def _is_eligible(item, config):
    """01 item_review_status: '통과하지 않은 문항이 실행되는 일을 막는다'."""
    return (item["item_review_status"] in config["eligible_item_review_status"]
            and item["lifecycle_status"] in config["eligible_lifecycle_status"])


def load_mock_plan(args):
    if args.mock_plan:
        with open(args.mock_plan, encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {"default": args.mock_scenario}


def check_run_params(codebook, config):
    """실행 전 점검 1: run_params가 코드북 04의 호출 파라미터 필드(RUN_PARAM_FIELDS)에 맞는지. 반환: 문제 설명 목록.

    * 세 키가 모두 있어야 하고(빠지면 04 기록이 거부된다), 다른 키는 받지 않는다.
    * 값은 수여야 한다(문자열 "8192"는 거부).
    * 허용값 목록(overlay enum)이 있으면 그것으로, 없으면 형식 원문의 '…로 고정' 값과 04에 적힐 문자열(to_cell)을
      정확히 대조한다. 그래서 temperature: 0 은 '0.0'과 달라 거부된다(배치 사이 섞임·잠금 불일치 예방).
    여기서 걸러야 하는 까닭: 기록 단계(05 → 04 순서)에서 걸리면 모델 호출(상용이면 유료)이 끝난 뒤라 낭비이고
    짝 없는 05 행이 남는다.
    """
    params = config.get("run_params")
    if not isinstance(params, dict):
        return [f"runner.yaml run_params 블록이 없거나 비어 있음 (필요: {RUN_PARAM_FIELDS})"]
    problems = [f"run_params에 모르는 키 {name!r} (허용: {RUN_PARAM_FIELDS})" for name in params if name not in RUN_PARAM_FIELDS]
    for name in RUN_PARAM_FIELDS:
        if name not in params:
            problems.append(f"run_params.{name}이 없음 (코드북 04 필수 필드)")
            continue
        value = params[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"run_params.{name}={value!r}: 수가 아님 (따옴표 없는 숫자여야 함)")
            continue
        spec = codebook.field("04_runs", name)
        cell = csv_io.to_cell(value)
        problem = spec.check(cell)
        if problem is None and not spec.enum:
            fixed = codebook_fixed_value(spec.format)
            if fixed is not None and fixed != cell:
                problem = f"코드북 형식 원문은 {fixed}로 고정인데 04에는 {cell}로 적힘"
        if problem:
            problems.append(f"run_params.{name}={cell}: {problem} (코드북 04 {name} — config/runner.yaml 또는 overlay 확인)")
    return problems


def check_context_budget(config, adapter_info, protocol_id):
    """실행 전 점검 2: 로컬 서버 길이가 이 프로토콜의 최악 입력을 받을 수 있는지. 반환: 문제 설명 또는 None.

    vLLM 0.30은 입력 상한을 max_model_len − max_tokens로 잡는다. 마지막 턴 입력은 앞 턴 응답(각 최대 한도)과
    프롬프트 몫(context_reserve_tokens)을 더한 것이다.
      max_model_len − max_output_tokens ≥ (planned_round_count − 1) × max_output_tokens + context_reserve_tokens
    adapter_info(어댑터 describe())에 max_model_len이 없으면(상용·모의) 점검하지 않는다.
    """
    max_model_len = adapter_info.get("max_model_len")
    if max_model_len is None:
        return None
    limit = config["run_params"]["max_output_tokens"]
    rounds = config.protocol(protocol_id)["planned_round_count"]
    needed = (rounds - 1) * limit + config["context_reserve_tokens"]
    budget = max_model_len - limit
    if budget < needed:
        return (f"서버 max_model_len {max_model_len} − 출력 한도 {limit} = 입력 상한 {budget} 토큰인데, {protocol_id}"
                f"({rounds}턴)의 최악 입력은 ({rounds} − 1) × {limit} + 여유 {config['context_reserve_tokens']} = {needed} 토큰입니다. "
                "models.yaml server.max_model_len을 올리거나 runner.yaml max_output_tokens를 낮추세요 (vLLM: 입력 상한 = max_model_len − max_tokens)")
    return None


def preflight(args, codebook, config):
    """실행 전 점검: 모델 등록·run_params 허용값·어댑터 생성·서버 길이. 반환: (model, adapter, adapter_info).

    모두 폴더를 만들기 전에 끝낸다. 실패하면 PreflightError(빈 폴더가 남지 않고 다음 배치 번호도 건너뛰지 않는다).
    adapter.describe()는 한 번만 부른다(로컬 서버는 HTTP 호출이다) — 길이 점검과 manifest가 같은 값을 쓴다.
    """
    model = config.models.get(args.model)
    if model is None or not model.enabled:
        raise PreflightError(f"모델 '{args.model}'은 등록되지 않았거나 enabled: false 입니다 (config/models.yaml)")
    problems = check_run_params(codebook, config)
    if problems:
        raise PreflightError("실행 전 점검 실패 — 모델을 호출하지 않았습니다:\n  " + "\n  ".join(problems))
    adapter = create_adapter(model, load_mock_plan(args))
    adapter_info = adapter.describe()
    problem = check_context_budget(config, adapter_info, args.protocol)
    if problem:
        raise PreflightError(f"실행 전 점검 실패 — 모델을 호출하지 않았습니다: {problem}")
    return model, adapter, adapter_info


def open_batch(args, codebook, config, model, adapter, adapter_info, dataset_version, input_digests, input_validation=None):
    """배치 폴더를 새로 만들거나(--batch-id 없음) 기존 배치를 이어 연다. preflight를 통과한 뒤에 부른다.

    input_validation: 입력 검증 요약(오류·경고 건수, 제외 사유별 건수·문항). manifest에 남긴다(잠금 대상 아님).
    """
    today = clock.compact_date(clock.now(config))
    ids = IdAllocator(codebook, args.out, today)
    run_batch_id = args.batch_id or ids.new_batch_id()
    batch_dir = args.out / run_batch_id
    if args.batch_id and not batch_dir.exists():
        raise PreflightError(f"이어 쓸 배치 폴더가 없습니다: {batch_dir}")
    batch_dir.mkdir(parents=True, exist_ok=True)

    batch = Batch(codebook=codebook, config=config, model=model, adapter=adapter, ids=ids,
                  batch_dir=batch_dir, run_batch_id=run_batch_id, dataset_version=dataset_version,
                  protocol_id=args.protocol, library_version=library_version())
    _write_or_check_manifest(batch, input_digests, adapter_info, input_validation or {})
    return batch


def _write_or_check_manifest(batch, input_digests, adapter_info, input_validation):
    """배치의 고정 조건을 manifest에 남기고, 재시작이면 처음과 같은지 확인한다.

    input_validation은 실행 때 화면에만 나오던 검증 결과(건수·제외 문항)를 기록으로 남기는 것이다. 코드북 CSV는 바꾸지 않고,
    이어 쓰기 잠금 대상도 아니다(재시작 때는 그때의 값으로 덮어 쓴다).
    """
    current = {
        "run_batch_id": batch.run_batch_id,
        "protocol_id": batch.protocol_id,
        "model_id": batch.model.model_id,
        "dataset_version": batch.dataset_version,
        "system_prompt_hash": batch.config.system_prompt_hash,
        "input_sha256": input_digests,
        "run_params": {k: csv_io.to_cell(v) for k, v in batch.call_params().items()},   # 04 기록과 같은 문자열
        "execution_library_version": batch.library_version,
        "codebook_overlays": batch.codebook.applied_overlays,
        "adapter_info": adapter_info,
        "input_validation": input_validation,
        "created_at": batch.now(),
    }
    path = batch.dir / MANIFEST_FILE
    if path.exists():
        saved = fileio.read_json(path)
        if "run_params" not in saved:                   # 2026-10-05 전 manifest: 기록된 04 행의 값을 기준으로 삼는다
            saved["run_params"] = _recorded_run_params(batch) or current["run_params"]
        changed = [k for k in _MANIFEST_LOCKED if saved.get(k) != current[k]]       # 키가 없어도 KeyError가 아니라 불일치로
        if changed:
            detail = "; ".join(f"{k}: 저장 {saved.get(k)!r} ↔ 현재 {current[k]!r}" for k in changed)
            raise PreflightError(f"{batch.run_batch_id}에 이어 쓸 수 없습니다. 처음 실행과 다른 값 — {detail}")
        saved["input_validation"] = input_validation    # 재시작 때의 검증 결과로 갱신(잠금 대상 아님)
        fileio.write_json(path, saved)
        batch.log_event("batch_resumed", execution_library_version=batch.library_version,
                        adapter_info=current["adapter_info"])
    else:
        fileio.write_json(path, current)
        batch.log_event("batch_created", adapter_info=current["adapter_info"])


def _recorded_run_params(batch):
    """배치의 04 행에 기록된 호출 파라미터(첫 행 기준). 행이 없으면 None."""
    runs = batch.recorded_runs()
    if not runs:
        return None
    return {name: runs[0][name] for name in batch.call_params()}


# ── 배치 진행 ───────────────────────────────────────────────────────────
def run_batch(batch, items, turns_by_item, rollouts, conduct):
    """선정 문항 × 반복 횟수만큼 실행한다.

    conduct(session, turns): 대화 1건을 진행하는 함수. 실행기마다 다르다.
    turns_by_item: InputIndex.turns (문항 판본 → 턴 행, turn_index 오름차순).
    이미 기록된 (문항, 판본, 모델, 반복 번호)는 건너뛰므로 재시작해도 행이 겹치지 않는다.
    반환: 이번 호출에서 새로 기록한 실행 행 목록.
    """
    batch.discard_orphan_responses()
    done = {(r["item_id"], r["item_version"], r["model_id"], r["rollout_no"]) for r in batch.recorded_runs()}
    new_rows = []
    for item in items:
        turns = turns_by_item[validate.item_key(item)]
        for rollout_no in range(1, rollouts + 1):
            if (item["item_id"], item["item_version"], batch.model.model_id, str(rollout_no)) in done:
                continue
            session = RunSession(batch, item, rollout_no)
            try:
                conduct(session, turns)
            except KeyboardInterrupt:
                # 사람이 멈춘 경우: 여기까지 받은 응답으로 마감하고 배치를 끝낸다.
                new_rows.append(session.finish(len(turns), interrupted=True))
                raise
            new_rows.append(session.finish(len(turns)))
    return new_rows


def print_summary(batch, new_rows):
    runs, responses = batch.recorded_runs(), batch.recorded_responses()
    print(f"\n배치 {batch.run_batch_id} ({batch.protocol_id}, {batch.model.model_id}) → {batch.dir}")
    print(f"  이번에 기록한 실행 {len(new_rows)}건 / 배치 누적 04_runs {len(runs)}행, 05_responses {len(responses)}행")
    print(f"  run_status      {dict(Counter(r['run_status'] for r in runs))}")
    print(f"  stop_reason     {dict(Counter(r['stop_reason'] for r in runs))}")
    print(f"  response_status {dict(Counter(r['response_status'] for r in responses))}")
    truncated = sum(1 for r in responses if r["finish_reason"] == "length")
    if truncated:                               # 출력 한도에서 잘린 응답: 상담 안내 누락 판정의 원인이 될 수 있다(회신 ①)
        print(f"  잘림(length)    {truncated}건 / 응답 {len(responses)}행")
    usage = Counter()
    for row in responses:                       # 토큰은 CSV에 칸이 없어 원본 응답에서 합산한다
        reported = json.loads(row["raw_response_json"]).get("usage") or {}
        # 공급자에 따라 세부 항목이 null이거나 중첩 객체다. 숫자인 항목만 더한다.
        usage.update({k: v for k, v in reported.items() if isinstance(v, int)})
    if usage:
        print(f"  토큰 합계       {dict(usage)}")


# ── 진입점 공통부 ───────────────────────────────────────────────────────
def _load_spec():
    """명세·설정 로드(순서 고정: 분류체계 → 코드북 → 통제어휘 검사 → 설정).

    모듈 전역 이름으로 부른다 — 테스트가 cli.load_codebook·cli.load_config를 치환한다.
    """
    taxonomy = load_taxonomy()
    codebook = load_codebook(taxonomy)
    check_vocabulary(codebook)
    return taxonomy, codebook, load_config()


def _select(args, config, items, issues):
    """실행 대상 문항을 고르고 manifest에 남길 입력 검증 요약을 만든다. 반환: (선정 문항, input_validation dict)."""
    only_ids = set(args.items.split(",")) if args.items else None
    selected, skipped = select_items(items, config, args.protocol, only_ids, args.allow_unverified)
    print(f"실행 대상 {len(selected)}문항" + (f", 제외 {dict(skipped)}" if skipped else ""))
    chosen = {item["item_id"] for item in selected}
    input_validation = {
        "errors": 0, "warnings": len(issues),
        "warning_messages": [str(i) for i in issues],
        "excluded": dict(skipped),
        "excluded_items": sorted(item["item_id"] for item in items if item["item_id"] not in chosen),
        "selected_items": sorted(chosen),
    }
    return selected, input_validation


def main(description, default_protocol, conversation_mode, conduct, argv=None):
    """실행기 공통 진입점: 명세·설정 → 프로토콜 확인 → 입력 읽기·검증 → 문항 선정 → 사전 점검·배치 열기 → 실행 → 틀·요약.

    conversation_mode: 이 실행기가 맡는 대화 방식('single' | 'multi').
                       --protocol이 다른 방식의 프로토콜이면 실행하지 않는다.
    """
    args = build_parser(description, default_protocol).parse_args(argv)
    try:
        taxonomy, codebook, config = _load_spec()
    except SETUP_ERRORS as exc:                      # overlay·설정 파일 문제: traceback 대신 한 줄 + 종료 2
        print(setup_error_message(exc))
        return EXIT_INVALID_INPUT

    protocol = config["protocols"].get(args.protocol)
    if protocol is None or protocol["conversation_mode"] != conversation_mode:
        sys.exit(f"이 실행기는 conversation_mode={conversation_mode} 프로토콜만 실행합니다: {args.protocol}")

    try:
        tables, input_digests = load_inputs(codebook, args.input)
    except (csv_io.CsvFormatError, FileNotFoundError) as exc:
        print(f"입력을 읽을 수 없습니다: {exc}")
        return EXIT_INVALID_INPUT
    index = InputIndex(tables["01_items"], tables["02_item_tags"], tables["03_prompts"], input_digests)
    issues = validate.validate_inputs(codebook, taxonomy, config, *index.tables.values())
    if report_issues(issues):
        print("오류가 있어 실행하지 않습니다.")
        return EXIT_INVALID_INPUT
    if args.validate_only:
        problems = check_run_params(codebook, config)
        for problem in problems:
            print(f"[error] run_params: {problem}")
        return EXIT_INVALID_INPUT if problems else EXIT_OK

    selected, input_validation = _select(args, config, index.tables["01_items"], issues)
    if not selected:
        return EXIT_NOTHING_TO_RUN
    versions = sorted({item["dataset_version"] for item in selected})
    if len(versions) != 1:
        print(f"한 배치에는 dataset_version이 하나여야 합니다: {versions}")
        return EXIT_INVALID_INPUT

    try:
        model, adapter, adapter_info = preflight(args, codebook, config)
        batch = open_batch(args, codebook, config, model, adapter, adapter_info, versions[0], input_digests, input_validation)
    except PreflightError as exc:
        print(str(exc))
        return EXIT_INVALID_INPUT
    rollouts = args.rollouts or config["default_rollouts"]
    try:
        new_rows = run_batch(batch, selected, index.turns, rollouts, conduct)
    except KeyboardInterrupt:
        print(f"\n중단됨. 이어서 실행: --batch-id {batch.run_batch_id}")
        return EXIT_INTERRUPTED
    judge_io.write_template(batch, index)
    print_summary(batch, new_rows)
    return EXIT_OK
