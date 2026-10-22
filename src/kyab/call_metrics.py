"""Per-call technical metrics (latency, token usage) — a sidecar file, NOT one of the 7 codebook CSVs.

The codebook's responses.csv has no latency / usage columns. Rather than adding columns to a codebook
table or wrapping the provider's raw response (raw_response_json must stay the provider's own object),
each model call also appends one row to <output_dir>/call_metrics.csv keyed by response_id.
"""
from __future__ import annotations

from .schemas import AI, Field, Req, Table

E, N = Req.EXEC, AI.NEVER
CALL_METRICS = Table("call_metrics", "(sidecar)", (
    Field("response_id", E, N),
    Field("run_id", E, N),
    Field("turn_id", E, N),
    Field("model_id", E, N),
    Field("request_started_at", E, N, kind="datetime"),
    Field("latency_ms", E, N, kind="int"),
    Field("input_tokens", Req.COND, N, kind="int"),
    Field("output_tokens", Req.COND, N, kind="int"),
    Field("reasoning_tokens", Req.COND, N, kind="int"),
    Field("response_status", E, N),
    Field("finish_reason", Req.COND, N),
), primary_key=("response_id",))

# One row per model call attempt (retries included). responses.csv keeps one row per turn — the final
# attempt — so the codebook table is unchanged; earlier failed attempts are only recorded here.
CALL_ATTEMPTS = Table("call_attempts", "(sidecar)", (
    Field("response_id", E, N),
    Field("attempt_no", E, N, kind="int"),
    Field("run_id", E, N),
    Field("turn_id", E, N),
    Field("model_id", E, N),
    Field("request_started_at", E, N, kind="datetime"),
    Field("latency_ms", E, N, kind="int"),
    Field("response_status", E, N),
    Field("error_code", Req.COND, N),
    Field("error_message", Req.COND, N),
    Field("retried", E, N, kind="bool"),
), primary_key=("response_id", "attempt_no"))


def _get(d, *path):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def usage_from_raw(raw: dict) -> dict:
    """Token counts from the provider's raw response (OpenAI/vLLM, Anthropic, Gemini shapes); blank if absent."""
    u = raw.get("usage") if isinstance(raw, dict) else None
    if isinstance(u, dict):
        reasoning = _get(u, "completion_tokens_details", "reasoning_tokens") or _get(
            u, "output_tokens_details", "reasoning_tokens")
        return {"input_tokens": u.get("prompt_tokens", u.get("input_tokens")),
                "output_tokens": u.get("completion_tokens", u.get("output_tokens")),
                "reasoning_tokens": reasoning}
    g = raw.get("usageMetadata") if isinstance(raw, dict) else None
    if isinstance(g, dict):
        return {"input_tokens": g.get("promptTokenCount"), "output_tokens": g.get("candidatesTokenCount"),
                "reasoning_tokens": g.get("thoughtsTokenCount")}
    return {"input_tokens": None, "output_tokens": None, "reasoning_tokens": None}
