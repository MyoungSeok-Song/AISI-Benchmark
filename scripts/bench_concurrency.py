"""Concurrency sweep for the parallel runner on a small subset (one fresh output dir per level).

    PYTHONPATH=src .venv/bin/python scripts/bench_concurrency.py --config config/pilot_persona_qwen.yaml \
        --model local_qwen3_32b_fp8 --levels 1,2,4,8 --items KYAB-900001,KYAB-900002,KYAB-900011,KYAB-900012 \
        --gpu 1 --out data/output/concurrency_bench

Per level: throughput (runs/min, calls/min), latency (mean, p95), timeout / error / length-cut rates,
and GPU utilization / memory sampled every second with nvidia-smi (the GPU is shared, so the baseline
from other jobs is included — compare levels, not absolute values). Writes summary.csv and summary.md.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kyab.call_metrics import CALL_METRICS  # noqa: E402
from kyab.run_benchmark import main as run_main  # noqa: E402
from kyab.schemas import RUNS  # noqa: E402
from kyab.storage import read_table  # noqa: E402


class GpuSampler(threading.Thread):
    def __init__(self, gpu: str):
        super().__init__(daemon=True)
        self.gpu, self.util, self.mem, self._halt = gpu, [], [], threading.Event()

    def run(self):
        while not self._halt.is_set():
            try:
                out = subprocess.run(["nvidia-smi", "-i", self.gpu, "--query-gpu=utilization.gpu,memory.used",
                                      "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                     timeout=5).stdout.strip().split(",")
                self.util.append(float(out[0]))
                self.mem.append(float(out[1]))
            except (OSError, subprocess.SubprocessError, ValueError, IndexError):
                pass
            self._halt.wait(1.0)

    def stop(self):
        self._halt.set()
        self.join()


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--items", required=True)
    ap.add_argument("--rollouts", type=int, default=1)
    ap.add_argument("--gpu", default="1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = (PROJECT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    cfg = yaml.safe_load((PROJECT / args.config).read_text(encoding="utf-8"))
    rows = []
    for level in [int(x) for x in args.levels.split(",")]:
        d = out / f"c{level}"
        if (d / "runs.csv").exists():
            print(f"[error] {d} already has runs — use a fresh --out")
            return 2
        c = dict(cfg, output_dir=str(d))
        c.setdefault("run_meta", {})
        c["run_meta"] = dict(c["run_meta"] or {}, run_purpose="concurrency_benchmark", concurrency=level)
        cfg_path = d / "config.yaml"
        d.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
        gs = GpuSampler(args.gpu)
        gs.start()
        t0 = time.perf_counter()
        rc = run_main(["--config", str(cfg_path), "--model", args.model, "--items", args.items,
                       "--rollouts", str(args.rollouts), "--concurrency", str(level)])
        wall = time.perf_counter() - t0
        gs.stop()
        runs = read_table(d / "runs.csv", RUNS, allow_extra=False)
        m = read_table(d / "call_metrics.csv", CALL_METRICS, allow_extra=False)
        lat = [int(x["latency_ms"]) / 1000 for x in m]
        n = len(m)
        rows.append({
            "concurrency": level, "exit_code": rc, "runs": len(runs), "calls": n, "wall_s": round(wall, 1),
            "runs_per_min": round(len(runs) / wall * 60, 2), "calls_per_min": round(n / wall * 60, 2),
            "speedup_vs_first": None,
            "latency_mean_s": round(statistics.mean(lat), 2) if lat else None,
            "latency_p95_s": round(p95(lat), 2) if lat else None,
            "timeout_rate": round(sum(x["response_status"] == "timeout" for x in m) / n, 4) if n else None,
            "error_rate": round(sum(x["response_status"] not in ("success", "timeout") for x in m) / n, 4) if n else None,
            "length_cut_rate": round(sum(x["finish_reason"] == "length" for x in m) / n, 4) if n else None,
            "runs_not_completed": sum(r["run_status"] != "completed" for r in runs),
            "gpu_util_mean": round(statistics.mean(gs.util), 1) if gs.util else None,
            "gpu_mem_max_mib": max(gs.mem) if gs.mem else None,
            "mean_output_tokens": round(statistics.mean(int(x["output_tokens"]) for x in m if x["output_tokens"]), 1)
            if any(x["output_tokens"] for x in m) else None,
        })
        rows[-1]["speedup_vs_first"] = round(rows[-1]["calls_per_min"] / rows[0]["calls_per_min"], 2)
        print(f"== concurrency {level}: {rows[-1]}", flush=True)
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cols = list(rows[0])
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join("" if r[k] is None else str(r[k]) for k in cols) + " |" for r in rows]
    (out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
