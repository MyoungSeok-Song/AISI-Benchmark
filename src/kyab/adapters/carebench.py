"""CAREBench prompts.csv (case_uid, risk_category, risk_mechanism, prompt) → canonical rows.

Source: handshake-ai-research/CAREBench (CC-BY-4.0), 500 single-turn English prompts.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .common import Converted, IdAllocator, item_row, prompt_rows, unreviewed_tag_row


def read_source(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def select_one_per_category(rows: list[dict], n: int) -> list[dict]:
    """Deterministic pilot sample: first n categories (sorted), lowest case_uid within each."""
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["risk_category"], []).append(r)
    cats = sorted(by_cat)[:n]
    return [min(by_cat[c], key=lambda r: r["case_uid"]) for c in cats]


def convert(rows: list[dict], ids: IdAllocator, *, dataset_version: str, item_version: str, rubric_id: str,
            taxonomy_version: str, built_at: str) -> Converted:
    out = Converted()
    for r in rows:
        item_id = ids.item_id()
        out.items.append(item_row(
            item_id=item_id, item_version=item_version, dataset_version=dataset_version,
            scenario_type="not_specified", persona_text="not_specified", user_gender="unspecified",
            conversation_mode="single", planned_round_count=1, protocol_id="ST1-1.0.0", rubric_id=rubric_id,
            source_benchmark="CAREBench", source_item_id=r["case_uid"],
            source_category=r["risk_category"], source_mechanism=r["risk_mechanism"],
            source_language="en", original_text=r["prompt"], source_license="CC-BY-4.0",
            item_review_status="pending",
            # not in the source → left blank, reported as missing: target_age_group, risk_cues_json,
            # prohibited_response_json, localization_type (no codebook value for "untranslated original")
        ))
        out.item_tags.append(unreviewed_tag_row(item_id, item_version, taxonomy_version, built_at))
        out.prompts += prompt_rows(ids, item_id, item_version, [r["prompt"]])
        out.notes.append(f"{item_id} ← CAREBench {r['case_uid']} ({r['risk_category']} / {r['risk_mechanism']})")
    return out
