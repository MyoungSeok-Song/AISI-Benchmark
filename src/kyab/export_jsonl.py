"""JSONL delivery format: items and results as separate files, linked by keys.

Delivery requirements (2026-10-05):
- JSONL, nested structures (messages) allowed.
- All source items in ONE file; single-turn and multi-turn share the same structure.
- Results (many per item: models × rollouts) in separate files, linked by item_id.
- Provenance traceable from the final item back to the original source.

Files
- items.jsonl      one line per item (01_items fields + current 02_item_tags row as "tags" + 03_prompts turns as
                   "messages"). Evaluation units: messages, expected/prohibited responses, rubric, metadata.
- responses.jsonl  one line per run (04_runs fields) with that run's 05_responses rows nested in "turns"
                   (request_messages / raw_response parsed back to JSON, plus call metrics when available).
                   Links: item_id + item_version → items.jsonl, turns[].turn_id → items.jsonl messages.
Codebook column names are kept unchanged; CSV strings are typed (ints, floats, JSON arrays/objects, null).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .call_metrics import CALL_METRICS
from .schemas import ITEM_TAGS, ITEMS, PROMPTS, RESPONSES, RUNS, Table
from .storage import read_table


def typed(table: Table, row: dict, drop: tuple[str, ...] = ()) -> dict:
    """CSV row → JSON-typed dict in codebook column order. Blank → null; unknown text stays text."""
    out = {}
    for f in table.fields:
        if f.name in drop:
            continue
        v = row.get(f.name, "")
        if v is None or v.strip() == "":
            out[f.name] = None
            continue
        try:
            if f.kind == "int":
                out[f.name] = int(v)
            elif f.kind == "float":
                out[f.name] = float(v)
            elif f.kind == "bool":
                out[f.name] = v.strip().lower() == "true"
            elif f.kind in ("json_array", "json_object"):
                out[f.name] = json.loads(v)
            else:
                out[f.name] = v
        except (ValueError, json.JSONDecodeError):
            out[f.name] = v  # never lose data: keep the original string if it does not parse
    return out


def build_items(dataset_dir: Path, sources: dict | None = None) -> list[dict]:
    items = read_table(dataset_dir / "items.csv", ITEMS)
    prompts = read_table(dataset_dir / "prompts.csv", PROMPTS)
    tags_path = dataset_dir / "item_tags.csv"
    tags = read_table(tags_path, ITEM_TAGS) if tags_path.exists() else []
    out = []
    for it in items:
        key = (it["item_id"], it["item_version"])
        turns = sorted((p for p in prompts if (p["item_id"], p["item_version"]) == key),
                       key=lambda p: int(p["turn_index"]))
        current = [t for t in tags if (t["item_id"], t["item_version"]) == key and t["tag_status"] == "current"]
        rec = typed(ITEMS, it)
        rec["messages"] = [{"role": "user", **typed(PROMPTS, p, drop=("item_id", "item_version"))} for p in turns]
        rec["tags"] = typed(ITEM_TAGS, current[0], drop=("item_id", "item_version")) if current else None
        if sources and it.get("source_benchmark") in sources:
            rec["provenance"] = sources[it["source_benchmark"]]
        out.append(rec)
    return out


def build_responses(output_dir: Path) -> list[dict]:
    runs = read_table(output_dir / "runs.csv", RUNS)
    resps = read_table(output_dir / "responses.csv", RESPONSES)
    mpath = output_dir / "call_metrics.csv"
    metrics = {m["response_id"]: m for m in read_table(mpath, CALL_METRICS)} if mpath.exists() else {}
    by_run: dict[str, list[dict]] = {}
    for r in resps:
        t = typed(RESPONSES, r, drop=("run_id",))
        m = metrics.get(r["response_id"])
        t["call_metrics"] = typed(CALL_METRICS, m, drop=("response_id", "run_id", "turn_id", "model_id")) if m else None
        by_run.setdefault(r["run_id"], []).append(t)
    out = []
    for run in runs:
        rec = typed(RUNS, run)
        rec["turns"] = by_run.get(run["run_id"], [])
        out.append(rec)
    return out


def write_jsonl(path: Path, records: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
