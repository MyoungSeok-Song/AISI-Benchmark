"""MinorBench test.csv (PromptID, Category, Prompt) → canonical rows.

Source: govtech/MinorBench (MIT), 299 single-turn English prompts. MinorBench pairs prompts with its
own four system prompts; those are NOT carried over — the benchmark protocol fixes one system prompt
for every item (plan 표 Ⅳ-20).
"""
from __future__ import annotations

import csv
from pathlib import Path

from .common import Converted, IdAllocator, item_row, prompt_rows, unreviewed_tag_row


def read_source(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def select_one_per_category(rows: list[dict], n: int) -> list[dict]:
    """Deterministic pilot sample: first n categories (sorted), lowest PromptID within each."""
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["Category"], []).append(r)
    cats = sorted(by_cat)[:n]
    return [min(by_cat[c], key=lambda r: int(r["PromptID"])) for c in cats]


def convert(rows: list[dict], ids: IdAllocator, *, dataset_version: str, item_version: str, rubric_id: str,
            taxonomy_version: str, built_at: str) -> Converted:
    out = Converted()
    for r in rows:
        item_id = ids.item_id()
        out.items.append(item_row(
            item_id=item_id, item_version=item_version, dataset_version=dataset_version,
            scenario_type="not_specified", persona_text="not_specified", user_gender="unspecified",
            conversation_mode="single", planned_round_count=1, protocol_id="ST1-1.0.0", rubric_id=rubric_id,
            source_benchmark="MinorBench", source_item_id=r["PromptID"], source_category=r["Category"],
            source_language="en", original_text=r["Prompt"], source_license="MIT",
            item_review_status="pending",
        ))
        out.item_tags.append(unreviewed_tag_row(item_id, item_version, taxonomy_version, built_at))
        out.prompts += prompt_rows(ids, item_id, item_version, [r["Prompt"]])
        out.notes.append(f"{item_id} ← MinorBench {r['PromptID']} ({r['Category']})")
    return out
