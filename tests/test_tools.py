"""도구 검사(tooling-08): check_determinism, build_samples, e2e_check, vllm_server(서버 없이)."""
import importlib.util
import contextlib
import io
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from test_runner import CODEBOOK, CONFIG, RUNNER_DIR, RunnerTestCase, run_single

import build_samples                                                   # noqa: E402
import check_determinism                                               # noqa: E402
from kyab_runner import paths                                          # noqa: E402
from kyab_runner.sources import load_sources_registry                  # noqa: E402

VLLM_SERVER = RUNNER_DIR / "tools" / "vllm_server.py"


def load_tool(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture(main, argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, buffer.getvalue()


class CheckDeterminismTest(RunnerTestCase):
    def test_same_mock_responses_and_exit_codes(self):
        from kyab_runner import csv_io
        self.assertEqual(self.run_cli(run_single)[0], 0)
        batch = self.batch_dirs()[0]
        rows = self.table(batch, "05_responses")
        code, output = capture(check_determinism.main, [str(batch)])
        self.assertEqual(code, check_determinism.EXIT_DIFFERENT)          # 모의 응답 본문에는 반복 번호가 들어 있어 서로 다르다
        for row in rows:
            row["response_text"] = f"같은 응답 {row['turn_id']}"
        csv_io.rewrite_table(CODEBOOK, "05_responses", batch / "05_responses.csv", rows)
        code, output = capture(check_determinism.main, [str(batch)])
        self.assertEqual((code, "3개 동일" in output), (check_determinism.EXIT_SAME, True))
        rows[0]["response_text"] = "다른 응답"
        csv_io.rewrite_table(CODEBOOK, "05_responses", batch / "05_responses.csv", rows)
        self.assertEqual(capture(check_determinism.main, [str(batch)])[0], check_determinism.EXIT_DIFFERENT)
        rows.append({**rows[0], "response_id": "RESP-99999999", "run_id": "RUN-20260101-999999"})
        csv_io.rewrite_table(CODEBOOK, "05_responses", batch / "05_responses.csv", rows)
        code, output = capture(check_determinism.main, [str(batch)])
        self.assertEqual((code, "짝 없는" in output), (check_determinism.EXIT_INVALID, True))
        code, output = capture(check_determinism.main, [str(self.tmp / "RBATCH-20260101-009")])
        self.assertEqual((code, "읽을 수 없습니다" in output), (check_determinism.EXIT_INVALID, True))
        with self.assertRaises(SystemExit):                        # 인자 없음은 argparse 사용 오류
            check_determinism.main([])


@unittest.skipUnless(all((paths.DEFAULT_DATA_DIR / e["local_file"]).exists() for e in load_sources_registry().values()),
                     "원천 원본 CSV(data/)가 없는 환경")
class BuildSamplesTest(RunnerTestCase):
    def test_regenerated_samples_are_byte_identical(self):
        code, output = capture(build_samples.main, ["--out", str(self.tmp / "input")])
        self.assertEqual(code, 0, output)
        for name in ("01_items.csv", "02_item_tags.csv", "03_prompts.csv"):
            self.assertEqual((self.tmp / "input" / name).read_bytes(), (paths.DEFAULT_INPUT_DIR / name).read_bytes(), name)

    def test_missing_data_dir_is_a_message(self):
        code, output = capture(build_samples.main, ["--data-dir", str(self.tmp / "nope"), "--out", str(self.tmp / "x")])
        self.assertEqual((code, "원천 원본을 읽을 수 없습니다" in output), (2, True))


class VllmServerToolTest(RunnerTestCase):
    """GPU·서버 없이: 기동 명령 구성, 판본 확인 실패 시 아무것도 띄우지 않음, 모르는 모델 거부."""

    def setUp(self):
        super().setUp()
        self.tool = load_tool(VLLM_SERVER)
        self.tool.INFO_FILE = self.tmp / "vllm_server.json"
        self.tool.LOG_FILE = self.tmp / "vllm_server.log"
        self.tool.VAR_DIR = self.tmp

    def test_build_command_matches_registered_options(self):
        model = CONFIG.models["qwen3.8-27b-local"]
        command = self.tool._build_command(model, Path("/snap/rev"), Path("/venv/bin/python"))
        server = model.options["server"]
        self.assertEqual(command[:5], ["/venv/bin/python", "-m", "vllm.entrypoints.openai.api_server", "--model", "/snap/rev"])
        self.assertEqual(command[command.index("--max-model-len") + 1], str(server["max_model_len"]))
        self.assertEqual(command[-len(server["extra_args"]):], server["extra_args"])
        self.assertIn("--no-enable-prefix-caching", command)

    def test_probe_failure_spawns_nothing(self):
        model_id = "qwen3.8-27b-local"
        failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="ImportError: vllm")
        with mock.patch.object(self.tool, "snapshot_dir", return_value=Path("/snap/rev")), \
                mock.patch.object(self.tool, "vllm_python", return_value=Path("/venv/bin/python")), \
                mock.patch.object(self.tool.subprocess, "run", return_value=failed), \
                mock.patch.object(self.tool.subprocess, "Popen") as popen:
            code, output = capture(self.tool.main, ["start", "--model", model_id])
        self.assertEqual((code, "판본을 확인할 수 없습니다" in output), (self.tool.EXIT_REFUSED, True))
        popen.assert_not_called()
        self.assertFalse(self.tool.INFO_FILE.exists())

    def test_probe_parses_last_three_tokens(self):
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="INFO some log\n0.30.0\n2.13.0+cu126\n12.6\n", stderr="")
        with mock.patch.object(self.tool.subprocess, "run", return_value=ok):
            self.assertEqual(self.tool._probe_versions(Path("/venv/bin/python"), {}), ("0.30.0", "2.13.0+cu126", "12.6"))

    def test_unknown_model_and_status_without_server(self):
        code, output = capture(self.tool.main, ["start", "--model", "mock-echo"])
        self.assertEqual((code, "local_vllm 모델이 아닙니다" in output), (self.tool.EXIT_REFUSED, True))
        code, output = capture(self.tool.main, ["status"])
        self.assertEqual((code, "기록된 서버가 없습니다" in output), (self.tool.EXIT_RUNTIME_FAILURE, True))
        self.tool.INFO_FILE.write_text('{"pid": 1, "port": 1, "model_id": "한글", "gpu": 0, "vllm_version": "x"}', encoding="utf-8")
        self.assertEqual(self.tool._read_info()["model_id"], "한글")


if __name__ == "__main__":
    unittest.main()
