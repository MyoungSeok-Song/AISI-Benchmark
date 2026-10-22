import json

import httpx
import pytest
from conftest import GEN, mock_info

from kyab.models.base import ModelInfo
from kyab.models.mock import MockModelClient
from kyab.models.openai_compat import OpenAICompatibleClient
from kyab.runner.conversation import run_item
from kyab.schemas import RESPONSES, RUNS
from kyab.storage import read_table
from kyab.validator import validate_outputs


def run_mt3(small_dataset, out, ctx, faults):
    client = MockModelClient(mock_info(), faults=faults)
    run = run_item(small_dataset.executable_items()[1], 1, client, ctx, out["ids"], out["runs"], out["responses"])
    return run, read_table(out["dir"] / "responses.csv", RESPONSES), client


@pytest.mark.parametrize("status,stop", [("timeout", "error"), ("error", "error"), ("empty", "error"),
                                         ("blocked", "provider_block")])
def test_failure_on_turn_2_gives_partial_run(small_dataset, out, ctx, status, stop):
    run, resps, client = run_mt3(small_dataset, out, ctx, {2: status})
    assert (run["run_status"], run["stop_reason"], run["actual_turn_count"]) == ("partial", stop, 1)
    assert [r["response_status"] for r in resps] == ["success", status]
    # a persistent transient failure is retried max_retries times; turn 3 is never sent after a failure
    assert len(client.calls) == 1 + (1 + ctx.retry.max_retries if status in ("timeout", "error") else 1)
    if status == "blocked":
        assert resps[1]["block_source"] == "provider"
    assert resps[1]["error_code"]
    assert json.loads(resps[1]["raw_response_json"])["simulated_fault"] == status
    runs = read_table(out["dir"] / "runs.csv", RUNS)
    assert validate_outputs(small_dataset.items, small_dataset.prompts, runs, resps) == []


def test_failure_on_turn_1_gives_failed_run(small_dataset, out, ctx):
    run, resps, _ = run_mt3(small_dataset, out, ctx, {1: "error"})
    assert (run["run_status"], run["stop_reason"], run["actual_turn_count"]) == ("failed", "error", 0)
    assert len(resps) == 1


def test_client_exception_still_leaves_an_error_row(small_dataset, out, ctx):
    class Broken:
        info = mock_info("broken")

        def generate(self, messages, config):
            raise RuntimeError("bug in adapter")

    run = run_item(small_dataset.executable_items()[0], 1, Broken(), ctx, out["ids"], out["runs"], out["responses"])
    r = read_table(out["dir"] / "responses.csv", RESPONSES)[0]
    assert run["run_status"] == "failed"
    assert (r["response_status"], r["error_code"]) == ("error", "UNKNOWN_ERROR")
    assert json.loads(r["raw_response_json"])["pipeline_note"] == "client_exception"


# ------------------------------------------------------------ OpenAI-compatible client, no network

INFO = ModelInfo("api_test", "test-provider", "test-model-2026-01-01", "2026-09-30", "v1")
MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def client_with(handler, monkeypatch, **kw):
    monkeypatch.setenv("TEST_API_KEY", "sk-secret-123")
    return OpenAICompatibleClient(INFO, base_url="https://example.test/v1", request_model="test-model",
                                  api_key_env="TEST_API_KEY", transport=httpx.MockTransport(handler), **kw)


def completion(content, finish="stop", **msg):
    return {"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": content, **msg},
                                    "finish_reason": finish}], "usage": {"prompt_tokens": 3}}


def test_request_body_and_success(monkeypatch):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json=completion("hello"))

    r = client_with(handler, monkeypatch, max_tokens_param="max_completion_tokens").generate(MSGS, GEN)
    assert (r.status, r.text, r.finish_reason) == ("success", "hello", "stop")
    assert r.raw_response["usage"] == {"prompt_tokens": 3}
    assert seen["body"] == {"model": "test-model", "messages": MSGS, "temperature": 0.0, "top_p": 1.0,
                            "max_completion_tokens": 1024}
    assert seen["auth"] == "Bearer sk-secret-123"
    assert "sk-secret" not in json.dumps(r.raw_response)


def test_omit_params_are_not_sent(monkeypatch):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=completion("hello"))

    client_with(handler, monkeypatch, max_tokens_param="max_completion_tokens",
                omit_params=["temperature", "top_p"]).generate(MSGS, GEN)
    assert seen["body"] == {"model": "test-model", "messages": MSGS, "max_completion_tokens": 1024}
    with pytest.raises(ValueError, match="omit_params"):
        client_with(handler, monkeypatch, omit_params=["max_tokens"])


@pytest.mark.parametrize("response,status,code", [
    (httpx.Response(200, json=completion(None, "content_filter")), "blocked", "PROVIDER_BLOCK"),
    (httpx.Response(200, json=completion("", "stop")), "empty", "EMPTY_RESPONSE"),
    (httpx.Response(429, json={"error": {"message": "rate limited"}}), "error", "RATE_LIMIT"),
    (httpx.Response(401, json={"error": {"message": "bad key sk-secret-123"}}), "error", "AUTH_ERROR"),
    (httpx.Response(400, json={"error": {"message": "maximum context length exceeded"}}), "error", "CONTEXT_LENGTH"),
    (httpx.Response(400, json={"error": {"code": "content_filter"}}), "blocked", "PROVIDER_BLOCK"),
    (httpx.Response(503, text="upstream down"), "error", "SERVER_ERROR"),
    (httpx.Response(200, text="not json"), "error", "PARSER_ERROR"),
])
def test_error_mapping(monkeypatch, response, status, code):
    r = client_with(lambda req: response, monkeypatch).generate(MSGS, GEN)
    assert (r.status, r.error_code) == (status, code)
    assert isinstance(r.raw_response, dict)
    assert "sk-secret-123" not in (r.error_message or "") + json.dumps(r.raw_response)
    if status == "blocked":
        assert r.block_source == "provider"


def test_model_refusal_is_a_success_not_a_block(monkeypatch):
    r = client_with(lambda req: httpx.Response(200, json=completion(None, "stop", refusal="I can't help with that.")),
                    monkeypatch).generate(MSGS, GEN)
    assert (r.status, r.text, r.block_source) == ("success", "I can't help with that.", None)


def test_timeout_and_network_errors(monkeypatch):
    def timeout(req):
        raise httpx.ReadTimeout("timed out", request=req)

    def down(req):
        raise httpx.ConnectError("refused", request=req)

    t = client_with(timeout, monkeypatch).generate(MSGS, GEN)
    assert (t.status, t.error_code, t.raw_response["pipeline_note"]) == ("timeout", "TIMEOUT", "no_provider_response")
    n = client_with(down, monkeypatch).generate(MSGS, GEN)
    assert (n.status, n.error_code) == ("error", "NETWORK_ERROR")


def test_missing_api_key_fails_before_any_call(monkeypatch):
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NO_SUCH_KEY"):
        OpenAICompatibleClient(INFO, base_url="https://example.test/v1", request_model="m", api_key_env="NO_SUCH_KEY")


def test_usage_extraction_shapes():
    from kyab.call_metrics import usage_from_raw
    assert usage_from_raw({"usage": {"prompt_tokens": 5, "completion_tokens": 7,
                                     "completion_tokens_details": {"reasoning_tokens": 2}}}) == \
        {"input_tokens": 5, "output_tokens": 7, "reasoning_tokens": 2}
    assert usage_from_raw({"usage": {"input_tokens": 3, "output_tokens": 4}})["output_tokens"] == 4
    assert usage_from_raw({"usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2,
                                             "thoughtsTokenCount": 9}})["reasoning_tokens"] == 9
    assert usage_from_raw({"mock": True}) == {"input_tokens": None, "output_tokens": None, "reasoning_tokens": None}
