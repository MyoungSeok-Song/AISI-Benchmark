import json

import pytest
from conftest import mock_info

from kyab.call_metrics import CALL_ATTEMPTS
from kyab.models.base import ERROR, SUCCESS, GenerationResult, ModelInfo, check_reasoning_leak
from kyab.models.mock import MockModelClient
from kyab.runner.conversation import RetryPolicy, run_item
from kyab.schemas import RESPONSES, RUNS
from kyab.storage import TableWriter, read_table
from kyab.validator import validate_outputs


def run_mt3(small_dataset, out, ctx, client):
    attempts = TableWriter(out["dir"] / "call_attempts.csv", CALL_ATTEMPTS)
    run = run_item(small_dataset.executable_items()[1], 1, client, ctx, out["ids"], out["runs"], out["responses"],
                   attempts=attempts)
    return (run, read_table(out["dir"] / "responses.csv", RESPONSES),
            read_table(out["dir"] / "call_attempts.csv", CALL_ATTEMPTS, allow_extra=False))


# ------------------------------------------------------------------------------------------- retry

def test_transient_failure_recovered_by_retry(small_dataset, out, ctx):
    waits = []
    ctx = ctx.__class__(**{**ctx.__dict__, "retry": RetryPolicy(sleep=waits.append)})
    client = MockModelClient(mock_info(), faults={2: "timeout"}, fault_repeats=2)
    run, resps, attempts = run_mt3(small_dataset, out, ctx, client)
    assert (run["run_status"], run["actual_turn_count"]) == ("completed", 3)
    assert [r["response_status"] for r in resps] == ["success"] * 3  # one responses row per turn
    t2 = [a for a in attempts if a["turn_id"] == resps[1]["turn_id"]]
    assert [(a["attempt_no"], a["response_status"], a["error_code"], a["retried"]) for a in t2] == [
        ("1", "timeout", "TIMEOUT", "true"), ("2", "timeout", "TIMEOUT", "true"), ("3", "success", "", "false")]
    assert {a["response_id"] for a in t2} == {resps[1]["response_id"]}
    assert waits == [2.0, 4.0]
    # every retry sends exactly the same history as the first attempt
    assert client.calls[1] == client.calls[2] == client.calls[3]
    runs = read_table(out["dir"] / "runs.csv", RUNS)
    assert validate_outputs(small_dataset.items, small_dataset.prompts, runs, resps) == []


def test_retry_gives_up_after_max_retries(small_dataset, out, ctx):
    client = MockModelClient(mock_info(), faults={1: "error"})  # SERVER_ERROR every time
    run, resps, attempts = run_mt3(small_dataset, out, ctx, client)
    assert (run["run_status"], run["stop_reason"]) == ("failed", "error")
    assert len(resps) == 1 and resps[0]["error_code"] == "SERVER_ERROR"
    assert len(attempts) == 1 + ctx.retry.max_retries
    assert [a["retried"] for a in attempts] == ["true"] * ctx.retry.max_retries + ["false"]


@pytest.mark.parametrize("status", ["blocked", "empty"])
def test_block_and_empty_are_not_retried(small_dataset, out, ctx, status):
    client = MockModelClient(mock_info(), faults={1: status})
    _, _, attempts = run_mt3(small_dataset, out, ctx, client)
    assert len(client.calls) == 1 and len(attempts) == 1


def test_backoff_is_capped():
    p = RetryPolicy(max_retries=6, backoff_s=2.0, max_backoff_s=10.0)
    assert [p.wait_s(k) for k in range(1, 6)] == [2.0, 4.0, 8.0, 10.0, 10.0]


def test_retry_disabled(small_dataset, out, ctx):
    ctx = ctx.__class__(**{**ctx.__dict__, "retry": RetryPolicy(max_retries=0, sleep=lambda s: None)})
    client = MockModelClient(mock_info(), faults={1: "timeout"}, fault_repeats=1)
    run, _, _ = run_mt3(small_dataset, out, ctx, client)
    assert run["run_status"] == "failed" and len(client.calls) == 1


# ------------------------------------------------------------------------------- reasoning leak

def ok(text, **raw):
    return GenerationResult(SUCCESS, text, {"choices": [{"message": {"content": text}}], **raw}, [])


@pytest.mark.parametrize("text", ["<think>\nplan\n</think>\n\nHi", "Hi</think>", "<THINK >x"])
def test_think_tag_in_text_is_a_leak(text):
    r = check_reasoning_leak(ok(text), allow_reasoning=True)  # tags leak into history even when reasoning allowed
    assert (r.status, r.error_code, r.text) == (ERROR, "REASONING_LEAK", text)


def test_reasoning_field_or_tokens_leak_only_when_thinking_off():
    field = GenerationResult(SUCCESS, "Hi", {"choices": [{"message": {"content": "Hi",
                                                                      "reasoning_content": "hmm"}}]}, [])
    tokens = ok("Hi", usage={"completion_tokens_details": {"reasoning_tokens": 12}})
    for r in (field, tokens):
        assert check_reasoning_leak(r).error_code == "REASONING_LEAK"
        assert check_reasoning_leak(r, allow_reasoning=True).status == SUCCESS
    assert check_reasoning_leak(ok("Hi", usage={"completion_tokens_details": {"reasoning_tokens": 0}})).status == SUCCESS
    assert check_reasoning_leak(ok("plain answer about thinking")).status == SUCCESS


def test_leak_stops_run_and_never_enters_history(small_dataset, out, ctx):
    class Leaky(MockModelClient):
        def generate(self, messages, config):
            r = super().generate(messages, config)
            if sum(m["role"] == "user" for m in messages) == 2:
                r.text = "<think>internal</think>\n" + r.text
            return r

    client = Leaky(mock_info())
    run, resps, attempts = run_mt3(small_dataset, out, ctx, client)
    assert (run["run_status"], run["stop_reason"], run["actual_turn_count"]) == ("partial", "error", 1)
    assert (resps[1]["response_status"], resps[1]["error_code"]) == ("error", "REASONING_LEAK")
    assert resps[1]["response_text"].startswith("<think>")  # kept for diagnosis
    assert len(client.calls) == 2 and len(attempts) == 2  # not retried, turn 3 not sent
    assert all("<think>" not in json.dumps(c) for c in client.calls)


def test_registry_passes_allow_reasoning(tmp_path):
    from kyab.models.registry import build_client
    y = tmp_path / "m.yaml"
    y.write_text("models:\n- {model_id: a, client: mock, provider: p, model_version: v, api_version: x}\n"
                 "- {model_id: b, client: mock, provider: p, model_version: v, api_version: x, allow_reasoning: true}\n")
    assert build_client("a", y).info.extra == {"allow_reasoning": False}
    assert build_client("b", y).info.extra == {"allow_reasoning": True}
    assert isinstance(build_client("a", y).info, ModelInfo)
