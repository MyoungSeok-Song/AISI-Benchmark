"""어댑터 공통부 검사(과제 8 adapters-03·04·05·09): 재시도 표, 결과 도우미, HTTP 공통 틀, 모의 어댑터 규칙. 네트워크 없음."""
import socket
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import httpx2
from anthropic import DefaultHttpxClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kyab_runner.adapters import base, mock as mock_adapter                     # noqa: E402
from kyab_runner.adapters.anthropic import AnthropicAdapter                   # noqa: E402
from kyab_runner.adapters.gemini import GeminiAdapter                         # noqa: E402
from kyab_runner.adapters.http_json import HttpReply                          # noqa: E402
from kyab_runner.adapters.local_vllm import LocalVllmAdapter                  # noqa: E402
from kyab_runner.adapters.openai import OpenAIAdapter                         # noqa: E402
from kyab_runner.config import ModelEntry, load_config                        # noqa: E402

CONFIG = load_config()
CALL = base.CallInfo("KYAB-900001", 1, 1, 1)
PARAMS = {"temperature": 0.0, "top_p": 1.0, "max_output_tokens": 8192}
MESSAGES = [{"role": "user", "content": "q"}]


def reply(status, body, text="원문"):
    return HttpReply(status, body, text)


class RetryPolicyTest(unittest.TestCase):
    """같은 HTTP 상태는 세 상용 어댑터에서 같은 재시도 판단을 받는다. 501(비일시)은 재시도하지 않는다."""

    def http_adapter(self, cls, model_id, status, body):
        env = {CONFIG.models[model_id].options["api_key_env"]: "test-key"}
        with mock.patch.dict("os.environ", env):
            return cls(CONFIG.models[model_id], transport=lambda *_: reply(status, body))

    def test_501_is_not_retried_and_529_is(self):
        for cls, model_id in ((OpenAIAdapter, "gpt-5.6-terra"), (GeminiAdapter, "gemini-3.8-flash")):
            for status, retryable in ((501, False), (529, True), (409, True), (400, False)):
                result = self.http_adapter(cls, model_id, status, {"error": {"message": "x"}}).complete(MESSAGES, PARAMS, CALL)
                self.assertEqual((result.response_status, result.error_code, result.retryable), ("error", f"http_{status}", retryable), cls.__name__)
        for status, retryable in ((501, False), (529, True)):
            def handler(request, status=status):
                return httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "x"}})
            env = {CONFIG.models["claude-sonnet-4.6"].options["api_key_env"]: "test-key"}
            with mock.patch.dict("os.environ", env):
                adapter = AnthropicAdapter(CONFIG.models["claude-sonnet-4.6"],
                                           http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
            result = adapter.complete(MESSAGES, PARAMS, CALL)
            self.assertEqual((result.error_code, result.retryable), (f"http_{status}", retryable))

    def test_non_json_2xx_body_is_invalid_response_not_crash(self):
        """2xx인데 본문이 JSON 객체가 아니면(HTML 오류 페이지) 예외 대신 invalid_response(adapters-02)."""
        for cls, model_id in ((OpenAIAdapter, "gpt-5.6-terra"), (GeminiAdapter, "gemini-3.8-flash")):
            result = self.http_adapter(cls, model_id, 200, None).complete(MESSAGES, PARAMS, CALL)
            self.assertEqual((result.response_status, result.error_code, result.retryable), ("error", "invalid_response", False))


class ChatCompletionResultTest(unittest.TestCase):
    def test_refusal_field_only_counts_for_openai(self):
        raw = {"choices": [{"finish_reason": "stop", "message": {"content": None, "refusal": "거절합니다"}}]}
        self.assertEqual(base.chat_completion_result(raw, refusal_as_text=True).response_text, "거절합니다")
        self.assertEqual(base.chat_completion_result(raw).response_status, "empty")       # 로컬 vLLM은 refusal 필드를 쓰지 않는다


class LocalVllmErrorTest(unittest.TestCase):
    """로컬 어댑터의 시간 초과·연결 오류 메시지(05 error_message) 고정."""

    def adapter(self):
        model = ModelEntry(model_id="x", adapter="local_vllm", provider="local_vllm", model_version="rev",
                           model_snapshot_date="2026-09-30", api_version="v1", enabled=True,
                           options={"base_url": "http://127.0.0.1:8000/v1", "served_model_name": "x", "request_timeout_s": 7})
        with mock.patch.object(LocalVllmAdapter, "_find_served_model", return_value={"root": "rev"}):
            return LocalVllmAdapter(model)

    def test_messages(self):
        adapter = self.adapter()
        cases = ((socket.timeout("t"), ("timeout", "timeout", "7초 안에 응답 없음", True)),
                 (urllib.error.URLError(socket.timeout("t")), ("timeout", "timeout", "7초 안에 응답 없음", True)),
                 (urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
                  ("error", "connection_error", "<urlopen error [Errno 111] Connection refused>", True)),
                 (ValueError("Expecting value: line 1 column 1 (char 0)"),
                  ("error", "invalid_response", "Expecting value: line 1 column 1 (char 0)", False)))
        for exc, expected in cases:
            with mock.patch.object(adapter, "_post", side_effect=exc):
                result = adapter.complete(MESSAGES, PARAMS, CALL)
            self.assertEqual((result.response_status, result.error_code, result.error_message, result.retryable), expected)


class MockAdapterTest(unittest.TestCase):
    MODEL = CONFIG.models["mock-echo"]

    def test_scenario_results_use_shared_helpers(self):
        adapter = mock_adapter.MockAdapter(self.MODEL, {"default": "normal"})
        for scenario, expected in (("normal", ("success", "stop", "", "")), ("refusal", ("success", "stop", "", "")),
                                   ("length", ("success", "length", "", "")), ("empty", ("empty", "stop", "", "empty_response")),
                                   ("blocked", ("blocked", "content_filter", "provider", "")),
                                   ("error", ("error", "", "", "provider_error")), ("timeout", ("timeout", "", "", "timeout"))):
            adapter._default = scenario
            result = adapter.complete(MESSAGES, PARAMS, CALL)
            self.assertEqual((result.response_status, result.finish_reason, result.block_source, result.error_code), expected, scenario)
        blocked = mock_adapter.MockAdapter(self.MODEL, {"default": "blocked"}).complete(MESSAGES, PARAMS, CALL)
        self.assertIsNone(blocked.raw_response["choices"][0]["message"]["content"])
        self.assertEqual(blocked.raw_response["usage"]["completion_tokens"], 0)

    def test_flaky_recovers_on_third_attempt(self):
        adapter = mock_adapter.MockAdapter(self.MODEL, {"default": "flaky"})
        statuses = [adapter.complete(MESSAGES, PARAMS, base.CallInfo("i", 1, 1, attempt)).response_status for attempt in (1, 2, 3)]
        self.assertEqual(statuses, ["error", "error", "success"])

    def test_rule_keys_are_validated(self):
        with self.assertRaises(base.AdapterSetupError) as caught:
            mock_adapter.MockAdapter(self.MODEL, {"rules": [{"item_id": "x", "turn": 2, "scenario": "error"}]})
        self.assertIn("rules[0]", str(caught.exception))
        self.assertIn("turn", str(caught.exception))
        with self.assertRaises(ValueError):                 # 옛 호출자 호환: AdapterSetupError는 ValueError다
            mock_adapter.MockAdapter(self.MODEL, {"default": "bogus"})


if __name__ == "__main__":
    unittest.main()
