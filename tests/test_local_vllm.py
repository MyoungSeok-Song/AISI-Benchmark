"""로컬 vLLM 어댑터의 응답 정규화 검사. 서버 없이 돈다(원본 응답 객체만 넣어 본다)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kyab_runner.adapters.local_vllm import LocalVllmAdapter, _to_result    # noqa: E402
from kyab_runner.config import ModelEntry, load_config                      # noqa: E402


def completion(content, finish_reason):
    return {"choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class NormalizeTest(unittest.TestCase):
    def test_success_keeps_text_and_raw(self):
        raw = completion("안녕하세요.", "stop")
        result = _to_result(raw, 120)
        self.assertEqual((result.response_status, result.response_text, result.finish_reason),
                         ("success", "안녕하세요.", "stop"))
        self.assertIs(result.raw_response, raw)          # 원본을 고치지 않는다

    def test_length_is_success(self):
        result = _to_result(completion("잘린 응답", "length"), 1)
        self.assertEqual((result.response_status, result.finish_reason), ("success", "length"))

    def test_content_filter_is_blocked(self):
        result = _to_result(completion(None, "content_filter"), 1)
        self.assertEqual((result.response_status, result.block_source, result.response_text),
                         ("blocked", "provider", ""))

    def test_blank_content_is_empty(self):
        for content in (None, "", "  \n"):
            self.assertEqual(_to_result(completion(content, "stop"), 1).response_status, "empty")

    def test_unknown_finish_reason_becomes_other(self):
        result = _to_result(completion("응답", "abort"), 1)
        self.assertEqual(result.finish_reason, "other")
        self.assertIn(result.finish_reason, load_config()["finish_reasons"])


class LocalOnlyTest(unittest.TestCase):
    def test_refuses_non_local_address(self):
        """localhost가 아닌 주소는 연결을 시도하기 전에 거부한다(문항 외부 전송 방지)."""
        model = ModelEntry(model_id="x", adapter="local_vllm", provider="local_vllm", model_version="rev",
                           model_snapshot_date="2026-09-30", api_version="v1", enabled=True,
                           options={"base_url": "https://api.example.com/v1", "served_model_name": "x"})
        with self.assertRaises(ValueError):
            LocalVllmAdapter(model)


class RequestShapeTest(unittest.TestCase):
    """서버 없이 요청 본문을 확인한다(_find_served_model을 패치). max_tokens는 runner.yaml run_params 값이다."""

    def make_adapter(self, extra_body=None):
        from unittest import mock
        model = ModelEntry(model_id="x", adapter="local_vllm", provider="local_vllm", model_version="rev",
                           model_snapshot_date="2026-09-30", api_version="v1", enabled=True,
                           options={"base_url": "http://127.0.0.1:8000/v1", "served_model_name": "x",
                                    "extra_body": extra_body or {}})
        with mock.patch.object(LocalVllmAdapter, "_find_served_model", return_value={"root": "rev", "max_model_len": 32768}):
            return LocalVllmAdapter(model)

    def test_max_tokens_comes_from_run_params(self):
        from unittest import mock
        adapter = self.make_adapter({"chat_template_kwargs": {"enable_thinking": False}})
        params = load_config()["run_params"]
        with mock.patch.object(adapter, "_post", return_value=completion("응답", "stop")) as post:
            adapter.complete([{"role": "user", "content": "q"}], params, None)
        body = post.call_args.args[1]
        self.assertEqual((body["max_tokens"], body["temperature"], body["top_p"]), (8192, 0.0, 1.0))
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(adapter.describe()["max_model_len"], 32768)

    def test_shipped_local_models_have_32k_server_length(self):
        config = load_config()
        for model_id in ("qwen3.8-27b-local", "kanana-2-30b-local"):
            self.assertEqual(config.models[model_id].options["server"]["max_model_len"], 32768)
        self.assertFalse(config.models["qwen3-8b-local"].enabled)         # 가중치 없음


if __name__ == "__main__":
    unittest.main()
