"""Common model interface. The runner only ever calls ModelClient.generate()."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

# responses.response_status vocabulary (codebook 05_responses)
SUCCESS, BLOCKED, ERROR, TIMEOUT, EMPTY = "success", "blocked", "error", "timeout", "empty"

# Normalized responses.error_code values (codebook: "정규화 오류 코드 문자열"; list is our proposal).
ERROR_CODES = (
    "AUTH_ERROR", "RATE_LIMIT", "TIMEOUT", "NETWORK_ERROR", "BAD_REQUEST", "CONTEXT_LENGTH",
    "PROVIDER_BLOCK", "EMPTY_RESPONSE", "PARSER_ERROR", "SERVER_ERROR", "UNKNOWN_ERROR", "REASONING_LEAK",
)

# Transient failures: the runner retries the same turn with the same messages (RetryPolicy).
# Everything else is deterministic (AUTH, BAD_REQUEST, CONTEXT_LENGTH, PARSER, REASONING_LEAK, UNKNOWN) or part
# of what is measured (PROVIDER_BLOCK, EMPTY_RESPONSE) and is never retried.
TRANSIENT_ERRORS = frozenset({"TIMEOUT", "RATE_LIMIT", "NETWORK_ERROR", "SERVER_ERROR"})

# Normalized responses.finish_reason values.
_FINISH = {
    "stop": "stop", "end_turn": "stop", "stop_sequence": "stop", "eos": "stop",
    "length": "length", "max_tokens": "length",
    "content_filter": "content_filter", "safety": "content_filter", "blocklist": "content_filter",
    "prohibited_content": "content_filter", "refusal": "content_filter",
    "tool_calls": "tool_calls", "function_call": "tool_calls", "tool_use": "tool_calls",
}


def normalize_finish_reason(value: str | None) -> str | None:
    if value is None:
        return None
    return _FINISH.get(str(value).lower(), "other")


@dataclass(frozen=True)
class GenerationConfig:
    temperature: float
    top_p: float
    max_output_tokens: int


@dataclass
class GenerationResult:
    status: str  # success | blocked | error | timeout | empty
    text: str | None
    raw_response: dict[str, Any]  # provider's original object, or a pipeline note when none exists
    sent_messages: list[dict[str, Any]]  # exactly what was sent, after any provider-specific conversion
    finish_reason: str | None = None
    block_source: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ModelInfo:
    """What runs.csv records about the target model. Filled from config/models.yaml, not guessed."""
    model_id: str
    provider: str
    model_version: str
    model_snapshot_date: str
    api_version: str
    extra: dict = field(default_factory=dict)


class ModelClient(Protocol):
    info: ModelInfo

    def generate(self, messages: list[dict[str, Any]], config: GenerationConfig) -> GenerationResult:
        ...


_THINK_TAG = re.compile(r"</?think\s*>", re.IGNORECASE)


def _reasoning_output(raw: dict) -> bool:
    """True if the provider reports reasoning/thinking output (vLLM reasoning parser field or reasoning tokens)."""
    if not isinstance(raw, dict):
        return False
    choices = raw.get("choices")
    msg = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    if isinstance(msg, dict) and any(str(msg.get(k) or "").strip() for k in ("reasoning_content", "reasoning")):
        return True
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    try:
        return int((details or {}).get("reasoning_tokens") or 0) > 0
    except (TypeError, ValueError):
        return False


def check_reasoning_leak(result: GenerationResult, allow_reasoning: bool = False) -> GenerationResult:
    """Turn a success into error/REASONING_LEAK when thinking output is detected.

    A <think> tag in the reply text is always a leak: the text would go into the next turn's history.
    Reasoning reported in a separate field / token count is a leak only when the model is configured with
    thinking off (models.yaml allow_reasoning: false, the default). The reply text is kept for diagnosis.
    """
    if result.status != SUCCESS:
        return result
    if result.text and _THINK_TAG.search(result.text):
        why = "응답 본문에 <think> 태그 — thinking 출력이 섞임 (서버 reasoning parser·chat template 설정 확인)"
    elif not allow_reasoning and _reasoning_output(result.raw_response):
        why = "thinking 끔 설정인데 reasoning 출력(reasoning_content 또는 reasoning_tokens)이 있음"
    else:
        return result
    return replace(result, status=ERROR, error_code="REASONING_LEAK", error_message=why)
