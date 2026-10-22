"""Mock target model: no network, deterministic output, optional fault injection per user turn."""
from __future__ import annotations

import copy
from typing import Any

from .base import BLOCKED, EMPTY, ERROR, SUCCESS, TIMEOUT, GenerationConfig, GenerationResult, ModelInfo

_FAULTS = {
    TIMEOUT: dict(error_code="TIMEOUT", error_message="mock: simulated request timeout"),
    ERROR: dict(error_code="SERVER_ERROR", error_message="mock: simulated HTTP 500"),
    BLOCKED: dict(block_source="provider", finish_reason="content_filter", error_code="PROVIDER_BLOCK",
                  error_message="mock: simulated provider content filter"),
    EMPTY: dict(finish_reason="stop", error_code="EMPTY_RESPONSE", error_message="mock: empty completion"),
}


class MockModelClient:
    """Replies "[MOCK] …" naming the user turn and the number of messages it received.

    faults: {user_turn_number: status} makes that turn fail with the given response_status;
    only_items limits faults to the listed item_ids (matched through `current_item_id`);
    fault_repeats makes each fault fire only that many times per (run, turn), then succeed (a transient
    failure that a retry recovers from). None = the fault fires every time. Counting per run keeps the
    outcome independent of run order, so sequential and parallel execution give the same results.
    """

    def __init__(self, info: ModelInfo, faults: dict[int, str] | None = None, only_items: list[str] | None = None,
                 fault_repeats: int | None = None):
        self.info = info
        self.faults = {int(k): v for k, v in (faults or {}).items()}
        self.only_items = set(only_items or [])
        self.fault_repeats = fault_repeats
        self._fired: dict[tuple, int] = {}
        self.current_item_id: str | None = None
        self.current_run_id: str | None = None
        self.calls: list[list[dict[str, Any]]] = []

    def generate(self, messages: list[dict[str, Any]], config: GenerationConfig) -> GenerationResult:
        sent = copy.deepcopy(messages)
        self.calls.append(sent)
        turn = sum(1 for m in messages if m["role"] == "user")
        fault = self.faults.get(turn)
        key = (self.current_run_id or self.current_item_id, turn)
        if fault and self.fault_repeats is not None and self._fired.get(key, 0) >= self.fault_repeats:
            fault = None
        if fault and (not self.only_items or self.current_item_id in self.only_items):
            self._fired[key] = self._fired.get(key, 0) + 1
            kw = dict(_FAULTS[fault])
            raw = {"mock": True, "simulated_fault": fault, "turn": turn}
            return GenerationResult(status=fault, text="" if fault == EMPTY else None, raw_response=raw,
                                    sent_messages=sent, **kw)
        text = f"[MOCK] Response to user turn {turn} (received {len(messages)} messages)."
        raw = {
            "mock": True,
            "model": self.info.model_version,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "params": {"temperature": config.temperature, "top_p": config.top_p,
                       "max_output_tokens": config.max_output_tokens},
        }
        return GenerationResult(status=SUCCESS, text=text, raw_response=raw, sent_messages=sent, finish_reason="stop")
