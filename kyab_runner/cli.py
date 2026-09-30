"""두 실행기가 함께 쓰는 명령행 처리와 배치 진행.

run_single.py와 run_multiturn.py는 '대화 1건을 어떻게 진행하는가'(conduct 함수)만 다르다.
입력 읽기·검증, 실행 대상 선정, 배치 준비, 반복·재시작, 요약은 이 모듈이 맡는다.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import __version__, csv_io, judge_io, paths, validate
from .adapters import create_adapter
from .codebook import load_codebook
from .config import load_config
from .ids import IdAllocator
from .session import Batch, RunSession
from .taxonomy import load_taxonomy

INPUT_FILES = {"01_items": "01_items.csv", "02_item_tags": "02_item_tags.csv", "03_prompts": "03_prompts.csv"}
MANIFEST_FILE = "batch_manifest.json"
# 재시작할 때 처음 실행과 같아야 하는 값. 하나라도 다르면 같은 배치로 이어 쓸 수 없다.
_MANIFEST_LOCKED = ("protocol_id", "model_id", "dataset_version", "system_prompt_hash", "input_sha256")

EXIT_OK, EXIT_NOTHING_TO_RUN, EXIT_INVALID_INPUT, EXIT_INTERRUPTED = 0, 1, 2, 130


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
def _git(*args):
    """러너 폴더에서 git 명령을 실행해 출력을 돌려준다. git이 없거나 실패하면 None."""
    try:
        return subprocess.run(["git", "-C", str(paths.RUNNER_DIR), *args],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def library_version():
    """execution_library_version. 예: runner-0.1.0+abc1234

    runner/ 가 git 저장소면   runner-<버전>+<커밋 SHA 7자리>
    커밋 안 된 변경이 있으면   runner-<버전>+<SHA>.dirty  (기록은 하되 표시를 남긴다)
    git 저장소가 아니면        runner-<버전>+src<소스 해시 7자리>  (대체 수단)

    본평가는 .dirty가 붙지 않은 상태에서 돌려야 실행 코드를 커밋으로 되짚을 수 있다.
    """
    # 상위 폴더의 다른 저장소를 잡지 않도록, 저장소 최상위가 runner/ 자신인지 확인한다.
    top = _git("rev-parse", "--show-toplevel")
    sha = _git("rev-parse", "--short=7", "HEAD")
    if top and sha and Path(top).resolve() == paths.RUNNER_DIR:
        dirty = ".dirty" if _git("status", "--porcelain") else ""
        return f"runner-{__version__}+{sha}{dirty}"
    digest = hashlib.sha256()
    for source in sorted((paths.RUNNER_DIR / "kyab_runner").rglob("*.py")):
        digest.update(source.read_bytes())
    return f"runner-{__version__}+src{digest.hexdigest()[:7]}"


def load_inputs(codebook, input_dir):
    """입력 3종을 읽는다. 반환: ({표 이름: 행 목록}, {파일명: sha256})."""
    tables, digests = {}, {}
    for table, filename in INPUT_FILES.items():
        path = input_dir / filename
        tables[table] = csv_io.read_table(codebook, table, path)
        digests[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return tables, digests


def report_issues(issues):
    for issue in issues:
        print(issue)
    errors = validate.errors_of(issues)
    print(f"입력 검증: 오류 {len(errors)}건, 경고 {len(issues) - len(errors)}건")
    return errors


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


def open_batch(args, codebook, config, dataset_version, input_digests):
    """배치 폴더를 새로 만들거나(--batch-id 없음) 기존 배치를 이어 연다."""
    model = config.models.get(args.model)
    if model is None or not model.enabled:
        sys.exit(f"모델 '{args.model}'은 등록되지 않았거나 enabled: false 입니다 (config/models.yaml)")

    today = datetime.now(ZoneInfo(config["timezone"])).strftime("%Y%m%d")
    ids = IdAllocator(codebook, args.out, today)
    run_batch_id = args.batch_id or ids.new_batch_id()
    batch_dir = args.out / run_batch_id
    if args.batch_id and not batch_dir.exists():
        sys.exit(f"이어 쓸 배치 폴더가 없습니다: {batch_dir}")
    batch_dir.mkdir(parents=True, exist_ok=True)

    batch = Batch(codebook=codebook, config=config, model=model,
                  adapter=create_adapter(model, load_mock_plan(args)), ids=ids,
                  batch_dir=batch_dir, run_batch_id=run_batch_id, dataset_version=dataset_version,
                  protocol_id=args.protocol, library_version=library_version())
    _write_or_check_manifest(batch, input_digests, codebook)
    return batch


def _write_or_check_manifest(batch, input_digests, codebook):
    """배치의 고정 조건을 manifest에 남기고, 재시작이면 처음과 같은지 확인한다."""
    current = {
        "run_batch_id": batch.run_batch_id,
        "protocol_id": batch.protocol_id,
        "model_id": batch.model.model_id,
        "dataset_version": batch.dataset_version,
        "system_prompt_hash": batch.config.system_prompt_hash,
        "input_sha256": input_digests,
        "execution_library_version": batch.library_version,
        "codebook_overlays": codebook.applied_overlays,
        "adapter_info": batch.adapter.describe(),
        "created_at": batch.now(),
    }
    path = batch.dir / MANIFEST_FILE
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        changed = [k for k in _MANIFEST_LOCKED if saved[k] != current[k]]
        if changed:
            sys.exit(f"{batch.run_batch_id}에 이어 쓸 수 없습니다. 처음 실행과 다른 값: {changed}")
        batch.log_event("batch_resumed", execution_library_version=batch.library_version,
                        adapter_info=current["adapter_info"])
    else:
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        batch.log_event("batch_created", adapter_info=current["adapter_info"])


# ── 배치 진행 ───────────────────────────────────────────────────────────
def run_batch(batch, items, turns_by_item, rollouts, conduct):
    """선정 문항 × 반복 횟수만큼 실행한다.

    conduct(session, turns): 대화 1건을 진행하는 함수. 실행기마다 다르다.
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
    usage = Counter()
    for row in responses:                       # 토큰은 CSV에 칸이 없어 원본 응답에서 합산한다
        usage.update(json.loads(row["raw_response_json"]).get("usage", {}))
    if usage:
        print(f"  토큰 합계       {dict(usage)}")


# ── 진입점 공통부 ───────────────────────────────────────────────────────
def main(description, default_protocol, conversation_mode, conduct, argv=None):
    """실행기 공통 진입점.

    conversation_mode: 이 실행기가 맡는 대화 방식('single' | 'multi').
                       --protocol이 다른 방식의 프로토콜이면 실행하지 않는다.
    """
    args = build_parser(description, default_protocol).parse_args(argv)
    taxonomy = load_taxonomy()
    codebook = load_codebook(taxonomy)
    config = load_config()

    protocol = config["protocols"].get(args.protocol)
    if protocol is None or protocol["conversation_mode"] != conversation_mode:
        sys.exit(f"이 실행기는 conversation_mode={conversation_mode} 프로토콜만 실행합니다: {args.protocol}")

    try:
        tables, input_digests = load_inputs(codebook, args.input)
    except (csv_io.CsvFormatError, FileNotFoundError) as exc:
        print(f"입력을 읽을 수 없습니다: {exc}")
        return EXIT_INVALID_INPUT
    items, tags, prompts = tables["01_items"], tables["02_item_tags"], tables["03_prompts"]
    if report_issues(validate.validate_inputs(codebook, taxonomy, config, items, tags, prompts)):
        print("오류가 있어 실행하지 않습니다.")
        return EXIT_INVALID_INPUT
    if args.validate_only:
        return EXIT_OK

    only_ids = set(args.items.split(",")) if args.items else None
    selected, skipped = select_items(items, config, args.protocol, only_ids, args.allow_unverified)
    print(f"실행 대상 {len(selected)}문항" + (f", 제외 {dict(skipped)}" if skipped else ""))
    if not selected:
        return EXIT_NOTHING_TO_RUN
    versions = sorted({item["dataset_version"] for item in selected})
    if len(versions) != 1:
        print(f"한 배치에는 dataset_version이 하나여야 합니다: {versions}")
        return EXIT_INVALID_INPUT

    turns_by_item = defaultdict(list)
    for row in sorted(prompts, key=lambda r: int(r["turn_index"])):
        turns_by_item[validate.item_key(row)].append(row)

    batch = open_batch(args, codebook, config, versions[0], input_digests)
    rollouts = args.rollouts or config["default_rollouts"]
    try:
        new_rows = run_batch(batch, selected, turns_by_item, rollouts, conduct)
    except KeyboardInterrupt:
        print(f"\n중단됨. 이어서 실행: --batch-id {batch.run_batch_id}")
        return EXIT_INTERRUPTED
    judge_io.write_template(batch, items, tags)
    print_summary(batch, new_rows)
    return EXIT_OK
