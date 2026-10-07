"""러너 완료 기준 검사 (계획_실행코드·구조검토_v0.2.md §6).

모의 어댑터만 쓰며 외부 호출이 없다. 출력은 임시 폴더에 쓴다.
실행 (runner/ 폴더에서):  .venv/bin/python -m unittest discover -s tests -v
"""
import json
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from support import CODEBOOK, CONFIG, FAILURE_PLAN, RUNNER_DIR, TAXONOMY, RunnerTestCase   # noqa: F401 (다른 테스트가 이 이름들을 가져간다)

from kyab_runner import cli, csv_io, paths, run_multiturn, run_single, validate    # noqa: E402
from kyab_runner.adapters.mock import MockAdapter                        # noqa: E402
from kyab_runner.session import RUNNER_STAGES                            # noqa: E402


class NormalRunTest(RunnerTestCase):
    """정상 시나리오: 6문항 × 3회."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_cli(run_single)[0], 0)
        self.assertEqual(self.run_cli(run_multiturn)[0], 0)
        self.single_dir, self.multi_dir = self.batch_dirs()

    def test_row_counts(self):
        """04_runs 18행, 05_responses 36행(단일 9 + 3턴 27)."""
        self.assertEqual(len(self.table(self.single_dir, "04_runs")), 9)
        self.assertEqual(len(self.table(self.single_dir, "05_responses")), 9)
        self.assertEqual(len(self.table(self.multi_dir, "04_runs")), 9)
        self.assertEqual(len(self.table(self.multi_dir, "05_responses")), 27)

    def test_header_equals_codebook(self):
        """출력 CSV의 열 이름·순서가 코드북과 완전히 같다 (BOM 포함 원문 확인)."""
        for batch_dir in (self.single_dir, self.multi_dir):
            for table, filename in (("04_runs", "04_runs.csv"), ("05_responses", "05_responses.csv"),
                                    ("06_judgments", "06_judgments_template.csv")):
                raw = (batch_dir / filename).read_bytes()
                self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), f"{filename}: BOM 없음")
                header = raw.decode("utf-8-sig").split("\r\n", 1)[0]
                self.assertEqual(header, ",".join(CODEBOOK.columns(table)))

    def test_every_value_passes_codebook(self):
        """사후 전수 검사: 기록된 모든 값이 코드북 허용값·형식을 통과한다."""
        for batch_dir in (self.single_dir, self.multi_dir):
            for table in ("04_runs", "05_responses"):
                for row in self.table(batch_dir, table):
                    self.assertEqual(CODEBOOK.check_row(table, row, stages=RUNNER_STAGES), [])

    def test_run_fields(self):
        """실행 행의 고정값·해시·판정 의존 필드."""
        for row in self.table(self.multi_dir, "04_runs"):
            params = CONFIG["run_params"]
            self.assertEqual((row["temperature"], row["top_p"], row["max_output_tokens"]),
                             (str(params["temperature"]), str(params["top_p"]), str(params["max_output_tokens"])))
            self.assertEqual(row["max_output_tokens"], "8192")                # 회신 ① + 사용자 결정 2026-10-05
            self.assertEqual(row["system_prompt_hash"], CONFIG.system_prompt_hash)
            self.assertRegex(row["system_prompt_hash"], r"^[0-9a-f]{64}$")
            self.assertEqual((row["first_fail_turn"], row["first_cfc_turn"]), ("", ""))
            self.assertEqual((row["run_status"], row["stop_reason"], row["actual_turn_count"]),
                             ("completed", "planned_end", "3"))
            self.assertEqual(row["protocol_id"], "MT3-1.0.0")

    def test_multiturn_history_is_own_actual_responses(self):
        """3턴 요청에는 같은 실행의 1·2턴 실제 응답이 순서대로 들어 있고, 실행 간에 섞이지 않는다."""
        by_run = {}
        for row in self.table(self.multi_dir, "05_responses"):
            by_run.setdefault(row["run_id"], []).append(row)
        self.assertEqual(len(by_run), 9)
        for rows in by_run.values():
            self.assertEqual(len(rows), 3)
            third = json.loads(rows[2]["request_messages_json"])
            self.assertEqual([m["role"] for m in third], ["system", "user", "assistant", "user", "assistant", "user"])
            self.assertEqual([third[2]["content"], third[4]["content"]],
                             [rows[0]["response_text"], rows[1]["response_text"]])
            first = json.loads(rows[0]["request_messages_json"])
            self.assertEqual([m["role"] for m in first], ["system", "user"])

    def test_only_deliverable_fields_reach_model(self):
        """모델에 간 문자열은 시스템 프롬프트·대본 발화(+context_text)·모델 자신의 응답뿐이다."""
        prompts = csv_io.read_table(CODEBOOK, "03_prompts", paths.DEFAULT_INPUT_DIR / "03_prompts.csv")
        allowed = {CONFIG.system_prompt_text}
        for p in prompts:
            prefix = p["context_text"] + CONFIG["context_separator"] if p["context_text"] else ""
            allowed.add(prefix + p["message_text"])
        for batch_dir in (self.single_dir, self.multi_dir):
            responses = self.table(batch_dir, "05_responses")
            answers = {r["response_text"] for r in responses}
            for row in responses:
                for message in json.loads(row["request_messages_json"]):
                    self.assertEqual(set(message), {"role", "content"})
                    self.assertIn(message["content"], allowed | answers)

    def test_context_text_is_user_prefix(self):
        """context_text가 있는 턴(KYAB-900102 1턴)은 user 메시지 앞에 붙는다."""
        row = next(r for r in self.table(self.multi_dir, "05_responses") if r["turn_id"] == "TURN-90010201")
        user = json.loads(row["request_messages_json"])[-1]["content"]
        self.assertTrue(user.startswith("사용자는 이 AI를"))
        self.assertTrue(user.endswith("학교 애들은 다 나를 이해 못 해."))

    def test_ids_unique_across_batches(self):
        runs = self.table(self.single_dir, "04_runs") + self.table(self.multi_dir, "04_runs")
        responses = self.table(self.single_dir, "05_responses") + self.table(self.multi_dir, "05_responses")
        self.assertEqual(len({r["run_id"] for r in runs}), 18)
        self.assertEqual(len({r["response_id"] for r in responses}), 36)
        self.assertEqual({r["run_batch_id"] for r in runs}, {self.single_dir.name, self.multi_dir.name})

    def test_judgment_template(self):
        """판정 틀: 성공 응답마다 turn 행, 다중턴 실행마다 conversation 행."""
        single = self.table(self.single_dir, "06_judgments", "06_judgments_template.csv")
        multi = self.table(self.multi_dir, "06_judgments", "06_judgments_template.csv")
        self.assertEqual(Counter(r["evaluation_scope"] for r in single), {"turn": 9})
        self.assertEqual(Counter(r["evaluation_scope"] for r in multi), {"turn": 27, "conversation": 9})
        # KYAB-900102는 태그 이력이 2행이고 현재 적용은 2번이다.
        runs = {r["run_id"]: r for r in self.table(self.multi_dir, "04_runs")}
        responses = {r["response_id"]: r for r in self.table(self.multi_dir, "05_responses")}
        revisions = {runs[responses[j["response_id"]]["run_id"]]["item_id"]: j["tag_revision"] for j in multi}
        # 대조 문항 KYAB-900103은 대조 위험군 연결(control_target_risk)을 rev 2로 덧붙였다(회신 ②).
        self.assertEqual(revisions, {"KYAB-900101": "1", "KYAB-900102": "2", "KYAB-900103": "2"})


class FailureScenarioTest(RunnerTestCase):
    """차단·오류·시간초과·빈 응답·길이 절단이 코드북 허용값 조합으로 남는다."""

    def outcomes(self, runner):
        self.assertEqual(self.run_cli(runner, "--mock-plan", str(FAILURE_PLAN))[0], 0)
        batch_dir = self.batch_dirs()[-1]
        runs = {(r["item_id"], r["rollout_no"]): r for r in self.table(batch_dir, "04_runs")}
        responses = {}
        for r in self.table(batch_dir, "05_responses"):
            self.assertEqual(CODEBOOK.check_row("05_responses", r, stages=RUNNER_STAGES), [])
            responses.setdefault(r["run_id"], []).append(r)
        return batch_dir, runs, responses

    def assert_run(self, run, status, stop, turns):
        self.assertEqual((run["run_status"], run["stop_reason"], run["actual_turn_count"]), (status, stop, str(turns)))

    def test_single_turn(self):
        batch_dir, runs, responses = self.outcomes(run_single)
        last = lambda item, rollout: responses[runs[(item, rollout)]["run_id"]][-1]     # noqa: E731

        self.assert_run(runs[("KYAB-900001", "1")], "completed", "planned_end", 1)     # 거절 문장은 success
        self.assertEqual(last("KYAB-900001", "1")["response_status"], "success")
        self.assertEqual(last("KYAB-900001", "2")["finish_reason"], "length")            # 길이 절단
        self.assert_run(runs[("KYAB-900001", "2")], "completed", "planned_end", 1)
        self.assert_run(runs[("KYAB-900001", "3")], "completed", "planned_end", 1)     # 재시도 후 성공

        blocked = last("KYAB-900002", "1")
        self.assertEqual((blocked["response_status"], blocked["block_source"], blocked["finish_reason"],
                          blocked["response_text"]), ("blocked", "provider", "content_filter", ""))
        self.assert_run(runs[("KYAB-900002", "1")], "failed", "provider_block", 0)
        self.assertEqual(last("KYAB-900002", "2")["response_status"], "empty")
        self.assert_run(runs[("KYAB-900002", "2")], "failed", "error", 0)

        error = last("KYAB-900003", "1")
        self.assertEqual((error["response_status"], error["error_code"], error["raw_response_json"]),
                         ("error", "provider_error", "{}"))
        self.assertTrue(error["error_message"])
        self.assertEqual(last("KYAB-900003", "2")["response_status"], "timeout")
        self.assert_run(runs[("KYAB-900003", "2")], "failed", "error", 0)

        # 재시도 내역은 CSV가 아니라 보조 로그에 남는다: flaky 3회, timeout 3회, error 1회.
        events = [json.loads(line) for line in (batch_dir / "runner_events.jsonl").read_text("utf-8").splitlines()]
        attempts = Counter(e["run_id"] for e in events if e["event"] == "turn_attempt")
        self.assertEqual(attempts[runs[("KYAB-900001", "3")]["run_id"]], 3)
        self.assertEqual(attempts[runs[("KYAB-900003", "2")]["run_id"]], 3)
        self.assertEqual(attempts[runs[("KYAB-900003", "1")]["run_id"]], 1)
        # 시도가 여러 번이어도 응답 행은 턴당 1개다.
        self.assertTrue(all(len(rows) == 1 for rows in responses.values()))

    def test_multi_turn(self):
        _, runs, responses = self.outcomes(run_multiturn)
        statuses = lambda item, rollout: [r["response_status"]                           # noqa: E731
                                          for r in responses.get(runs[(item, rollout)]["run_id"], [])]

        self.assert_run(runs[("KYAB-900101", "1")], "partial", "provider_block", 1)    # 2턴 차단에서 중단
        self.assertEqual(statuses("KYAB-900101", "1"), ["success", "blocked"])
        self.assert_run(runs[("KYAB-900101", "2")], "failed", "error", 0)              # 1턴 오류
        self.assertEqual(statuses("KYAB-900101", "2"), ["error"])
        self.assert_run(runs[("KYAB-900102", "1")], "partial", "error", 2)             # 3턴 시간 초과
        self.assertEqual(statuses("KYAB-900102", "1"), ["success", "success", "timeout"])
        self.assert_run(runs[("KYAB-900102", "2")], "completed", "planned_end", 3)     # 재시도 후 성공
        self.assert_run(runs[("KYAB-900103", "1")], "completed", "planned_end", 3)     # 절단돼도 계속
        self.assert_run(runs[("KYAB-900103", "2")], "partial", "error", 2)             # 3턴 빈 응답
        self.assert_run(runs[("KYAB-900101", "3")], "completed", "planned_end", 3)     # 규칙 없는 실행은 정상


class PreflightTest(RunnerTestCase):
    """실행 전 점검(회신 ①): run_params 허용값과 서버 길이. 실패하면 모델을 부르지 않고 폴더도 남기지 않는다."""

    def config_with(self, **run_params):
        from dataclasses import replace
        return replace(CONFIG, raw={**CONFIG.raw, "run_params": {**CONFIG["run_params"], **run_params}})

    def plan(self, **plan):
        import yaml
        path = self.tmp / "plan.yaml"
        path.write_text(yaml.safe_dump({"default": "normal", **plan}), encoding="utf-8")
        return path

    def test_run_params_must_match_codebook_enum(self):
        """overlay OV-R1005-1 허용값 [8192]. 1024로 두면 모델 호출 전에 멈추고 출력 폴더가 생기지 않는다."""
        problems = cli.check_run_params(CODEBOOK, self.config_with(max_output_tokens=1024))
        self.assertEqual(len(problems), 1)
        self.assertIn("max_output_tokens=1024", problems[0])
        self.assertIn("허용값 아님", problems[0])
        self.assertEqual(cli.check_run_params(CODEBOOK, CONFIG), [])
        from unittest import mock
        with mock.patch.object(cli, "load_config", return_value=self.config_with(max_output_tokens=1024)):
            code, output = self.run_cli(run_single)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("실행 전 점검 실패", output)
        self.assertFalse(self.out.exists())

    def test_missing_run_params_block_is_a_problem_not_a_traceback(self):
        from dataclasses import replace
        for raw in ({k: v for k, v in CONFIG.raw.items() if k != "run_params"}, {**CONFIG.raw, "run_params": None}):
            problems = cli.check_run_params(CODEBOOK, replace(CONFIG, raw=raw))
            self.assertTrue(problems and "run_params 블록" in problems[0], problems)

    def test_run_params_must_be_complete_numeric_and_exact(self):
        """키가 빠지거나 모르는 키가 있거나 문자열이면 문제. 고정값은 04에 적힐 문자열과 정확히 대조(temperature: 0 ≠ '0.0')."""
        from dataclasses import replace
        def with_params(params):
            return replace(CONFIG, raw={**CONFIG.raw, "run_params": params})
        base = dict(CONFIG["run_params"])
        self.assertTrue(any("top_p이 없음" in p for p in cli.check_run_params(CODEBOOK, with_params({k: v for k, v in base.items() if k != "top_p"}))))
        self.assertTrue(any("모르는 키 'max_tokens'" in p for p in cli.check_run_params(CODEBOOK, with_params({**base, "max_tokens": 1}))))
        self.assertTrue(any("수가 아님" in p for p in cli.check_run_params(CODEBOOK, with_params({**base, "max_output_tokens": "8192"}))))
        self.assertTrue(any("temperature" in p and "0.0" in p for p in cli.check_run_params(CODEBOOK, with_params({**base, "temperature": 0}))))
        self.assertEqual(cli.check_run_params(CODEBOOK, with_params(base)), [])
        # 명령행에서는 모델 호출 없이 종료 2
        from unittest import mock
        with mock.patch.object(cli, "load_config", return_value=with_params({k: v for k, v in base.items() if k != "top_p"})):
            code, output = self.run_cli(run_single)
        self.assertEqual((code, "top_p이 없음" in output, self.out.exists()), (cli.EXIT_INVALID_INPUT, True, False))
        with mock.patch.object(cli, "load_config", return_value=with_params({**base, "temperature": 0})):
            code, output = self.run_cli(run_single, "--validate-only")
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)                  # --validate-only도 점검

    def test_fixed_value_check_survives_without_overlay(self):
        """overlay를 지우면 enum이 없다. 그래도 형식 원문('1024로 고정')과 설정(8192)이 다르면 걸러낸다."""
        from kyab_runner.codebook import load_codebook
        bare = load_codebook(TAXONOMY, overlay_yaml=self.tmp / "none.yaml")
        problems = cli.check_run_params(bare, CONFIG)
        self.assertTrue(any("1024로 고정" in p and "8192" in p for p in problems), problems)
        self.assertEqual(cli.check_run_params(bare, self.config_with(max_output_tokens=1024)), [])
        # 고정값 검사는 temperature·top_p에도 적용된다
        self.assertTrue(any("top_p" in p for p in cli.check_run_params(CODEBOOK, self.config_with(top_p=0.9))))

    def test_context_budget_refuses_short_server_even_for_single_turn(self):
        """vLLM 규칙: 입력 상한 = max_model_len − max_tokens. 서버 8192 + 한도 8192는 1턴도 입력 상한 0으로 거부."""
        code, output = self.run_cli(run_single, "--mock-plan", str(self.plan(max_model_len=8192)))
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("입력 상한 0 토큰", output)
        self.assertIn("ST1-1.0.0", output)
        self.assertFalse(self.out.exists())
        # 그 뒤 정상 실행은 첫 배치 번호(001)를 받는다 — 거부가 번호를 소비하지 않음
        self.assertEqual(self.run_cli(run_single, "--mock-plan", str(self.plan(max_model_len=32768)))[0], 0)
        self.assertEqual(self.batch_dirs()[0].name[-3:], "001")

    def test_context_budget_boundary(self):
        """MT3: 필요 입력 = 2 × 8192 + 1024 = 17408. max_model_len 8192 + 17408 = 25600은 통과, 1 모자라면 거부."""
        limit, reserve = CONFIG["run_params"]["max_output_tokens"], CONFIG["context_reserve_tokens"]
        exact = limit + 2 * limit + reserve
        self.assertEqual(exact, 25600)
        self.assertEqual(self.run_cli(run_multiturn, "--mock-plan", str(self.plan(max_model_len=exact)))[0], 0)
        code, output = self.run_cli(run_multiturn, "--mock-plan", str(self.plan(max_model_len=exact - 1)))
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn(f"{exact - 1}", output)
        self.assertIn("MT3-1.0.0", output)
        self.assertEqual(len(self.batch_dirs()), 1)
        # describe()에 max_model_len이 없는 어댑터(모의 기본·상용)는 점검하지 않는다
        self.assertEqual(self.run_cli(run_multiturn)[0], 0)

    def test_manifest_locks_run_params(self):
        """manifest에 run_params가 기록되고 이어 쓰기 잠금 대상이다. 옛 manifest(키 없음)는 04 행의 값으로 비교한다."""
        self.assertEqual(self.run_cli(run_single, "--rollouts", "1")[0], 0)
        batch_dir = self.batch_dirs()[0]
        manifest = json.loads((batch_dir / cli.MANIFEST_FILE).read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_params"], {"temperature": "0.0", "top_p": "1.0", "max_output_tokens": "8192"})
        # 옛 형식: 키를 지워도 04 행(8192)과 설정이 같아 이어 쓸 수 있다
        del manifest["run_params"]
        (batch_dir / cli.MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.run_cli(run_single, "--batch-id", batch_dir.name)[0], 0)
        # 다른 조건으로 기록된 배치에는 이어 쓸 수 없다
        manifest["run_params"] = {"temperature": "0.0", "top_p": "1.0", "max_output_tokens": "1024"}
        (batch_dir / cli.MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        code, output = self.run_cli(run_single, "--batch-id", batch_dir.name)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("run_params", output)

    def test_manifest_records_input_validation(self):
        """입력 검증 결과(경고·제외 사유·제외 문항)가 batch_manifest.json에 남는다(코드북 CSV는 그대로, 잠금 대상 아님)."""
        self.assertEqual(self.run_cli(run_single, "--items", "KYAB-900001", "--rollouts", "1")[0], 0)
        manifest = json.loads((self.batch_dirs()[0] / cli.MANIFEST_FILE).read_text(encoding="utf-8"))
        record = manifest["input_validation"]
        self.assertEqual((record["errors"], record["warnings"]), (0, 0))
        self.assertEqual(record["excluded"], {"다른 프로토콜": 3, "--items에 없음": 2})
        self.assertEqual(record["selected_items"], ["KYAB-900001"])
        self.assertEqual(sorted(record["excluded_items"]), ["KYAB-900002", "KYAB-900003", "KYAB-900101", "KYAB-900102", "KYAB-900103"])
        self.assertNotIn("input_validation", cli._MANIFEST_LOCKED)

    def test_summary_reports_truncated_responses(self):
        """출력 한도에서 잘린 응답(finish_reason=length) 건수를 실행 요약에 보인다."""
        code, output = self.run_cli(run_single, "--mock-plan", str(FAILURE_PLAN))
        self.assertEqual(code, 0)
        self.assertIn("잘림(length)    1건", output)


class ResumeTest(RunnerTestCase):
    """중단 후 재실행해도 행이 중복되지 않는다."""

    def test_resume_adds_only_missing_runs(self):
        self.assertEqual(self.run_cli(run_multiturn, "--rollouts", "1")[0], 0)
        batch_dir = self.batch_dirs()[0]
        self.assertEqual(len(self.table(batch_dir, "04_runs")), 3)

        self.assertEqual(self.run_cli(run_multiturn, "--batch-id", batch_dir.name)[0], 0)
        self.assertEqual(self.run_cli(run_multiturn, "--batch-id", batch_dir.name)[0], 0)     # 한 번 더: 추가 없음
        runs, responses = self.table(batch_dir, "04_runs"), self.table(batch_dir, "05_responses")
        self.assertEqual((len(runs), len(responses)), (9, 27))
        self.assertEqual(len({(r["item_id"], r["rollout_no"]) for r in runs}), 9)
        self.assertEqual(len({r["response_id"] for r in responses}), 27)
        self.assertEqual(len(self.batch_dirs()), 1)

    def test_interrupt_records_manual_stop_and_resumes(self):
        """실행 도중 사람이 멈추면 그 실행은 manual_stop으로 남고, 이어 실행하면 나머지만 채운다."""
        calls = {"n": 0}
        original = MockAdapter.complete

        def interrupt_on_fifth_call(adapter, messages, params, call_info):
            calls["n"] += 1
            if calls["n"] == 5:                     # 두 번째 실행의 2턴
                raise KeyboardInterrupt
            return original(adapter, messages, params, call_info)

        MockAdapter.complete = interrupt_on_fifth_call
        try:
            code, _ = self.run_cli(run_multiturn)
        finally:
            MockAdapter.complete = original
        self.assertEqual(code, cli.EXIT_INTERRUPTED)
        batch_dir = self.batch_dirs()[0]
        runs = self.table(batch_dir, "04_runs")
        self.assertEqual([(r["run_status"], r["stop_reason"], r["actual_turn_count"]) for r in runs],
                         [("completed", "planned_end", "3"), ("partial", "manual_stop", "1")])

        self.assertEqual(self.run_cli(run_multiturn, "--batch-id", batch_dir.name)[0], 0)
        runs = self.table(batch_dir, "04_runs")
        self.assertEqual(len(runs), 9)
        self.assertEqual(len({(r["item_id"], r["rollout_no"]) for r in runs}), 9)

    def test_orphan_responses_are_discarded(self):
        """응답만 쓰고 죽은 실행의 흔적은 재시작 때 걷어낸다."""
        self.assertEqual(self.run_cli(run_single, "--rollouts", "1")[0], 0)
        batch_dir = self.batch_dirs()[0]
        orphan = dict(self.table(batch_dir, "05_responses")[0], response_id="RESP-00009999",
                      run_id="RUN-20260101-000001")
        csv_io.append_rows(CODEBOOK, "05_responses", batch_dir / "05_responses.csv", [orphan])

        self.assertEqual(self.run_cli(run_single, "--rollouts", "1", "--batch-id", batch_dir.name)[0], 0)
        responses = self.table(batch_dir, "05_responses")
        self.assertEqual(len(responses), 3)
        self.assertNotIn("RESP-00009999", {r["response_id"] for r in responses})

    def test_resume_refuses_changed_input(self):
        self.assertEqual(self.run_cli(run_single, "--rollouts", "1")[0], 0)
        batch_dir = self.batch_dirs()[0]
        input_dir, tables = self.copy_inputs()
        tables["03_prompts"][0]["message_text"] += " (수정)"
        self.save(input_dir, "03_prompts", tables["03_prompts"])
        code, output = self.run_cli(run_single, "--batch-id", batch_dir.name, input_dir=input_dir)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("input_sha256", output)


class ValidationTest(RunnerTestCase):
    """입력 검증이 잘못된 입력을 막는다. 각 경우 모델 호출 전에 종료 코드 2로 끝난다."""

    def assert_rejected(self, table, mutate, expected_text):
        input_dir, tables = self.copy_inputs()
        mutate(tables[table])
        self.save(input_dir, table, tables[table])
        code, output = self.run_cli(run_multiturn, input_dir=input_dir)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT, output)
        self.assertIn(expected_text, output)
        self.assertFalse(self.out.exists(), "검증 실패인데 출력이 생김")

    @staticmethod
    def current_tag(rows, item_id):
        return next(r for r in rows if r["item_id"] == item_id and r["tag_status"] == "current")

    def test_non_integer_turn_index_is_a_validation_error(self):
        """R01: 색인(turn_index int 정렬)보다 검증이 먼저 돌아 traceback·종료 1이 아니라 '[error] … int 형식 아님'·종료 2."""
        def mutate(rows):
            rows[0]["turn_index"] = "x"
        self.assert_rejected("03_prompts", mutate, "int 형식 아님: 'x'")
        input_dir, tables = self.copy_inputs() if not (self.tmp / "input").exists() else (self.tmp / "input", None)
        for bad, expected in (("", "필수인데 비어 있음"), ("1.0", "int 형식 아님"), ("x", "int 형식 아님: 'x'")):
            rows = csv_io.read_table(CODEBOOK, "03_prompts", input_dir / cli.INPUT_FILES["03_prompts"])
            rows[0]["turn_index"] = bad
            self.save(input_dir, "03_prompts", rows)
            for runner in (run_single, run_multiturn):
                code, output = self.run_cli(runner, "--validate-only", input_dir=input_dir)
                self.assertEqual(code, cli.EXIT_INVALID_INPUT, (bad, output))
                self.assertIn(expected, output)
                self.assertNotIn("Traceback", output)

    def test_sub_risk_must_be_child_of_primary(self):
        def mutate(rows):
            self.current_tag(rows, "KYAB-900101")["sub_risk_codes"] = '["A4.01"]'
        self.assert_rejected("02_item_tags", mutate, "A4.01는 A5의 소분류가 아님")

    def test_only_one_sub_risk(self):
        def mutate(rows):
            self.current_tag(rows, "KYAB-900101")["sub_risk_codes"] = '["A5.01", "A5.02"]'
        self.assert_rejected("02_item_tags", mutate, "최대 1개")

    def test_legacy_code_rejected_in_new_taxonomy_row(self):
        def mutate(rows):
            self.current_tag(rows, "KYAB-900101")["primary_risk"] = "R5"
        self.assert_rejected("02_item_tags", mutate, "허용값 아님: 'R5'")

    def test_m_review_must_be_blank_in_new_taxonomy_row(self):
        def mutate(rows):
            self.current_tag(rows, "KYAB-900101")["m_review_status"] = "no_issue"
        self.assert_rejected("02_item_tags", mutate, "새 분류체계 행에서는 비워 둠")

    def test_mapped_requires_primary_risk(self):
        def mutate(rows):
            self.current_tag(rows, "KYAB-900101").update(primary_risk="", sub_risk_codes="[]")
        self.assert_rejected("02_item_tags", mutate, "risk_review_status=mapped인데 비어 있음")

    def test_exactly_one_current_tag(self):
        def mutate(rows):
            for row in rows:
                if row["item_id"] == "KYAB-900102":
                    row["tag_status"] = "current"
        self.assert_rejected("02_item_tags", mutate, "current 행이 2개")

    def test_turns_must_be_contiguous(self):
        def mutate(rows):
            rows[:] = [r for r in rows if r["turn_id"] != "TURN-90010102"]
        self.assert_rejected("03_prompts", mutate, "턴 번호 [1, 3]")

    def test_protocol_must_match_mode(self):
        def mutate(rows):
            next(r for r in rows if r["item_id"] == "KYAB-900101")["protocol_id"] = "ST1-1.0.0"
        self.assert_rejected("01_items", mutate, "ST1-1.0.0은 (conversation_mode, planned_round_count)")

    def test_old_protocol_notation_rejected(self):
        def mutate(rows):
            next(r for r in rows if r["item_id"] == "KYAB-900101")["protocol_id"] = "MT3-v1"
        self.assert_rejected("01_items", mutate, "허용값 아님: 'MT3-v1'")

    def test_original_text_required_for_sourced_item(self):
        def mutate(rows):
            next(r for r in rows if r["item_id"] == "KYAB-900001")["original_text"] = ""
        self.assert_rejected("01_items", mutate, "외부 원천 문항인데 원문이 없음")

    def test_extra_column_rejected(self):
        """코드북에 없는 열이 있는 입력은 읽지 않는다."""
        input_dir, _ = self.copy_inputs()
        path = input_dir / "03_prompts.csv"
        lines = path.read_bytes().decode("utf-8-sig").split("\r\n")
        widened = [lines[0] + ",latency_ms"] + [line + "," for line in lines[1:] if line]
        path.write_bytes("\r\n".join(widened).encode("utf-8"))
        code, output = self.run_cli(run_multiturn, input_dir=input_dir)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("코드북에 없는 열 ['latency_ms']", output)

    def test_ragged_row_rejected(self):
        """셀 수가 열 수와 다른 행(따옴표·쉼표가 깨진 CSV)은 읽지 않는다."""
        input_dir, _ = self.copy_inputs()
        path = input_dir / "03_prompts.csv"
        path.write_bytes(path.read_bytes() + "TURN-99999999,KYAB-900101\r\n".encode("utf-8"))
        code, output = self.run_cli(run_multiturn, input_dir=input_dir)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("셀 2개 (열은 7개)", output)

    def test_control_target_risk_only_on_control_items(self):
        """대조 위험군 연결(OV-P4 잠정): 위험 문항에 값이 있으면 오류."""
        self.assert_rejected("02_item_tags", lambda rows: self.current_tag(rows, "KYAB-900001").update(control_target_risk="A8"),
                             "대조 문항(safe_control)이 아닌데")

    def test_control_target_risk_must_be_a_major_code(self):
        """새 체계 행의 연결값은 A1~A10만."""
        self.assert_rejected("02_item_tags", lambda rows: self.current_tag(rows, "KYAB-900002").update(control_target_risk="R1"),
                             "허용값 아님")

    def test_control_without_link_is_warning_unless_required(self):
        """대조 문항의 연결이 비면 경고만(실행 진행). runner.yaml control_link_required가 true면 오류."""
        input_dir, tables = self.copy_inputs()
        self.current_tag(tables["02_item_tags"], "KYAB-900103").update(control_target_risk="")
        self.save(input_dir, "02_item_tags", tables["02_item_tags"])
        code, output = self.run_cli(run_multiturn, input_dir=input_dir)
        self.assertEqual(code, 0, output)
        self.assertIn("[warning] 02_item_tags KYAB-900103@1.0.0#rev2 control_target_risk", output)
        strict = {**CONFIG.raw, "control_link_required": True}
        issues = validate.validate_inputs(CODEBOOK, TAXONOMY, strict, *tables.values())
        self.assertTrue(any(i.level == "error" and i.field == validate.CONTROL_TARGET_FIELD for i in issues))

    def test_legacy_control_link_uses_r_codes(self):
        """이전 체계(MAJOR 0) 행의 대조 연결값은 R1~R5. A 코드는 오류."""
        input_dir, tables = self.copy_inputs()
        legacy = next(r for r in tables["02_item_tags"] if r["item_id"] == "KYAB-900002" and r["tag_revision"] == "1")
        legacy.update(taxonomy_version="0.7.0", m_review_status="no_issue", control_target_risk="R2")
        self.assertEqual(validate.errors_of(validate.validate_inputs(CODEBOOK, TAXONOMY, CONFIG, *tables.values())), [])
        legacy.update(control_target_risk="A2")
        issues = validate.validate_inputs(CODEBOOK, TAXONOMY, CONFIG, *tables.values())
        self.assertTrue(any(i.field == validate.CONTROL_TARGET_FIELD and "R 코드가 아님" in i.message for i in issues))

    def test_missing_control_column_in_codebook_is_reported_not_raised(self):
        """코드북(overlay)에 열이 없으면 traceback이 아니라 메시지 있는 오류."""
        from kyab_runner.codebook import load_codebook
        bare = load_codebook(TAXONOMY, overlay_yaml=self.tmp / "no_overlay.yaml")
        _, tables = self.copy_inputs()
        tables["02_item_tags"] = [{k: v for k, v in r.items() if k != validate.CONTROL_TARGET_FIELD} for r in tables["02_item_tags"]]
        issues = validate.validate_inputs(bare, TAXONOMY, CONFIG, *tables.values())
        self.assertTrue(any(i.field == validate.CONTROL_TARGET_FIELD and "열이 없음" in i.message for i in issues))

    def test_old_29_column_tags_header_is_read_with_warning(self):
        """다른 팀이 코드북 원본 머리글(29열)로 만든 02도 읽힌다: 새 열은 공란, 경고. 실행은 진행(연결 공란은 경고)."""
        import contextlib
        import io
        input_dir, tables = self.copy_inputs()
        path = input_dir / cli.INPUT_FILES["02_item_tags"]
        columns = [c for c in CODEBOOK.columns("02_item_tags") if c != validate.CONTROL_TARGET_FIELD]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            import csv as _csv
            writer = _csv.writer(f)
            writer.writerow(columns)
            writer.writerows([r[c] for c in columns] for r in tables["02_item_tags"])
        self.assertEqual(len(columns), 29)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rows = csv_io.read_table(CODEBOOK, "02_item_tags", path)
        self.assertIn("옛 머리글(29열)", err.getvalue())
        self.assertTrue(all(r[validate.CONTROL_TARGET_FIELD] == "" for r in rows))
        self.assertEqual(list(rows[0]), CODEBOOK.columns("02_item_tags"))
        with contextlib.redirect_stderr(io.StringIO()):
            code, output = self.run_cli(run_multiturn, input_dir=input_dir)
        self.assertEqual(code, 0, output)
        # 쓰기는 항상 새 머리글이다: 옛 머리글로 읽은 02를 다시 쓰면 30열(control_target_risk 포함)
        rewritten = input_dir / "02_rewritten.csv"
        csv_io.rewrite_table(CODEBOOK, "02_item_tags", rewritten, rows)
        header = rewritten.read_bytes().decode("utf-8-sig").split("\r\n", 1)[0]
        self.assertEqual(header, ",".join(CODEBOOK.columns("02_item_tags")))
        self.assertEqual(len(header.split(",")), 30)
        # 다른 열이 빠진 머리글은 여전히 거부
        with self.assertRaises(csv_io.CsvFormatError):
            csv_io.read_table(CODEBOOK, "02_item_tags", input_dir / cli.INPUT_FILES["01_items"])

    def test_unverified_items_need_flag(self):
        """검토 미통과 문항은 --allow-unverified 없이는 실행되지 않는다."""
        code, _ = self.capture(run_single.main, ["--out", str(self.out)])
        self.assertEqual(code, cli.EXIT_NOTHING_TO_RUN)
        self.assertFalse(self.out.exists())

    def test_broken_overlay_ends_with_message_and_exit_code_2(self):
        """overlay·설정 오류는 traceback이 아니라 한 줄 메시지 + 종료 코드 2(EXIT_NOTHING_TO_RUN=1과 구분)."""
        from unittest import mock
        from kyab_runner.codebook import OverlayError
        with mock.patch.object(cli, "load_codebook", side_effect=OverlayError("시험용 overlay 오류")):
            code, output = self.run_cli(run_single)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("명세·설정을 읽을 수 없습니다", output)
        self.assertIn("시험용 overlay 오류", output)
        from kyab_runner.config import ConfigError
        with mock.patch.object(cli, "load_config", side_effect=ConfigError("시험용 설정 오류")):
            code, output = self.run_cli(run_single)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertFalse(self.out.exists())

    def test_disabled_model_refused(self):
        code, output = self.run_cli(run_single, "--model", "gpt-5.6-terra")
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("enabled: false", output)


class ManifestLockTest(RunnerTestCase):
    """이어 쓰기 잠금: manifest에서 잠금 키가 빠져 있어도 KeyError가 아니라 불일치 메시지와 종료 2(core-10)."""

    def test_missing_locked_key_is_reported_not_crash(self):
        self.assertEqual(self.run_cli(run_single)[0], 0)
        batch_dir = self.batch_dirs()[0]
        manifest = json.loads((batch_dir / cli.MANIFEST_FILE).read_text(encoding="utf-8"))
        del manifest["model_id"]
        (batch_dir / cli.MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        code, output = self.run_cli(run_single, "--batch-id", batch_dir.name)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("model_id: 저장 None", output)


class PreflightArgsTest(RunnerTestCase):
    """실행 전 점검(core-01·05, adapters-06): 잘못된 인자·어댑터 문제는 모델 호출·폴더 생성 없이 종료 2."""

    def assert_refused(self, *extra, text):
        from kyab_runner import ids
        before = ids.batch_dirs(self.out)
        code, output = self.run_cli(run_single, *extra)
        self.assertEqual(code, cli.EXIT_INVALID_INPUT, output)
        self.assertIn(text, output)
        self.assertEqual(ids.batch_dirs(self.out), before)            # 새 배치 폴더가 생기지 않는다

    def test_rollouts_outside_codebook_values(self):
        self.assert_refused("--rollouts", "4", text="rollout_no 허용값")
        self.assert_refused("--rollouts", "-1", text="1 이상")

    def test_batch_id_must_have_manifest(self):
        (self.out / "foo").mkdir(parents=True)
        self.assert_refused("--batch-id", "foo", text="--batch-id")
        (self.out / "RBATCH-20260101-001").mkdir()
        self.assert_refused("--batch-id", "RBATCH-20260101-001", text="manifest")
        self.assertEqual(list((self.out / "RBATCH-20260101-001").iterdir()), [])     # 폴더는 손대지 않음

    def test_mock_plan_problems_are_preflight_errors(self):
        self.assert_refused("--mock-plan", str(self.tmp / "missing.yaml"), text="어댑터를 만들 수 없습니다")
        self.assert_refused("--mock-scenario", "bogus", text="알 수 없는 모의 시나리오")

    def test_protocol_mode_mismatch_exits_2(self):
        code, output = self.run_cli(run_single, "--protocol", "MT3-1.0.0")
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("conversation_mode=single", output)
        self.assertFalse(self.out.exists())

    def test_adapter_setup_error_is_preflight_error(self):
        from unittest import mock
        from kyab_runner.adapters.base import AdapterSetupError
        with mock.patch.object(cli, "create_adapter", side_effect=AdapterSetupError("서버 없음")):
            self.assert_refused(text="서버 없음")

    def test_corrupt_sibling_batch_is_reported(self):
        self.assertEqual(self.run_cli(run_single, "--rollouts", "1")[0], 0)
        batch_dir = self.batch_dirs()[0]
        (batch_dir / "04_runs.csv").write_text("run_id,bogus\r\nx,y\r\n", encoding="utf-8")
        code, output = self.run_cli(run_single, "--rollouts", "1")
        self.assertEqual(code, cli.EXIT_INVALID_INPUT)
        self.assertIn("기존 배치 기록을 읽을 수 없습니다", output)
        self.assertEqual(len(self.batch_dirs()), 1)

    def test_items_are_stripped_and_unknown_ids_warned(self):
        code, output = self.run_cli(run_single, "--items", "KYAB-900001, NOPE", "--rollouts", "1")
        self.assertEqual(code, 0, output)
        self.assertIn("경고: --items에 있으나 01_items에 없는 ID ['NOPE']", output)
        self.assertIn("실행 대상 1문항", output)

    def test_items_without_ids_is_an_error_not_a_full_run(self):
        """R02: 쉼표·공백뿐인 --items는 '필터 없음'이 아니라 종료 2 — 전체 문항이 조용히 실행되지 않는다."""
        for value in (",", " ", ", ,"):
            code, output = self.run_cli(run_single, "--items", value, "--rollouts", "1")
            self.assertEqual(code, cli.EXIT_INVALID_INPUT, (value, output))
            self.assertIn("--items에 문항 ID가 없습니다", output)
            self.assertNotIn("실행 대상", output)
        self.assertFalse(self.out.exists(), "거부했는데 배치가 생김")
        self.assertIsNone(cli.parse_items(None))
        self.assertIsNone(cli.parse_items(""))                 # 빈 문자열은 옛 동작대로 필터 없음
        self.assertEqual(cli.parse_items(" a , b"), {"a", "b"})


class RecordGuardTest(RunnerTestCase):
    """기록 단계 보호(core-04·15): 04가 거부되면 05도 쓰이지 않고, 옛 머리글 파일에는 이어 쓰지 않는다."""

    def test_rejected_run_row_leaves_no_orphan_responses(self):
        from kyab_runner.session import RunSession
        from kyab_runner.adapters.mock import MockAdapter
        from kyab_runner.ids import IdAllocator
        from kyab_runner.session import Batch
        from kyab_runner.records import InputIndex
        index = InputIndex.load(CODEBOOK, paths.DEFAULT_INPUT_DIR)
        item = next(i for i in index.tables["01_items"] if i["conversation_mode"] == "single")
        batch_dir = self.out / "RBATCH-20260101-001"
        batch_dir.mkdir(parents=True)
        model = CONFIG.models["mock-echo"]
        batch = Batch(codebook=CODEBOOK, config=CONFIG, model=model, adapter=MockAdapter(model, {}),
                      ids=IdAllocator(CODEBOOK, self.out, "20260101"), batch_dir=batch_dir, run_batch_id=batch_dir.name,
                      dataset_version=item["dataset_version"], protocol_id=item["protocol_id"], library_version="runner-0.0.0+test")
        session = RunSession(batch, item, 4)                 # rollout_no 4는 코드북 허용값 밖 → 04 거부
        turn = index.turns[validate.item_key(item)][0]
        session.turn(turn, [{"role": "user", "content": turn["message_text"]}])
        with self.assertRaises(csv_io.CsvFormatError):
            session.finish(1)
        self.assertFalse((batch_dir / "05_responses.csv").exists())

    def test_append_refuses_old_header(self):
        path = self.tmp / "02_item_tags.csv"
        old_columns = [c for c in CODEBOOK.columns("02_item_tags") if c != validate.CONTROL_TARGET_FIELD]
        path.write_text(",".join(old_columns) + "\r\n", encoding="utf-8-sig")
        before = path.read_bytes()
        row = dict.fromkeys(CODEBOOK.columns("02_item_tags"), "")
        with self.assertRaises(csv_io.CsvFormatError) as caught:
            csv_io.append_rows(CODEBOOK, "02_item_tags", path, [row])
        self.assertIn("옛 머리글", str(caught.exception))
        self.assertEqual(path.read_bytes(), before)


class MessagesTest(RunnerTestCase):
    """요청 메시지(core-11): context_text는 user 메시지 앞에 붙고, 지원하지 않는 위치는 즉시 오류."""

    def test_context_prefix_and_unsupported_position(self):
        from kyab_runner import messages
        turn = {"context_text": "맥락", "message_text": "질문"}
        built = messages.build_messages(CONFIG, [], turn)
        self.assertEqual(built[-1]["content"], "맥락" + CONFIG["context_separator"] + "질문")
        bad = type(CONFIG)(raw={**CONFIG.raw, "context_position": "system"}, system_prompt_text="s", system_prompt_hash="h", models={})
        with self.assertRaises(ValueError):
            messages.build_messages(bad, [], turn)


class ConfigCheckTest(RunnerTestCase):
    """설정 로드 검사(core-02, codebook-13): 빠진 키·잘못된 종류·모르는 모델 키는 ConfigError(한 줄, 종료 2)."""

    def load_with(self, mutate_runner=None, mutate_models=None):
        import yaml
        from kyab_runner.config import load_config
        config_dir = Path(tempfile.mkdtemp(prefix="config_", dir=self.tmp)) / "config"
        shutil.copytree(paths.CONFIG_DIR, config_dir)
        for name, mutate in (("runner.yaml", mutate_runner), ("models.yaml", mutate_models)):
            if mutate:
                raw = yaml.safe_load((config_dir / name).read_text(encoding="utf-8"))
                mutate(raw)
                (config_dir / name).write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return load_config(config_dir)

    def test_runner_yaml_problems(self):
        from kyab_runner.config import ConfigError
        self.load_with()                                      # 실제 설정은 통과
        cases = (lambda r: r.pop("safety_profile"), lambda r: r.update(max_attempts_per_turn=0),
                 lambda r: r.update(context_position="system"), lambda r: r.update(default_rollouts="3"),
                 lambda r: r.update(timezone="Mars/Olympus"))
        for mutate in cases:
            with self.assertRaises(ConfigError):
                self.load_with(mutate_runner=mutate)

    def test_models_yaml_problems(self):
        from kyab_runner.config import ConfigError
        with self.assertRaises(ConfigError) as caught:
            self.load_with(mutate_models=lambda m: m["models"]["mock-echo"].update(enabeld=True))
        self.assertIn("enabeld", str(caught.exception))
        with self.assertRaises(ConfigError):
            self.load_with(mutate_models=lambda m: m["models"]["mock-echo"].pop("provider"))
        with self.assertRaises(ConfigError) as caught:
            self.load_with(mutate_models=lambda m: m["models"]["mock-echo"].update(enabled="false"))
        self.assertIn("true/false", str(caught.exception))


if __name__ == "__main__":                            # 파일 맨 끝에 둔다 — 뒤에 클래스가 있으면 직접 실행 때 조용히 빠진다(R20)
    unittest.main()
