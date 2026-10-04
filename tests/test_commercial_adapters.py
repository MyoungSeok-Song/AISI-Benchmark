"""상용 어댑터 3종의 오프라인 검사. 네트워크 호출이 없다.

tests/fixtures/<공급자>/*.json의 가짜 HTTP 응답을 어댑터에 넣어 정규화 결과를 확인한다.
가짜 응답은 각 공급자 공식 문서의 응답 형식을 본뜬 것이며 실제 응답이 아니다. 실제 호출이
허용되면 실응답으로 대조해 고쳐야 한다(README '상용 모델' 절의 확인 필요 목록).

OpenAI·Gemini는 전송 함수를 바꿔 끼우고, Anthropic은 SDK에 가짜 전송 계층을 넣는다.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx2
from anthropic import DefaultHttpxClient

RUNNER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RUNNER_DIR))

from kyab_runner.adapters import create_adapter                        # noqa: E402
from kyab_runner.adapters.anthropic import AnthropicAdapter            # noqa: E402
from kyab_runner.adapters.base import CallInfo                         # noqa: E402
from kyab_runner.adapters.gemini import GeminiAdapter                  # noqa: E402
from kyab_runner.adapters.http_json import HttpReply, TransportFailure, TransportTimeout   # noqa: E402
from kyab_runner.adapters.openai import OpenAIAdapter                  # noqa: E402
from kyab_runner.config import ConfigError, _check_options, load_config   # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONFIG = load_config()
FAKE_KEY = "test-key-not-real-0123456789"
PARAMS = {"temperature": 0.0, "top_p": 1.0, "max_output_tokens": 1024}
CALL = CallInfo("KYAB-900101", 2, 1, 1)
MESSAGES = [{"role": "system", "content": "시스템 지시"},
            {"role": "user", "content": "첫 질문"},
            {"role": "assistant", "content": "첫 응답"},
            {"role": "user", "content": "둘째 질문"}]

# 공급자 공통 기대값: 시나리오 -> (response_status, finish_reason, block_source, retryable)
EXPECTED = {
    "normal":       ("success", "stop", "", False),
    "refusal_text": ("success", "stop", "", False),        # 모델이 스스로 거절한 문장은 success
    "blocked":      ("blocked", "content_filter", "provider", False),
    "rate_limit":   ("error", "", "", True),               # 429는 재시도 대상
    "empty":        ("empty", "length", "", False),        # 추론이 출력 한도를 다 쓴 빈 응답
    "length":       ("success", "length", "", False),
}


def fixture(provider, name):
    return json.loads((FIXTURES / provider / f"{name}.json").read_text(encoding="utf-8"))


class Recorder:
    """전송 함수 대역. 넘겨받은 요청을 기록하고 정해 둔 응답을 돌려준다."""

    def __init__(self, provider, name):
        self._fixture = fixture(provider, name)
        self.url = self.headers = self.body = None

    def __call__(self, url, headers, body, timeout):
        self.url, self.headers, self.body = url, headers, body
        data = self._fixture
        return HttpReply(data["status"], data["body"], json.dumps(data["body"], ensure_ascii=False))


class CommercialTestCase(unittest.TestCase):
    """키 환경변수를 가짜 값으로 채워 둔다. 실제 키는 쓰지 않는다."""
    model_id = ""

    def setUp(self):
        self.model = CONFIG.models[self.model_id]
        patcher = mock.patch.dict(os.environ, {self.model.options["api_key_env"]: FAKE_KEY})
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_outcome(self, result, name):
        status, finish, block_source, retryable = EXPECTED[name]
        self.assertEqual((result.response_status, result.finish_reason, result.block_source, result.retryable),
                         (status, finish, block_source, retryable), name)
        self.assertEqual(bool(result.response_text), status == "success", name)
        self.assertIn(result.finish_reason, ["", *CONFIG["finish_reasons"]])
        self.assertIsInstance(result.raw_response, dict)      # 원본 응답을 그대로 보존

    def assert_no_key_leak(self, adapter):
        self.assertNotIn(FAKE_KEY, json.dumps(adapter.describe(), ensure_ascii=False))
        self.assertEqual(adapter.describe()["api_key_env"], self.model.options["api_key_env"])


class HttpAdapterChecks:
    """OpenAI·Gemini 공통 검사 (전송 함수를 바꿔 끼우는 어댑터)."""
    adapter_class = None
    provider = ""

    def call(self, name):
        transport = Recorder(self.provider, name)
        return self.adapter_class(self.model, transport=transport).complete(MESSAGES, PARAMS, CALL), transport

    def test_scenarios(self):
        for name in EXPECTED:
            result, _ = self.call(name)
            self.assert_outcome(result, name)

    def test_transport_errors(self):
        def timeout(*_):
            raise TransportTimeout("timed out")

        def refused(*_):
            raise TransportFailure("connection refused")

        result = self.adapter_class(self.model, transport=timeout).complete(MESSAGES, PARAMS, CALL)
        self.assertEqual((result.response_status, result.retryable), ("timeout", True))
        result = self.adapter_class(self.model, transport=refused).complete(MESSAGES, PARAMS, CALL)
        self.assertEqual((result.response_status, result.error_code, result.retryable),
                         ("error", "connection_error", True))

    def test_omit_params_are_not_sent(self):
        """공급자가 받지 않는 파라미터는 설정으로 빼고, 그 사실이 describe()에 남는다."""
        options = {**self.model.options, "omit_params": ["temperature", "top_p"]}
        model = type(self.model)(**{**self.model.__dict__, "options": options})
        adapter = self.adapter_class(model, transport=Recorder(self.provider, "normal"))
        body = json.dumps(adapter.build_request(MESSAGES, PARAMS))
        self.assertNotIn("temperature", body)
        self.assertNotIn("top", body)
        self.assertEqual(adapter.describe()["omit_params"], ["temperature", "top_p"])

    def test_key_is_not_described(self):
        self.assert_no_key_leak(self.adapter_class(self.model, transport=Recorder(self.provider, "normal")))


class OpenAITest(HttpAdapterChecks, CommercialTestCase):
    model_id, provider, adapter_class = "gpt-5.6-terra", "openai", OpenAIAdapter

    def test_request_shape(self):
        _, sent = self.call("normal")
        self.assertTrue(sent.url.endswith("/chat/completions"))
        self.assertEqual(sent.headers, {"Authorization": f"Bearer {FAKE_KEY}"})
        self.assertEqual(sent.body["messages"], MESSAGES)              # 매 호출 전체 이력
        self.assertIs(sent.body["store"], False)                       # 공급자 쪽 대화 저장 끔
        self.assertEqual((sent.body["temperature"], sent.body["top_p"], sent.body["max_completion_tokens"]),
                         (0.0, 1.0, 1024))

    def test_policy_http400_is_blocked(self):
        result, _ = self.call("blocked_http400")
        self.assertEqual((result.response_status, result.block_source), ("blocked", "provider"))

    def test_other_http400_is_not_retried(self):
        result, _ = self.call("bad_request")
        self.assertEqual((result.response_status, result.error_code, result.retryable), ("error", "http_400", False))
        self.assertIn("temperature", result.error_message)


class GeminiTest(HttpAdapterChecks, CommercialTestCase):
    model_id, provider, adapter_class = "gemini-3.8-flash", "gemini", GeminiAdapter

    def test_request_shape(self):
        _, sent = self.call("normal")
        self.assertTrue(sent.url.endswith(f"/models/{self.model.options['api_model']}:generateContent"))
        self.assertEqual(sent.headers, {"x-goog-api-key": FAKE_KEY})
        self.assertNotIn(FAKE_KEY, sent.url)                           # 키를 주소에 넣지 않는다
        self.assertEqual(sent.body["systemInstruction"], {"parts": [{"text": "시스템 지시"}]})
        self.assertEqual([(c["role"], c["parts"][0]["text"]) for c in sent.body["contents"]],
                         [("user", "첫 질문"), ("model", "첫 응답"), ("user", "둘째 질문")])
        self.assertEqual(sent.body["generationConfig"], {"temperature": 0.0, "topP": 1.0, "maxOutputTokens": 1024})
        self.assertNotIn("safetySettings", sent.body)                  # 서비스 기본 안전 설정 그대로

    def test_thought_parts_are_excluded(self):
        result, _ = self.call("normal")
        self.assertEqual(result.response_text, "안녕하세요. 무엇을 도와드릴까요?")

    def test_prompt_block_is_blocked(self):
        result, _ = self.call("blocked_prompt")
        self.assertEqual((result.response_status, result.block_source, result.finish_reason),
                         ("blocked", "provider", "content_filter"))


class AnthropicTest(CommercialTestCase):
    model_id = "claude-sonnet-4.6"

    def adapter(self, name):
        """SDK에 가짜 전송 계층을 넣은 어댑터와, 보낸 요청을 담을 dict."""
        data, sent = fixture("anthropic", name), {}

        def handler(request):
            sent.update(url=str(request.url), body=json.loads(request.content), key=request.headers.get("x-api-key"))
            return httpx2.Response(data["status"], json=data["body"])

        client = DefaultHttpxClient(transport=httpx2.MockTransport(handler))
        return AnthropicAdapter(self.model, http_client=client), sent

    def test_scenarios(self):
        for name in EXPECTED:
            adapter, _ = self.adapter(name)
            self.assert_outcome(adapter.complete(MESSAGES, PARAMS, CALL), name)

    def test_request_shape(self):
        adapter, sent = self.adapter("normal")
        adapter.complete(MESSAGES, PARAMS, CALL)
        body = sent["body"]
        self.assertTrue(sent["url"].endswith("/v1/messages"))
        self.assertEqual(sent["key"], FAKE_KEY)
        self.assertEqual((body["model"], body["max_tokens"], body["system"]), ("claude-sonnet-4-6", 1024, "시스템 지시"))
        self.assertEqual(body["messages"], MESSAGES[1:])               # system은 따로, 나머지는 전체 이력
        self.assertEqual(body["temperature"], 0.0)
        self.assertNotIn("top_p", body)                                # temperature와 함께 보낼 수 없음
        self.assertNotIn("thinking", body)
        self.assertNotIn("fallbacks", body)                            # 다른 모델로 넘기지 않음

    def test_server_error_is_retryable_and_bad_request_is_not(self):
        adapter, _ = self.adapter("overloaded")
        result = adapter.complete(MESSAGES, PARAMS, CALL)
        self.assertEqual((result.response_status, result.error_code, result.retryable), ("error", "http_529", True))
        adapter, _ = self.adapter("bad_request")
        result = adapter.complete(MESSAGES, PARAMS, CALL)
        self.assertEqual((result.response_status, result.error_code, result.retryable), ("error", "http_400", False))

    def test_sdk_does_not_retry_by_itself(self):
        """재시도는 러너가 한다. SDK가 몰래 다시 보내면 보조 로그의 시도 횟수가 틀어진다."""
        calls = []

        def handler(request):
            calls.append(1)
            return httpx2.Response(429, json=fixture("anthropic", "rate_limit")["body"])

        adapter = AnthropicAdapter(self.model, http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
        adapter.complete(MESSAGES, PARAMS, CALL)
        self.assertEqual(len(calls), 1)

    def test_timeout_and_connection_error(self):
        def timeout(request):
            raise httpx2.ReadTimeout("timed out", request=request)

        def refused(request):
            raise httpx2.ConnectError("connection refused", request=request)

        for handler, expected in ((timeout, ("timeout", True)), (refused, ("error", True))):
            client = DefaultHttpxClient(transport=httpx2.MockTransport(handler))
            result = AnthropicAdapter(self.model, http_client=client).complete(MESSAGES, PARAMS, CALL)
            self.assertEqual((result.response_status, result.retryable), expected)

    def test_key_is_not_described(self):
        self.assert_no_key_leak(self.adapter("normal")[0])


class KeyAndRegistrationTest(unittest.TestCase):
    MODELS = ("gpt-5.6-terra", "claude-sonnet-4.6", "gemini-3.8-flash")

    def test_missing_key_stops_with_clear_message(self):
        for model_id in self.MODELS:
            model = CONFIG.models[model_id]
            env = {k: v for k, v in os.environ.items() if k != model.options["api_key_env"]}
            with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(SystemExit) as raised:
                create_adapter(model)
            self.assertIn(model.options["api_key_env"], str(raised.exception.code))

    def test_commercial_models_are_disabled(self):
        """키·D06·D08 확인 전에는 상용 모델이 실행되지 않는다."""
        for model_id in self.MODELS:
            self.assertFalse(CONFIG.models[model_id].enabled, model_id)


if __name__ == "__main__":
    unittest.main()


class OptionGuardTest(unittest.TestCase):
    """models.yaml 추가 설정(extra_body·extra_generation_config)은 호출 파라미터를 덮어쓸 수 없다(회신 ① 한도 변경 뒤 어긋남 방지)."""

    def test_forbidden_keys_rejected_for_every_adapter_option_block(self):
        for block, key in (("extra_body", "max_tokens"), ("extra_body", "max_completion_tokens"), ("extra_body", "temperature"),
                           ("extra_body", "top_p"), ("extra_generation_config", "maxOutputTokens"), ("extra_generation_config", "topP"),
                           ("extra_body", "top_k"), ("extra_body", "seed"), ("extra_body", "stop"), ("extra_body", "min_tokens"),
                           ("extra_body", "presence_penalty"), ("extra_generation_config", "candidateCount"),
                           # Gemini: generationConfig 블록을 통째로 넣어 러너의 블록을 덮어쓰는 우회
                           ("extra_body", "generationConfig"), ("extra_body", "generation_config")):
            with self.assertRaises(ConfigError) as ctx:
                _check_options("some-model", {block: {key: 1}})
            self.assertIn(key, str(ctx.exception))
            self.assertIn("run_params", str(ctx.exception))

    def test_omit_params_cannot_drop_output_limit(self):
        with self.assertRaises(ConfigError):
            _check_options("m", {"omit_params": ["max_output_tokens"]})
        _check_options("m", {"omit_params": ["top_p"]})

    def test_other_keys_allowed(self):
        _check_options("m", {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "minimal"},
                             "extra_generation_config": {"thinkingConfig": {"thinkingBudget": 0}}})
        _check_options("m", {})

    def test_shipped_config_passes_and_uses_long_timeout(self):
        config = load_config()
        for model_id in ("gpt-5.6-terra", "claude-sonnet-4.6", "gemini-3.8-flash"):
            self.assertEqual(config.models[model_id].options["request_timeout_s"], 600)

