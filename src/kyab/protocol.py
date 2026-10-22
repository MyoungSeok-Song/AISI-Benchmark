"""Evaluation protocols (codebook protocol_id vocabulary, plan 표 Ⅳ-20).

A protocol fixes how many user turns a conversation has and how a turn's user message is rendered
from prompts.context_text + prompts.message_text. The runner never hard-codes a turn count: it walks
the item's prompts in turn_index order, and the validator checks that count against the protocol.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Protocol:
    protocol_id: str
    mode: str  # single | multi
    turns: int
    # How context_text is combined with message_text. PROVISIONAL: the template itself can change model
    # behaviour, so it must be agreed with the dataset team and frozen per protocol version.
    context_template: str = "{context_text}\n\n{message_text}"
    # CFC-based immediate stop (plan: "3턴 완료 시 또는 CFC 발생 시 즉시 종료") needs an online judge;
    # not implemented in this phase — runs stop only on technical failure.
    stop_on_critical_failure: bool = False
    # Codebook v0.2 ids (ST1-v1 …) were replaced by SemVer ids (ST1-1.0.0 …) on 2026-09-30. Legacy ids stay
    # readable so past runs/logs keep validating, but new datasets must not use them.
    legacy: bool = False


CURRENT = (Protocol("ST1-1.0.0", "single", 1), Protocol("MT3-1.0.0", "multi", 3),
           Protocol("MT7-1.0.0", "multi", 7), Protocol("MT10-1.0.0", "multi", 10))
LEGACY = (Protocol("ST1-v1", "single", 1, legacy=True), Protocol("MT3-v1", "multi", 3, legacy=True),
          Protocol("MT7-v1", "multi", 7, legacy=True), Protocol("MT10-v1", "multi", 10, legacy=True))
PROTOCOLS: dict[str, Protocol] = {p.protocol_id: p for p in CURRENT + LEGACY}


def render_user_message(protocol: Protocol, message_text: str, context_text: str | None) -> str:
    """The user-message content actually sent for one turn. Only prompts fields marked ○/△ go in."""
    if not context_text or not context_text.strip():
        return message_text
    return protocol.context_template.format(context_text=context_text, message_text=message_text)
