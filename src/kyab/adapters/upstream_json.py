"""Multi-turn script JSON → canonical rows.

Used now for the developer-written MT3 test scripts (data/raw/dev_multiturn/*.json), and intended for
the dataset team's future multi-turn deliveries once their format is fixed. Expected shape:

    {"items": [{"item_id": "KYAB-900011", "item_version": "0.1.0", "protocol_id": "MT3-1.0.0",
                "items_fields": {...optional 01_items values...},
                "turns": [{"turn_index": 1, "message_text": "...", "context_text": ""}, ...]}]}

Items fields not given stay blank (reported as missing). turn_id is issued here.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..protocol import PROTOCOLS
from .common import Converted, IdAllocator, item_row, prompt_rows, unreviewed_tag_row


def convert(path: str | Path, ids: IdAllocator, *, dataset_version: str, taxonomy_version: str,
            built_at: str) -> Converted:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    out = Converted()
    for it in doc["items"]:
        proto = PROTOCOLS[it["protocol_id"]]
        turns = sorted(it["turns"], key=lambda t: int(t["turn_index"]))
        item_id = it.get("item_id") or ids.item_id()
        fields = dict(it.get("items_fields", {}))
        out.items.append(item_row(
            item_id=item_id, item_version=it["item_version"], dataset_version=dataset_version,
            conversation_mode=proto.mode, planned_round_count=len(turns), protocol_id=proto.protocol_id, **fields))
        out.item_tags.append(unreviewed_tag_row(item_id, it["item_version"], taxonomy_version, built_at))
        out.prompts += prompt_rows(ids, item_id, it["item_version"], [t["message_text"] for t in turns],
                                   [t.get("context_text", "") for t in turns])
        out.notes.append(f"{item_id} ← {Path(path).name} ({len(turns)} turns, {proto.protocol_id})")
    return out
