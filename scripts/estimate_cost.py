"""API cost re-estimate from MEASURED token usage of a smoke test (lead-lab request 2026-10-22 ⑤).

For every model in the smoke outputs, the mean input / output tokens are taken per call position
(single-turn, and multi-turn turn 1..N — later turns carry the accumulated history), then scaled to the
full plan:

    calls = items × personas × rollouts (per position);  cost = input × in_price + output × out_price

OpenAI's completion_tokens already include reasoning tokens, so output × out_price covers GPT-5 mini's
reasoning; reasoning_tokens is shown separately for information. Prices: config/prices.yaml.

    .venv/bin/python scripts/estimate_cost.py --smoke data/output/smoke_* --single 240 --multi 240 \
        [--personas 6 --rollouts 3 --full-runs 3 --retry 0.2 --krw 1400] [--price-as local_qwen3_32b_fp8=gpt_6_luna]

--price-as prices a model's measured tokens at another model's rate (dry run before the API key arrives).
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(smoke_dirs):
    """{model_id: {(protocol_kind, turn_index): [(in, out, reasoning), …]}}"""
    data: dict = defaultdict(lambda: defaultdict(list))
    for d in smoke_dirs:
        d = Path(d)
        runs = {r["run_id"]: r for r in csv.DictReader(open(d / "runs.csv", encoding="utf-8"))}
        resp = {r["response_id"]: r for r in csv.DictReader(open(d / "responses.csv", encoding="utf-8"))}
        turn_no: dict[str, dict[str, int]] = defaultdict(dict)
        for r in resp.values():  # turn position inside its run = order of appearance (turns are sequential)
            turn_no[r["run_id"]].setdefault(r["turn_id"], len(turn_no[r["run_id"]]) + 1)
        for m in csv.DictReader(open(d / "call_metrics.csv", encoding="utf-8")):
            if m["response_status"] != "success" or not m["input_tokens"] or not m["output_tokens"]:
                continue
            run = runs[m["run_id"]]
            kind = "single" if run["protocol_id"].startswith("ST1") else "multi"
            data[m["model_id"]][(kind, turn_no[m["run_id"]][m["turn_id"]])].append(
                (int(m["input_tokens"]), int(m["output_tokens"]), int(m["reasoning_tokens"] or 0)))
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", nargs="+", required=True, help="smoke-test output dirs (runs/responses/call_metrics)")
    ap.add_argument("--single", type=int, required=True, help="single-turn items in the full plan")
    ap.add_argument("--multi", type=int, required=True, help="multi-turn items in the full plan")
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--personas", type=int, default=6)
    ap.add_argument("--rollouts", type=int, default=3)
    ap.add_argument("--full-runs", type=int, default=3, help="full executions until project end (pilot, main, re-run)")
    ap.add_argument("--retry", type=float, default=0.2, help="re-execution margin on each full run")
    ap.add_argument("--krw", type=float, default=1400.0)
    ap.add_argument("--prices", default=str(ROOT / "config" / "prices.yaml"))
    ap.add_argument("--price-as", action="append", default=[], help="model_id=price_key")
    args = ap.parse_args(argv)
    prices = yaml.safe_load(Path(args.prices).read_text(encoding="utf-8"))["models"]
    alias = dict(x.split("=", 1) for x in args.price_as)
    data = load(args.smoke)
    if not data:
        print("[error] 성공한 호출의 토큰 기록이 없음")
        return 2

    rows, total_once = [], 0.0
    reps = args.personas * args.rollouts
    for model, by_pos in sorted(data.items()):
        key = alias.get(model, model)
        if key not in prices:
            print(f"[warning] {model}: config/prices.yaml에 단가 없음 → 제외 (공개 모델이면 API 비용 0)")
            continue
        pin, pout = prices[key]["input_per_1m"], prices[key]["output_per_1m"]
        plan = [("single", 1, args.single)] + [("multi", t, args.multi) for t in range(1, args.turns + 1)]
        tin = tout = treason = 0.0
        missing = []
        for kind, t, n_items in plan:
            obs = by_pos.get((kind, t))
            if not obs:
                if n_items:
                    missing.append(f"{kind} t{t}")
                continue
            calls = n_items * reps
            tin += calls * statistics.mean(o[0] for o in obs)
            tout += calls * statistics.mean(o[1] for o in obs)
            treason += calls * statistics.mean(o[2] for o in obs)
        once = (tin * pin + tout * pout) / 1e6
        total_once += once
        n_obs = sum(len(v) for v in by_pos.values())
        rows.append((model, key, n_obs, tin, tout, treason, once, missing))

    calls = (args.single + args.multi * args.turns) * reps
    print(f"계획: 단일턴 {args.single} + {args.turns}턴 {args.multi}문항 × 페르소나 {args.personas} × 반복 {args.rollouts} = "
          f"{(args.single + args.multi) * reps:,} runs / 모델당 {calls:,} calls\n")
    print("| 모델 (단가 기준) | 실측 호출 | 입력 토큰 | 출력 토큰 (추론 포함) | 그중 추론 | 본 평가 1회 |")
    print("|---|---|---|---|---|---|")
    for model, key, n, tin, tout, tr, once, missing in rows:
        label = model if key == model else f"{model} ({key} 단가)"
        print(f"| {label} | {n} | {tin / 1e6:.2f}M | {tout / 1e6:.2f}M | {tr / 1e6:.2f}M | ${once:,.1f} |"
              + (f" 실측 없음: {missing}" if missing else ""))
    project = total_once * (1 + args.retry) * args.full_runs
    won = lambda usd: f"약 {usd * args.krw:,.0f}원"  # noqa: E731
    print(f"\n본 평가 1회 합계: ${total_once:,.1f} ({won(total_once)})")
    print(f"과제 종료까지 (전체 실행 {args.full_runs}회 × 재시도 여유 +{args.retry:.0%}): ${project:,.1f} "
          f"({won(project)}) — Judge 비용 제외")
    return 0


if __name__ == "__main__":
    sys.exit(main())
