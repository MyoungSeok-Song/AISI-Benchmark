"""Load a canonical dataset directory (items.csv, prompts.csv, item_tags.csv, manifest.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .schemas import ITEM_TAGS, ITEMS, PROMPTS
from .storage import read_table
from .text_qa import check_file_encoding


@dataclass(frozen=True)
class ScriptTurn:
    """The only prompt fields the runner can see. Tags, rubric and expected responses are not here."""
    turn_id: str
    turn_index: int
    context_text: str
    message_text: str


@dataclass(frozen=True)
class ExecutableItem:
    item_id: str
    item_version: str
    dataset_version: str
    protocol_id: str
    planned_round_count: int
    turns: tuple[ScriptTurn, ...]
    persona_id: str = "none"  # set by personas.apply_persona; "none" = no persona axis


@dataclass
class Dataset:
    root: Path
    manifest: dict
    items: list[dict]
    prompts: list[dict]
    item_tags: list[dict] | None

    def executable_items(self) -> list[ExecutableItem]:
        by_item: dict[tuple, list[ScriptTurn]] = {}
        for p in self.prompts:
            by_item.setdefault((p["item_id"], p["item_version"]), []).append(
                ScriptTurn(p["turn_id"], int(p["turn_index"]), p.get("context_text", ""), p["message_text"]))
        out = []
        for it in self.items:
            turns = sorted(by_item.get((it["item_id"], it["item_version"]), []), key=lambda t: t.turn_index)
            out.append(ExecutableItem(it["item_id"], it["item_version"], it["dataset_version"], it["protocol_id"],
                                      int(it["planned_round_count"]), tuple(turns)))
        return out


def load_dataset(root: str | Path) -> Dataset:
    root = Path(root)
    manifest_path = root / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    tags_path = root / "item_tags.csv"
    for p in (root / "items.csv", root / "prompts.csv", tags_path):
        if p.exists():
            check_file_encoding(p)  # raises InputEncodingError with a readable message instead of a decode crash
    return Dataset(
        root=root,
        manifest=manifest or {},
        items=read_table(root / "items.csv", ITEMS),
        prompts=read_table(root / "prompts.csv", PROMPTS),
        item_tags=read_table(tags_path, ITEM_TAGS) if tags_path.exists() else None,
    )
