"""Helpers shared by source adapters (source file → canonical items / item_tags / prompts rows).

Adapter rule (codebook 00 원칙 "원천값 보존"): copy only what the source actually states. A field the
source does not provide stays blank and is reported as missing — never inferred (e.g. age group,
R1~R5 risk, severity). Values the codebook itself prescribes for "no information" are used as such:
scenario_type / persona_text = not_specified, user_gender = unspecified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count

from ..schemas import ITEM_TAGS, ITEMS, PROMPTS


@dataclass
class IdAllocator:
    """Sequential canonical IDs for one dataset build. Pilot ranges are provisional (agree with dataset team)."""
    next_item: int
    next_turn: int
    _items: count = field(init=False)
    _turns: count = field(init=False)

    def __post_init__(self):
        self._items, self._turns = count(self.next_item), count(self.next_turn)

    def item_id(self) -> str:
        return f"KYAB-{next(self._items):06d}"

    def turn_id(self) -> str:
        return f"TURN-{next(self._turns):08d}"


@dataclass
class Converted:
    items: list[dict] = field(default_factory=list)
    item_tags: list[dict] = field(default_factory=list)
    prompts: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def extend(self, other: "Converted") -> None:
        self.items += other.items
        self.item_tags += other.item_tags
        self.prompts += other.prompts
        self.notes += other.notes


def blank_row(table) -> dict:
    return {c: "" for c in table.columns}


def item_row(**values) -> dict:
    row = blank_row(ITEMS)
    unknown = set(values) - set(row)
    if unknown:
        raise KeyError(f"items에 없는 필드 {unknown}")
    row.update({k: "" if v is None else str(v) for k, v in values.items()})
    return row


def prompt_rows(ids: IdAllocator, item_id: str, item_version: str, messages: list[str],
                context_texts: list[str] | None = None) -> list[dict]:
    rows = []
    for i, msg in enumerate(messages, start=1):
        row = blank_row(PROMPTS)
        row.update(turn_id=ids.turn_id(), item_id=item_id, item_version=item_version, turn_index=str(i),
                   context_text=(context_texts[i - 1] if context_texts else "") or "", message_text=msg)
        rows.append(row)
    return rows


def unreviewed_tag_row(item_id: str, item_version: str, taxonomy_version: str, annotated_at: str) -> dict:
    """tag_revision 1 in which every review status says 'not reviewed yet' and no tag value is guessed."""
    row = blank_row(ITEM_TAGS)
    row.update(item_id=item_id, item_version=item_version, tag_revision="1", tag_status="current",
               taxonomy_version=taxonomy_version, risk_review_status="unreviewed", m_review_status="unreviewed",
               companion_review_status="unreviewed", role_review_status="unreviewed",
               impersonation_review_status="unreviewed", review_status="draft", annotated_at=annotated_at)
    return row
