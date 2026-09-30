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


if __name__ == "__main__":
    unittest.main()
