"""Run one item once (one runs.csv row) as an N-turn conversation.

Turn t sends: system, user_1, assistant_1, …, user_t. User turns come from the fixed script in
turn_index order regardless of what the model said; the model's actual replies go into the history.
Each response row is fsync'ed to responses.csv before the next turn starts, so a crash never loses a
completed turn. The run stops early only on a technical failure (error / timeout / blocked / empty).

Transient failures (models.base.TRANSIENT_ERRORS) are retried inside the turn with the same messages
(RetryPolicy); responses.csv keeps the final attempt and call_attempts.csv every attempt. A reply that
leaks thinking output (<think> tags) is recorded as error/REASONING_LEAK and never enters the history.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ..call_metrics import usage_from_raw
from ..ids import IdRegistry
from ..loader import ExecutableItem
from ..models.base import (BLOCKED, ERROR, SUCCESS, TRANSIENT_ERRORS, GenerationConfig, GenerationResult,
                           ModelClient, check_reasoning_leak)
from ..protocol import PROTOCOLS, render_user_message
from ..redact import redact_exact, redact_obj, redact_text
from ..storage import TableWriter
from ..validator import sha256_text


@dataclass(frozen=True)
class RetryPolicy:
    """In-turn retry of transient failures only. Wait before retry k (1-based): min(backoff_s·2^(k-1), max)."""
    max_retries: int = 3
    backoff_s: float = 2.0
    max_backoff_s: float = 30.0
    sleep: Callable[[float], None] = field(default=time.sleep, compare=False, repr=False)

    def wait_s(self, retry_no: int) -> float:
        return min(self.backoff_s * 2 ** (retry_no - 1), self.max_backoff_s)

    def as_dict(self) -> dict:
        return {"max_retries": self.max_retries, "backoff_s": self.backoff_s, "max_backoff_s": self.max_backoff_s,
                "retry_on": sorted(TRANSIENT_ERRORS)}


@dataclass(frozen=True)
class RunContext:
    run_batch_id: str
    dataset_version: str
    system_prompt: str
    gen: GenerationConfig
    safety_profile: str
    tool_profile: str
    execution_library_version: str
    retry: RetryPolicy = RetryPolicy()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def run_item(item: ExecutableItem, rollout_no: int, client: ModelClient, ctx: RunContext, ids: IdRegistry,
             runs: TableWriter, responses: TableWriter, metrics: TableWriter | None = None,
             attempts: TableWriter | None = None) -> dict:
    protocol = PROTOCOLS[item.protocol_id]
    info = client.info
    run_id = ids.new_run_id()
    if hasattr(client, "current_item_id"):  # mock fault targeting only (one client per worker thread)
        client.current_item_id, client.current_run_id = item.item_id, run_id
    run = {
        "run_id": run_id, "run_batch_id": ctx.run_batch_id, "dataset_version": ctx.dataset_version,
        "item_id": item.item_id, "item_version": item.item_version, "rollout_no": rollout_no,
        "persona_id": item.persona_id,
        "protocol_id": item.protocol_id, "model_id": info.model_id, "provider": info.provider,
        "model_version": info.model_version, "model_snapshot_date": info.model_snapshot_date,
        "system_prompt_text": ctx.system_prompt, "system_prompt_hash": sha256_text(ctx.system_prompt),
        "temperature": ctx.gen.temperature, "top_p": ctx.gen.top_p, "max_output_tokens": ctx.gen.max_output_tokens,
        "safety_profile": ctx.safety_profile, "tool_profile": ctx.tool_profile, "api_version": info.api_version,
        "execution_library_version": ctx.execution_library_version, "started_at": now_iso(), "completed_at": "",
        "actual_turn_count": 0,
        "first_fail_turn": "", "first_cfc_turn": "",  # judged later (responses → judgments); never guessed here
        "run_status": "running", "stop_reason": "",
    }
    runs.upsert(run)

    messages = [{"role": "system", "content": ctx.system_prompt}]
    n_success, stop_reason = 0, "planned_end"
    try:
        for turn in item.turns:
            messages.append({"role": "user",
                             "content": render_user_message(protocol, turn.message_text, turn.context_text)})
            tries = _call_with_retry(client, messages, ctx)
            result, call_started, latency_ms = tries[-1]
            response_id = ids.new_response_id()
            if attempts is not None:
                for n, (r, started, ms) in enumerate(tries, 1):
                    attempts.append({"response_id": response_id, "attempt_no": n, "run_id": run["run_id"],
                                     "turn_id": turn.turn_id, "model_id": info.model_id, "request_started_at": started,
                                     "latency_ms": ms, "response_status": r.status, "error_code": r.error_code or "",
                                     "error_message": redact_text(r.error_message or ""), "retried": n < len(tries)})
            responses.append({
                "response_id": response_id, "run_id": run["run_id"], "turn_id": turn.turn_id,
                # research data: only exact known secrets are masked; technical logs get full pattern redaction
                "request_messages_json": redact_exact(_json(result.sent_messages)),
                "response_text": redact_exact(result.text or ""),
                "raw_response_json": _json(redact_obj(result.raw_response)), "response_status": result.status,
                "block_source": result.block_source or "", "finish_reason": result.finish_reason or "",
                "error_code": result.error_code or "", "error_message": redact_text(result.error_message or ""),
            })
            if metrics is not None:
                metrics.append({"response_id": response_id, "run_id": run["run_id"], "turn_id": turn.turn_id,
                                "model_id": info.model_id, "request_started_at": call_started,
                                "latency_ms": latency_ms, **usage_from_raw(result.raw_response),
                                "response_status": result.status, "finish_reason": result.finish_reason or ""})
            if result.status != SUCCESS:
                stop_reason = "provider_block" if result.status == BLOCKED else "error"
                break
            messages.append({"role": "assistant", "content": result.text})
            n_success += 1
    except KeyboardInterrupt:
        stop_reason = "manual_stop"
        _finish(run, n_success, len(item.turns), stop_reason, runs)
        raise
    return _finish(run, n_success, len(item.turns), stop_reason, runs)


def _call_with_retry(client: ModelClient, messages: list[dict], ctx: RunContext) -> list[tuple]:
    """Call the model for one turn; retry transient failures. Returns [(result, started_at, latency_ms), …]."""
    allow_reasoning = bool(getattr(client.info, "extra", {}).get("allow_reasoning", False))
    tries = []
    while True:
        started, t0 = now_iso(), time.perf_counter()
        try:
            result = client.generate(messages, ctx.gen)
        except Exception as e:  # a client bug must still leave an error row, not a silent gap
            result = GenerationResult(ERROR, None, {"pipeline_note": "client_exception",
                                                    "exception_type": type(e).__name__},
                                      list(messages), error_code="UNKNOWN_ERROR", error_message=repr(e)[:2000])
        result = check_reasoning_leak(result, allow_reasoning)
        tries.append((result, started, int((time.perf_counter() - t0) * 1000)))
        retries_done = len(tries) - 1
        if result.status == SUCCESS or result.error_code not in TRANSIENT_ERRORS \
                or retries_done >= ctx.retry.max_retries:
            return tries
        ctx.retry.sleep(ctx.retry.wait_s(retries_done + 1))


def _finish(run: dict, n_success: int, planned: int, stop_reason: str, runs: TableWriter) -> dict:
    run["actual_turn_count"] = n_success
    run["completed_at"] = now_iso()
    if n_success == planned:
        run["run_status"], run["stop_reason"] = "completed", "planned_end"
    else:
        run["run_status"] = "partial" if n_success > 0 else "failed"
        run["stop_reason"] = stop_reason
    runs.upsert(run)
    return run
