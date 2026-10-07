"""로컬 vLLM 어댑터의 응답 정규화 검사. 서버 없이 돈다(원본 응답 객체만 넣어 본다)."""
import unittest
from pathlib import Path

import support                                                              # noqa: F401 (sys.path 설정)

from kyab_runner.adapters.local_vllm import LocalVllmAdapter, _to_result    # noqa: E402
from kyab_runner.config import ModelEntry, load_config                      # noqa: E402


def completion(content, finish_reason):
    return {"choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class NormalizeTest(unittest.TestCase):
    def test_success_keeps_text_and_raw(self):
        raw = completion("안녕하세요.", "stop")
        result = _to_result(raw)
        self.assertEqual((result.response_status, result.response_text, result.finish_reason),
                         ("success", "안녕하세요.", "stop"))
        self.assertIs(result.raw_response, raw)          # 원본을 고치지 않는다

    def test_length_is_success(self):
        result = _to_result(completion("잘린 응답", "length"))
        self.assertEqual((result.response_status, result.finish_reason), ("success", "length"))

    def test_content_filter_is_blocked(self):
        result = _to_result(completion(None, "content_filter"))
        self.assertEqual((result.response_status, result.block_source, result.response_text),
                         ("blocked", "provider", ""))

    def test_blank_content_is_empty(self):
        for content in (None, "", "  \n"):
            self.assertEqual(_to_result(completion(content, "stop")).response_status, "empty")

    def test_unknown_finish_reason_becomes_other(self):
        result = _to_result(completion("응답", "abort"))
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


class ServedModelCheckTest(unittest.TestCase):
    """서버가 올린 스냅샷 확인(adapters-14): root의 마지막 경로 요소가 revision과 같아야 하고, 빈 revision은 거부."""

    def make(self, root, model_version="rev"):
        from unittest import mock
        from kyab_runner.adapters.base import AdapterSetupError
        model = ModelEntry(model_id="x", adapter="local_vllm", provider="local_vllm", model_version=model_version,
                           model_snapshot_date="2026-09-30", api_version="v1", enabled=True,
                           options={"base_url": "http://127.0.0.1:8000/v1", "served_model_name": "x"})
        listing = {"data": [{"id": "x", "root": root, "max_model_len": 32768}]}
        with mock.patch.object(LocalVllmAdapter, "_get", return_value=listing):
            return LocalVllmAdapter(model), AdapterSetupError

    def test_root_must_end_with_revision(self):
        adapter, _ = self.make("/cache/snapshots/rev")
        self.assertEqual(adapter._served["root"], "/cache/snapshots/rev")
        for root in ("/cache/snapshots/other", None, ""):
            with self.assertRaises(ValueError):           # AdapterSetupError(ValueError)
                self.make(root)
        with self.assertRaises(ValueError):
            self.make("/cache/snapshots/", model_version="")

    def test_describe_attaches_server_info_only_for_this_model(self):
        import json
        import shutil
        import tempfile
        from unittest import mock
        from kyab_runner.adapters import local_vllm
        adapter, _ = self.make("/cache/snapshots/rev")
        # 임시 폴더에 쓴다(R21): cwd(runner/)에 쓰면 실패 시 파일이 남아 provenance.git_state가 dirty로 보고 실배치 판본에 .dirty가 붙는다
        tmp = Path(tempfile.mkdtemp(prefix="kyab_test_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        info_file = tmp / "vllm_server.json"
        with mock.patch.object(local_vllm, "SERVER_INFO_FILE", info_file), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("no server")):
            info_file.write_text(json.dumps({"revision": "rev", "port": 8000, "pid": 1}), encoding="utf-8")
            self.assertEqual(adapter.describe()["server"]["pid"], 1)
            info_file.write_text(json.dumps({"revision": "other", "port": 8000}), encoding="utf-8")
            described = adapter.describe()
            self.assertNotIn("server", described)
            self.assertIn("server_info_skipped", described)


if __name__ == "__main__":                            # 파일 맨 끝에 둔다(R20)
    unittest.main()
