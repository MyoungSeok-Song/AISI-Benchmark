"""Export a JSONL release: items.jsonl + responses.jsonl (+ provisional_scores.jsonl) + manifest.json.

    .venv/bin/python scripts/export_release.py \
        --dataset data/pilot_0.0.2 --outputs data/output/live_pilot_0.0.2 --release data/release/pilot_0.0.2 \
        --scores data/output/live_pilot_0.0.2/pilot_review_scored_by_claude.csv

provisional_scores.jsonl holds Claude's provisional RB-6D scores for human review. They are deliberately NOT
written as codebook 06_judgments rows (no judgment_id, rubric_version or human review state exists yet).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kyab.export_jsonl import build_items, build_responses, write_jsonl  # noqa: E402
from kyab.run_benchmark import git_info  # noqa: E402

RAW_SOURCES = {"CAREBench": "data/raw/carebench/SOURCE.yaml", "MinorBench": "data/raw/minorbench/SOURCE.yaml"}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def provenance(dataset_manifest: dict) -> dict:
    """Per-source trail: final item → pilot build (selection rule, file hash) → raw snapshot → HF repo@commit."""
    out = {}
    for name, rel in RAW_SOURCES.items():
        src = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        build = (dataset_manifest.get("sources") or {}).get(name, {})
        out[name] = {"hf_repo": src.get("hf_repo"), "hf_commit": src.get("hf_commit"), "license": src.get("license"),
                     "raw_snapshot": rel.rsplit("/", 1)[0], "raw_files_sha256": src.get("files"),
                     "selection_rule": build.get("selection"), "id_field_in_source": "source_item_id"}
    dev = (dataset_manifest.get("sources") or {}).get("dev_multiturn")
    if dev:
        out["NEW"] = {"origin": "developer-written test script (not a research item)", "file": dev.get("file"),
                      "sha256": dev.get("sha256")}
    return out


def provisional_scores(scores_csv: Path, responses: list[dict]) -> list[dict]:
    model_of = {}
    for run in responses:
        for t in run["turns"]:
            model_of[t["response_id"]] = (run["model_id"], run["run_id"], run["item_id"])
    with open(scores_csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        for short in ("qwen3_8b", "qwen3_32b"):
            rid = r.get(f"{short}__response_id")
            if not rid or rid not in model_of:
                continue
            model_id, run_id, item_id = model_of[rid]
            dims = {d: (None if r[f"{short}__{d}"] in ("", "NA") else int(r[f"{short}__{d}"]))
                    for d in ("D1", "D2", "D3", "D4", "D5", "D6")}
            out.append({
                "response_id": rid, "run_id": run_id, "item_id": item_id, "turn_id": r["turn_id"],
                "model_id": model_id, "sample_group": r["표본구분"],
                "scorer": "claude-opus-5-5", "scorer_type": "llm_provisional_for_human_review",
                "rubric_id": "RB-6D-v1", **dims,
                "applicable_max": int(r[f"{short}__적용만점"]), "total": int(r[f"{short}__합계"]),
                "cfc_candidate": r[f"{short}__CFC(Y/N)"] == "Y", "cfc_note": r[f"{short}__CFC내용"] or None,
                "over_refusal": {"Y": True, "N": False}.get(r[f"{short}__과잉거절(Y/N/NA)"]),
                "rationale": r[f"{short}__점수근거"],
                "conventions": "NA=null (D3 no harmful request, D2 no AI-relationship context, D6 single-turn); "
                               "D5 capped at 1 (Korean resources not elicitable in English pilot)",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--outputs", required=True)
    ap.add_argument("--release", required=True)
    ap.add_argument("--scores")
    a = ap.parse_args()
    ds, outs, rel = ROOT / a.dataset, ROOT / a.outputs, ROOT / a.release
    dman = yaml.safe_load((ds / "manifest.yaml").read_text(encoding="utf-8"))

    items = build_items(ds, provenance(dman))
    responses = build_responses(outs)
    files = {"items.jsonl": (write_jsonl(rel / "items.jsonl", items), len(items)),
             "responses.jsonl": (write_jsonl(rel / "responses.jsonl", responses), len(responses))}
    if a.scores:
        sc = provisional_scores(ROOT / a.scores, responses)
        files["provisional_scores.jsonl"] = (write_jsonl(rel / "provisional_scores.jsonl", sc), len(sc))

    # integrity: every response links to an item and to one of its turns
    turn_ids = {(i["item_id"], i["item_version"]): {m["turn_id"] for m in i["messages"]} for i in items}
    broken = [(r["run_id"], t["turn_id"]) for r in responses for t in r["turns"]
              if t["turn_id"] not in turn_ids.get((r["item_id"], r["item_version"]), set())]
    if broken:
        print(f"[error] responses not linked to items: {broken[:5]}")
        return 1

    manifest = {
        "release": rel.name, "dataset_version": dman.get("dataset_version"), "purpose": dman.get("purpose"),
        "not_for_official_evaluation": dman.get("not_for_official_evaluation"),
        "schema": "ETRI 7-CSV codebook v0.2 + agreed v0.3 changes; codebook column names kept in JSON",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "code": git_info(),
        "files": {k: {"records": n, "sha256": h} for k, (h, n) in files.items()},
        "record_units": {
            "items.jsonl": "1 line = 1 item (01_items) + tags (current 02_item_tags row) + messages (03_prompts turns) "
                           "+ provenance. Single- and multi-turn share the same structure.",
            "responses.jsonl": "1 line = 1 run (04_runs: model × item × rollout) + turns (05_responses + call metrics)",
            "provisional_scores.jsonl": "1 line = 1 response scored provisionally by Claude for human review "
                                        "(NOT codebook 06_judgments)",
        },
        "links": {"responses.item_id+item_version": "items.item_id+item_version",
                  "responses.turns[].turn_id": "items.messages[].turn_id",
                  "provisional_scores.response_id": "responses.turns[].response_id"},
        "source_outputs": a.outputs,
        "models": sorted({r["model_id"] for r in responses}),
        "notes": ["Exploratory live pilot — final_benchmark_result: false",
                  "Provisional system prompt — not final benchmark protocol",
                  "Pre-A1~A10 migration taxonomy; English source text"],
    }
    (rel / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, (h, n) in files.items():
        print(f"{rel / k}: {n} records")
    print(f"{rel / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
