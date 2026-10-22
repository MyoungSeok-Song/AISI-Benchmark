"""CLI: validate a canonical dataset, run one target model over it, write runs.csv / responses.csv.

    python -m kyab.run_benchmark --config config/pilot.yaml --model mock
    python -m kyab.run_benchmark --config config/pilot.yaml --validate-only

Run order is item × persona × rollout. With ``persona_dir`` in the config every item is run once per persona
(kyab.personas); without it persona_id is "none". ``temperature`` is a recorded setting (repeated sampling
needs > 0); top_p and max_output_tokens keep the codebook values.

Independent runs (item × persona × rollout) execute in parallel with ``--concurrency N`` threads (config
``concurrency``, default 1); the turns of one multi-turn run always stay sequential because each turn's
history contains the model's previous reply. Re-running the same command resumes: completed run keys are
skipped, unfinished ones are re-run only with ``--retry-failed`` (kyab.runner.schedule).

One invocation issues one run_batch_id per protocol_id (same dataset_version, protocol, system prompt,
generation settings and code version inside a batch). A batch manifest with every input hash and the
environment is written to <output_dir>/batches/<run_batch_id>.yaml.
"""
from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib import metadata
from pathlib import Path

import yaml
from dotenv import load_dotenv

from . import __version__
from .call_metrics import CALL_ATTEMPTS, CALL_METRICS
from .ids import IdRegistry
from .loader import load_dataset
from .models.base import GenerationConfig
from .models.registry import build_client, load_models
from .personas import NO_PERSONA, PersonaSet, apply_persona, load_personas, prompt_source, validate_personas
from .redact import redact_obj
from .runner.conversation import RetryPolicy, RunContext, now_iso, run_item
from .runner.schedule import SETTINGS, plan_runs, run_key
from .validator import sha256_text
from .schemas import RESPONSES, RUNS
from .storage import TableWriter, read_table
from .text_qa import InputEncodingError, check_korean_inputs
from .validator import missing_field_counts, summarize, validate_inputs, validate_outputs

PROJECT = Path(__file__).resolve().parents[2]


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def code_version() -> str:
    """runner version + short hash of the source tree (the project is not a git repo)."""
    h = hashlib.sha256()
    for p in sorted((PROJECT / "src" / "kyab").rglob("*.py")):
        h.update(p.relative_to(PROJECT).as_posix().encode())
        h.update(p.read_bytes())
    return f"kyab-runner-{__version__}+src.{h.hexdigest()[:7]}"


def git_info() -> dict:
    """Commit / tag of the code that produced a batch (blank outside a git checkout)."""
    def run(*a):
        try:
            return subprocess.run(["git", "-C", str(PROJECT), *a], capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"commit": run("rev-parse", "HEAD"), "describe": run("describe", "--tags", "--always"),
            # code paths only: data/output (id_registry.json, results) changes on every run by design
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no", "--", "src", "config", "scripts"))}


def _metrics_summary(rows: list[dict]) -> dict:
    def mean(key):
        vals = [int(r[key]) for r in rows if r.get(key) not in ("", None)]
        return round(sum(vals) / len(vals), 1) if vals else None
    status: dict[str, int] = defaultdict(int)
    for r in rows:
        status[r["response_status"]] += 1
    return {"n_calls": len(rows), "response_status_counts": dict(status), "mean_latency_ms": mean("latency_ms"),
            "mean_input_tokens": mean("input_tokens"), "mean_output_tokens": mean("output_tokens"),
            "finish_reason_counts": {k: sum(1 for r in rows if r["finish_reason"] == k)
                                     for k in sorted({r["finish_reason"] for r in rows})}}


def _pkg(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not-installed"


def _resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else PROJECT / p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--model", help="config/models.yaml의 model_id")
    ap.add_argument("--models-yaml", default="config/models.yaml")
    ap.add_argument("--output-dir", help="config의 output_dir 대신 사용")
    ap.add_argument("--protocols", help="쉼표 구분, 예: ST1-1.0.0,MT3-1.0.0 (기본: 전체)")
    ap.add_argument("--items", help="쉼표 구분 item_id만 실행")
    ap.add_argument("--rollouts", type=int, help="config의 rollouts 대신 사용")
    ap.add_argument("--rollout-start", type=int, default=1,
                    help="첫 rollout_no (추가 반복 실행 시 이어 붙이기, 예: 4)")
    ap.add_argument("--personas", help="쉼표 구분 persona_id만 실행 (기본: personas.csv 전체)")
    ap.add_argument("--concurrency", type=int, help="동시에 실행할 run 수 (config의 concurrency 대신, 기본 1)")
    ap.add_argument("--retry-failed", action="store_true",
                    help="완료되지 않은 기존 run(failed·partial·중단된 running)을 새 run_id로 다시 실행")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args(argv)

    load_dotenv(PROJECT / ".env")
    cfg = yaml.safe_load(_resolve(args.config).read_text(encoding="utf-8"))
    try:
        ds = load_dataset(_resolve(cfg["dataset_dir"]))
    except InputEncodingError as e:
        print(f"[error] 입력 인코딩: {e}")
        return 2
    dataset_version = str(cfg["dataset_version"])
    if ds.manifest.get("dataset_version") and str(ds.manifest["dataset_version"]) != dataset_version:
        print(f"[error] manifest dataset_version {ds.manifest['dataset_version']} ≠ config {dataset_version}")
        return 2

    # ---------------------------------------------------------------- 1. validate inputs (no model call yet)
    issues = validate_inputs(ds.items, ds.prompts, ds.item_tags, dataset_version)
    # Korean-input QA (encoding, NFC, invisible/broken characters; language checks only when Korean is expected)
    expected_language = cfg.get("expected_language") or ds.manifest.get("expected_language")
    issues += check_korean_inputs(ds.root, ds.items, ds.prompts, ds.item_tags, expected_language)
    personas: PersonaSet | None = None
    fallback = str(cfg.get("persona_prompt_fallback", "none"))
    if cfg.get("persona_dir"):
        try:
            personas = load_personas(_resolve(cfg["persona_dir"]))
        except InputEncodingError as e:
            print(f"[error] 페르소나 입력 인코딩: {e}")
            return 2
        issues += validate_personas(personas, ds.executable_items(), fallback)
    allowed_review = set(cfg.get("allowed_item_review_status", ["verified"]))
    for it in ds.items:
        if (it.get("item_review_status") or "") not in allowed_review:
            print(f"[error] {it['item_id']}: item_review_status={it.get('item_review_status')!r} 는 실행 허용 목록 "
                  f"{sorted(allowed_review)} 밖")
            return 2
    counts = summarize(issues)
    print(f"입력 검증: items {len(ds.items)}, prompts {len(ds.prompts)}, "
          f"item_tags {len(ds.item_tags) if ds.item_tags is not None else '없음'}, "
          f"personas {len(personas.personas) if personas else '없음'} → {counts or '문제 없음'}")
    for i in issues:
        if i.level != "missing":
            print("  ", i)
    if counts.get("missing"):
        print("   필수 메타데이터 공란(필드별 문항 수):")
        for k, n in sorted(missing_field_counts(issues).items()):
            print(f"     {k}: {n}")
    if counts.get("error"):
        print("[중단] 구조 오류가 있어 모델을 호출하지 않음")
        return 2
    if counts.get("missing") and not cfg.get("allow_missing_metadata", False):
        print("[중단] 필수 메타데이터 공란. 개발용 파일럿이면 config에 allow_missing_metadata: true")
        return 2
    if args.validate_only:
        return 0
    if not args.model:
        ap.error("--model 이 필요함 (--validate-only가 아니면)")

    # ---------------------------------------------------------------- 2. set up the batch
    gen = GenerationConfig(float(cfg["temperature"]), float(cfg["top_p"]), int(cfg["max_output_tokens"]))
    fixed = {"top_p": (gen.top_p, 1.0), "max_output_tokens": (gen.max_output_tokens, 8192)}
    for k, (got, want) in fixed.items():
        if got != want:
            print(f"[warning] {k}={got} — 코드북 값 {want}과 다름 (max_output_tokens: 10/05 회신으로 1,024 → 8,192)")
    system_prompt = _resolve(cfg["system_prompt_file"]).read_text(encoding="utf-8").rstrip("\n")
    models_yaml = _resolve(args.models_yaml)
    client = build_client(args.model, models_yaml)  # fail fast (TBD version, missing key) before any thread starts
    concurrency = args.concurrency or int(cfg.get("concurrency", 1))
    if concurrency < 1:
        ap.error("--concurrency 는 1 이상")
    local = threading.local()

    def worker_client():
        """One client per worker thread: clients keep per-call state (mock fault targeting, HTTP pool)."""
        if not hasattr(local, "client"):
            local.client = build_client(args.model, models_yaml)
        return local.client
    out = _resolve(args.output_dir or cfg["output_dir"])
    # one registry for the whole project: run_id / response_id / run_batch_id must be globally unique
    # (codebook), not unique per output directory
    ids = IdRegistry(_resolve(cfg.get("id_registry", "data/output/id_registry.json")))
    runs_w, resp_w = TableWriter(out / "runs.csv", RUNS), TableWriter(out / "responses.csv", RESPONSES)
    metrics_w = TableWriter(out / "call_metrics.csv", CALL_METRICS)
    attempts_w = TableWriter(out / "call_attempts.csv", CALL_ATTEMPTS)
    retry = RetryPolicy(**{k: v for k, v in (cfg.get("retry") or {}).items()
                           if k in ("max_retries", "backoff_s", "max_backoff_s")})
    rollouts = args.rollouts or int(cfg.get("rollouts", 1))
    if args.rollout_start < 1:
        ap.error("--rollout-start 는 1 이상")
    rollout_nos = range(args.rollout_start, args.rollout_start + rollouts)
    if rollouts > 1 and gen.temperature == 0.0:
        print("[warning] temperature 0.0에서 반복 실행 — rollout 간 응답이 거의 같아 반복의 의미가 없음")
    persona_ids = personas.ids if personas else [NO_PERSONA]
    if args.personas:
        if not personas:
            ap.error("--personas 는 config에 persona_dir 이 있을 때만")
        unknown_p = set(args.personas.split(",")) - set(persona_ids)
        if unknown_p:
            print(f"[error] personas.csv에 없는 persona_id {sorted(unknown_p)}")
            return 2
        persona_ids = [p for p in persona_ids if p in set(args.personas.split(","))]
    variants = personas.variant_map() if personas else {}
    version = code_version()

    todo = ds.executable_items()
    if args.protocols:
        keep = set(args.protocols.split(","))
        todo = [i for i in todo if i.protocol_id in keep]
    item_ids = args.items.split(",") if args.items else cfg.get("item_ids")
    if item_ids:
        unknown = set(item_ids) - {i.item_id for i in todo}
        if unknown:
            print(f"[error] 데이터셋에 없는 item_id {sorted(unknown)}")
            return 2
        todo = [i for i in todo if i.item_id in set(item_ids)]
    by_protocol: dict[str, list] = defaultdict(list)
    for it in todo:
        by_protocol[it.protocol_id].append(it)

    # ---------------------------------------------------------------- 3. plan (resume / duplicates)
    jobs = [(it, pid, r) for it in todo for pid in persona_ids for r in rollout_nos]
    existing = read_table(out / "runs.csv", RUNS, allow_extra=False)
    info = client.info
    settings = {"model_version": info.model_version, "temperature": str(gen.temperature), "top_p": str(gen.top_p),
                "max_output_tokens": str(gen.max_output_tokens), "system_prompt_hash": sha256_text(system_prompt)}
    assert set(settings) == set(SETTINGS)
    plan = plan_runs(jobs, lambda j: run_key(args.model, dataset_version, j[0].item_id, j[0].item_version,
                                             j[0].protocol_id, j[1], j[2]), existing, settings, args.retry_failed)
    if plan.conflicts:
        job, rid, diff = plan.conflicts[0]
        print(f"[error] 같은 출력 폴더에 생성 조건이 다른 기존 run {len(plan.conflicts)}건 (예: {rid} "
              f"{job[0].item_id} {job[1]} r{job[2]}: {diff}). 다른 --output-dir를 쓸 것")
        return 2
    print(f"\n실행 계획: 전체 {len(jobs)} runs → 새로 실행 {len(plan.to_run)} (재실행 {len(plan.retried)}), "
          f"완료되어 건너뜀 {len(plan.done)}, 미완료라 건너뜀 {len(plan.unfinished)}, concurrency {concurrency}")
    if plan.unfinished:
        print(f"   미완료 run {len(plan.unfinished)}건은 --retry-failed 로 다시 실행 "
              f"(예: {[(rid, st) for _, rid, st in plan.unfinished[:3]]})")
    to_run = {id(j) for j in plan.to_run}

    # ---------------------------------------------------------------- 4. run
    new_run_ids: list[str] = []
    worker_failures: list[str] = []
    for protocol_id, items in sorted(by_protocol.items()):
        p_jobs = [j for j in jobs if j[0].protocol_id == protocol_id and id(j) in to_run]
        if not p_jobs:
            continue
        batch_id = ids.new_run_batch_id()
        ctx = RunContext(batch_id, dataset_version, system_prompt, gen, cfg.get("safety_profile", "service_default"),
                         cfg.get("tool_profile", "none"), version, retry)
        started = now_iso()
        print(f"\n{batch_id}  protocol={protocol_id}  model={args.model}  items={len(items)}  "
              f"personas={len(persona_ids)}  rollouts={list(rollout_nos)}  temperature={gen.temperature}")
        status_count: dict[str, int] = defaultdict(int)
        batch_runs = []
        print_lock = threading.Lock()

        def work(job, ctx=ctx):
            base_item, pid, r = job
            it = apply_persona(base_item, pid, variants) if personas else base_item
            return it, run_item(it, r, worker_client(), ctx, ids, runs_w, resp_w, metrics_w, attempts_w)

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="kyab-run") as ex:
            futures = {ex.submit(work, j): j for j in p_jobs}
            try:
                for f in as_completed(futures):
                    base_item, pid, r = futures[f]
                    try:
                        it, run = f.result()
                    except Exception as e:  # one broken run must not stop the others; it stays resumable
                        worker_failures.append(f"{base_item.item_id} {pid} r{r}: {e!r}")
                        status_count["worker_exception"] += 1
                        with print_lock:
                            print(f"  [worker error] {base_item.item_id} {pid} r{r}: {e!r}", flush=True)
                        continue
                    new_run_ids.append(run["run_id"])
                    batch_runs.append(run["run_id"])
                    status_count[run["run_status"]] += 1
                    with print_lock:
                        print(f"  {run['run_id']} {it.item_id} {pid} r{r}: {run['run_status']} "
                              f"({run['actual_turn_count']}/{it.planned_round_count} turns)", flush=True)
            except KeyboardInterrupt:
                ex.shutdown(wait=False, cancel_futures=True)
                print("[중단] 시작하지 않은 run은 취소, 진행 중인 run은 끝난 뒤 종료 (한 번 더 누르면 즉시 종료 → "
                      "그 run은 running으로 남음). 같은 명령으로 재개, 미완료 run은 --retry-failed", flush=True)
                raise
        wall_s = time.perf_counter() - t0
        batch_metrics = [m for m in read_table(out / "call_metrics.csv", CALL_METRICS, allow_extra=False)
                         if m["run_id"] in set(batch_runs)]
        persona_doc = None if not personas else {
            "persona_dir": str(personas.root), "persona_ids": persona_ids,
            "persona_status": sorted({p["persona_status"] for p in personas.personas}),
            "prompt_source": prompt_source(personas, items), "fallback": fallback,
            "files_sha256": {p.name: _sha256_file(p) for p in sorted(personas.root.glob("*.csv"))}}
        _write_batch_manifest(out, batch_id, protocol_id, args, cfg, ds, dataset_version, system_prompt, gen,
                              version, started, batch_runs, dict(status_count), _metrics_summary(batch_metrics),
                              retry, persona_doc, list(rollout_nos),
                              {"concurrency": concurrency, "wall_seconds": round(wall_s, 1),
                               "runs_per_minute": round(len(batch_runs) / wall_s * 60, 2) if wall_s else None,
                               "calls_per_minute": round(len(batch_metrics) / wall_s * 60, 2) if wall_s else None,
                               "retry_failed": args.retry_failed,
                               "retried_run_ids": [rid for j, rid, _ in plan.retried if id(j) in {id(x) for x in p_jobs}]})

    # ---------------------------------------------------------------- 4. validate outputs
    runs_rows = read_table(out / "runs.csv", RUNS, allow_extra=False)
    resp_rows = read_table(out / "responses.csv", RESPONSES, allow_extra=False)
    mine = set(new_run_ids)
    out_issues = validate_outputs(ds.items, ds.prompts, [r for r in runs_rows if r["run_id"] in mine],
                                  [r for r in resp_rows if r["run_id"] in mine])
    n_resp = sum(1 for r in resp_rows if r["run_id"] in mine)
    print(f"\n출력 검증: runs {len(mine)}, responses {n_resp} → {summarize(out_issues) or '문제 없음'}")
    for i in out_issues:
        print("  ", i)
    if worker_failures:
        print(f"[worker error] {len(worker_failures)} runs — 같은 명령에 --retry-failed 로 재개")
    print(f"출력: {out / 'runs.csv'}, {out / 'responses.csv'}")
    return 1 if out_issues or worker_failures else 0


def _write_batch_manifest(out: Path, batch_id, protocol_id, args, cfg, ds, dataset_version, system_prompt, gen,
                          version, started, run_ids, status_count, metrics_summary,
                          retry: RetryPolicy, persona_doc: dict | None, rollout_nos: list[int],
                          execution: dict) -> None:
    models = load_models(_resolve(args.models_yaml))
    entry = {k: v for k, v in models[args.model].items() if "key" not in k.lower()}
    doc = {
        "run_batch_id": batch_id, "protocol_id": protocol_id, "dataset_version": dataset_version,
        "dataset_purpose": ds.manifest.get("purpose"),
        "not_for_official_evaluation": ds.manifest.get("not_for_official_evaluation"),
        "started_at": started, "completed_at": now_iso(), "n_runs": len(run_ids),
        "run_status_counts": status_count, "technical_metrics": metrics_summary, "run_ids": run_ids,
        "run_meta": cfg.get("run_meta"),
        "code": {"execution_library_version": version, **git_info()},
        "model": entry,
        "generation": {"temperature": gen.temperature, "top_p": gen.top_p, "max_output_tokens": gen.max_output_tokens},
        "retry": retry.as_dict(),
        "rollout_nos": rollout_nos,
        "execution": execution,
        "personas": persona_doc,
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
        "config_file": args.config, "config": cfg,
        "inputs_sha256": {p.name: _sha256_file(p) for p in sorted(ds.root.glob("*")) if p.is_file()},
        "execution_library_version": version,
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                        "httpx": _pkg("httpx"), "pyyaml": _pkg("pyyaml"), "hostname": platform.node()},
        "command": " ".join(sys.argv),
    }
    path = out / "batches" / f"{batch_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(redact_obj(doc), allow_unicode=True, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
