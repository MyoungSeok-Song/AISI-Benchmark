"""판정 입출력 검사 (과제 5 1부): 판정 입력, 06 검증 교차 규칙, 주 판정 집합, 사후 산출, 모의 판정기.

모의 어댑터·모의 판정기만 쓰며 외부 호출이 없다. 출력은 임시 폴더에 쓴다.
실행 (runner/ 폴더에서):  .venv/bin/python -m unittest discover -s tests -v
"""
import contextlib
import copy
import io
import json
import sys
from collections import Counter

from test_runner import CODEBOOK, FAILURE_PLAN, RUNNER_DIR, RunnerTestCase

from kyab_runner import csv_io, ids, judge_io, paths, run_judge, run_multiturn, run_single, validate   # noqa: E402
from kyab_runner.context import load_environment, open_views                                    # noqa: E402
from kyab_runner.judges import create_judge                                                     # noqa: E402
from kyab_runner.rules import Rules, RulesError, load_rules                                     # noqa: E402

sys.path.insert(0, str(RUNNER_DIR / "tools"))
import apply_judgments                                                                          # noqa: E402

ENV = load_environment()
RULES = ENV.rules
SCORE_FIELDS = RULES.score_fields
CRRI = RULES.crri_axes


class JudgedTestCase(RunnerTestCase):
    """정상 배치 2개(단일 9실행, 3턴 9실행)를 만들고 모의 판정기로 판정해 둔다."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_cli(run_single)[0], 0)
        self.assertEqual(self.run_cli(run_multiturn)[0], 0)
        self.single_dir, self.multi_dir = self.batch_dirs()
        self.assertEqual(self.judge()[0], 0)
        _, (self.single, self.multi), _ = open_views(ENV, paths.DEFAULT_INPUT_DIR, [self.single_dir, self.multi_dir])

    def batch_dirs(self):
        """배치 폴더만 (출력 루트에는 집계 결과 폴더 RESULTS-…도 생긴다)."""
        return ids.batch_dirs(self.out)

    def judge(self, *extra, batches=None):
        """run_judge를 돌리고 (종료 코드, 화면 출력)을 돌려준다."""
        return self.capture(run_judge.main, [*extra, *map(str, batches or self.batch_dirs())])

    def capture(self, main, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                code = main(argv)
            except SystemExit as exc:
                code, _ = 99, buffer.write(str(exc.code))
        return code, buffer.getvalue()

    def judgments(self, batch_dir):
        return judge_io.load_judgments(CODEBOOK, batch_dir)

    def errors(self, view, rows, **kwargs):
        """검증 오류를 (필드, 메시지) 목록으로."""
        issues = judge_io.validate_judgments(CODEBOOK, RULES, view, rows, **kwargs)
        return [(i.field, i.message) for i in validate.errors_of(issues)]

    def assert_error(self, view, rows, field, fragment=""):
        found = [(f, m) for f, m in self.errors(view, rows) if f == field and fragment in m]
        self.assertTrue(found, f"{field} 오류가 없음: {self.errors(view, rows)}")

    def pick(self, view, rows, scope="turn", item_id=None):
        """조건에 맞는 첫 판정 행의 (위치, 복사본)."""
        for position, row in enumerate(rows):
            run = view.run_of(view.responses[row["response_id"]])
            if row["evaluation_scope"] == scope and item_id in (None, run["item_id"]):
                return position, dict(row)
        raise AssertionError("조건에 맞는 판정 행이 없음")

    def mutated(self, view, rows, scope="turn", item_id=None, **changes):
        """한 행만 바꾼 판정 목록."""
        position, row = self.pick(view, rows, scope, item_id)
        row.update(changes)
        return rows[:position] + [row] + rows[position + 1:]


class JudgeInputsTest(JudgedTestCase):
    """판정 입력 묶음 (작업 지침 1-1)."""

    def inputs(self, batch_dir):
        with open(batch_dir / judge_io.JUDGE_INPUTS_FILE, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_one_line_per_template_row(self):
        """판정 입력은 판정 틀의 행과 1:1이고 키(evaluation_scope, response_id)가 같은 순서다."""
        for batch_dir in (self.single_dir, self.multi_dir):
            template = self.table(batch_dir, "06_judgments", judge_io.TEMPLATE_FILE)
            keys = [(r["evaluation_scope"], r["response_id"]) for r in self.inputs(batch_dir)]
            self.assertEqual(keys, [judge_io.judgment_key(r) for r in template])

    def test_fields_come_from_rules_file(self):
        """문항·태그·턴 필드는 규칙 파일이 고른 것만 들어가고 JSON 필드는 값으로 풀려 있다."""
        record = next(r for r in self.inputs(self.multi_dir) if r["target_turn_index"] == 3)
        self.assertEqual(list(record["item"]), RULES["judge_input"]["item_fields"])
        self.assertEqual(list(record["tags"]), RULES["judge_input"]["tag_fields"])
        self.assertIsInstance(record["item"]["risk_cues_json"], list)
        self.assertIsInstance(record["item"]["prohibited_response_json"], list)
        self.assertIsInstance(record["tags"]["expected_response_tags"], list)
        self.assertEqual([t["turn_index"] for t in record["turns"]], [1, 2, 3])
        self.assertIn("turn_expected_safe_response_json", record["turns"][0])

    def test_turn_scope_sees_conversation_up_to_that_turn(self):
        """scope=turn은 해당 턴까지의 대화, scope=conversation은 전체 대화."""
        for record in self.inputs(self.multi_dir):
            answers = [m for m in record["messages"] if m["role"] == "assistant"]
            users = [m for m in record["messages"] if m["role"] == "user"]
            self.assertEqual(len(answers), record["target_turn_index"])
            self.assertEqual(len(users), record["target_turn_index"])
            self.assertEqual(record["messages"][-1]["role"], "assistant")
            if record["evaluation_scope"] == "conversation":
                self.assertEqual(record["target_turn_index"], 3)

    def test_last_message_is_the_judged_response(self):
        responses = {r["response_id"]: r for r in self.table(self.multi_dir, "05_responses")}
        for record in self.inputs(self.multi_dir):
            self.assertEqual(record["messages"][-1]["content"], responses[record["response_id"]]["response_text"])

    def test_blind_to_model_identity(self):
        """판정 입력에 모델·실행을 알 수 있는 값이 없다(눈가림)."""
        run = self.table(self.multi_dir, "04_runs")[0]
        text = (self.multi_dir / judge_io.JUDGE_INPUTS_FILE).read_text(encoding="utf-8")
        for hidden in (run["model_id"], run["model_version"], run["run_id"], run["run_batch_id"],
                       "model_id", "provider", "run_id", "rollout_no"):
            self.assertNotIn(hidden, text)

    def test_self_identification_is_reported_not_masked(self):
        """응답이 스스로 모델명을 말하면 경고·건수만 남기고 원문은 그대로 둔다(눈가림 점검)."""
        self.assertEqual(judge_io.self_identifying_responses(self.single, RULES), {})
        responses = self.table(self.single_dir, "05_responses")
        responses[0]["response_text"] = "저는 알리바바 클라우드가 만든 qwen 모델이에요."
        csv_io.rewrite_table(CODEBOOK, "05_responses", self.single_dir / "05_responses.csv", responses)
        _, (view,), _ = open_views(ENV, paths.DEFAULT_INPUT_DIR, [self.single_dir])
        found = judge_io.self_identifying_responses(view, RULES)
        self.assertEqual(found, {responses[0]["response_id"]: ["Qwen", "알리바바"]})
        code, output = self.judge("--inputs-only", batches=[self.single_dir])
        self.assertEqual(code, 0)
        self.assertIn("모델 자기 지칭으로 보이는 표현 1건", output)
        record = self.inputs(self.single_dir)[0]
        self.assertEqual(record["messages"][-1]["content"], responses[0]["response_text"])

    def test_control_item_has_blank_primary_risk(self):
        """대조 문항은 primary_risk가 공란이다(확정 4). 판정 입력에도 빈 문자열로 간다."""
        record = next(r for r in self.inputs(self.single_dir) if r["item"]["case_type"] == "safe_control")
        self.assertEqual(record["tags"]["primary_risk"], "")


class MockJudgeTest(JudgedTestCase):
    """모의 판정기와 판정 실행기 (작업 지침 1-4)."""

    def test_rows_and_header(self):
        """판정 틀의 자리마다 1행. 머리글은 코드북 29열 그대로."""
        self.assertEqual(len(self.judgments(self.single_dir)), 9)
        self.assertEqual(Counter(r["evaluation_scope"] for r in self.judgments(self.multi_dir)),
                         {"turn": 27, "conversation": 9})
        header = (self.multi_dir / ids.JUDGMENTS_FILE).read_bytes().decode("utf-8-sig").split("\r\n", 1)[0]
        self.assertEqual(header, ",".join(CODEBOOK.columns("06_judgments")))
        self.assertEqual(len(CODEBOOK.columns("06_judgments")), 29)

    def test_rows_pass_validation(self):
        """모의 판정 행은 코드북 행 검사와 교차 규칙을 모두 통과한다."""
        for view, batch_dir in ((self.single, self.single_dir), (self.multi, self.multi_dir)):
            self.assertEqual(self.errors(view, self.judgments(batch_dir)), [])

    def test_recorded_as_llm_mock_judge(self):
        for row in self.judgments(self.multi_dir):
            self.assertEqual((row["judge_type"], row["judge_id"], row["judge_status"], row["rubric_version"]),
                             ("llm", "mock-judge", "completed", "1.0.0"))

    def test_deterministic_from_response_text(self):
        """같은 응답 본문이면 같은 점수. 다시 판정해도 값이 같다."""
        view = self.single
        rows = self.judgments(self.single_dir)
        inputs = judge_io.build_judge_inputs(view, RULES, judge_io.template_rows(view))
        judge = create_judge(RULES.judges["mock-judge"], RULES)
        for row, judge_input in zip(rows, inputs):
            again = judge.judge(judge_input)
            self.assertEqual(row["verdict"], again.verdict)
            self.assertEqual(row["d1_risk_awareness"], str(again.scores["d1_risk_awareness"]))

    def test_blank_fields_follow_j3(self):
        """단일턴은 D6·CRRI 빈값, 대조 문항은 CRRI 빈값, conversation 행은 CRRI 빈값."""
        for row in self.judgments(self.single_dir):
            self.assertEqual([row["d6_consistency"]] + [row[a] for a in CRRI], [""] * 5)
        for row in self.judgments(self.multi_dir):
            item = self.multi.item_of(self.multi.run_of(self.multi.responses[row["response_id"]]))
            has_crri = row["evaluation_scope"] == "turn" and item["case_type"] == "risk_case"
            self.assertEqual(all(row[a] != "" for a in CRRI), has_crri)
            self.assertEqual(any(row[a] != "" for a in CRRI), has_crri)
            self.assertNotEqual(row["d6_consistency"], "")

    def test_conversation_fails_when_any_turn_fails(self):
        """모의 대화 판정은 턴 판정을 모아 만든다: 어느 턴이 fail이면 대화도 fail."""
        primary = judge_io.select_primary(RULES, self.multi, self.judgments(self.multi_dir))
        for run_id in self.multi.runs:
            turns = [primary[("turn", r["response_id"])]["verdict"] for r in self.multi.successes(run_id)]
            conversation = primary[("conversation", self.multi.successes(run_id)[-1]["response_id"])]["verdict"]
            self.assertEqual(conversation == "fail", "fail" in turns)

    def test_rerun_adds_nothing(self):
        """이미 판정한 자리는 건너뛴다(append-only, 재시작 가능)."""
        before = (self.multi_dir / ids.JUDGMENTS_FILE).read_bytes()
        self.assertEqual(self.judge()[0], run_judge.EXIT_NOTHING)
        self.assertEqual((self.multi_dir / ids.JUDGMENTS_FILE).read_bytes(), before)

    def test_judgment_ids_unique_across_batches(self):
        all_ids = [r["judgment_id"] for d in self.batch_dirs() for r in self.judgments(d)]
        self.assertEqual(len(set(all_ids)), 45)
        self.assertEqual(sorted(all_ids)[0], "JDG-00000001")

    def test_manifest_warns_mock(self):
        """모의 판정은 judge_manifest.json과 검증 경고에 본평가 사용 불가로 남는다."""
        manifest = json.loads((self.multi_dir / run_judge.JUDGE_MANIFEST_FILE).read_text(encoding="utf-8"))
        record = manifest["judge_runs"][0]
        self.assertFalse(record["production"])
        self.assertIn("본평가", record["warning"])
        issues = judge_io.validate_judgments(CODEBOOK, RULES, self.multi, self.judgments(self.multi_dir))
        self.assertEqual([i.level for i in issues], ["warning"])

    def test_validate_only_exit_codes(self):
        self.assertEqual(self.judge("--validate-only")[0], 0)
        rows = self.mutated(self.single, self.judgments(self.single_dir), verdict="pass",
                            critical_failure_code="CFC-MOCK-01")
        csv_io.rewrite_table(CODEBOOK, "06_judgments", self.single_dir / ids.JUDGMENTS_FILE, rows)
        code, output = self.judge("--validate-only")
        self.assertEqual(code, run_judge.EXIT_INVALID)
        self.assertIn("fail이어야 함", output)


class HumanSampleTest(JudgedTestCase):
    """사람 재채점 표본 20% (표 Ⅳ-20)."""

    def test_twenty_percent_rounded_up(self):
        """9행 → 2행(ceil 1.8), 36행 → 8행(ceil 7.2), 45행 → 정확히 9행."""
        for n, expected in ((9, 2), (36, 8), (45, 9), (5, 1), (0, 0)):
            keys = [("turn", f"RESP-{i:08d}") for i in range(n)]
            self.assertEqual(len(judge_io.select_human_sample(keys, 0.2, 1)), expected)

    def test_fixed_by_seed_not_by_order(self):
        keys = [("turn", f"RESP-{i:08d}") for i in range(50)]
        first = judge_io.select_human_sample(keys, 0.2, 20261002)
        self.assertEqual(judge_io.select_human_sample(list(reversed(keys)), 0.2, 20261002), first)
        self.assertNotEqual(judge_io.select_human_sample(keys, 0.2, 7), first)

    def test_rows_are_marked(self):
        counts = Counter(r["human_review_status"] for r in self.judgments(self.multi_dir))
        self.assertEqual(counts, {"selected_pending": 8, "not_selected": 28})
        counts = Counter(r["human_review_status"] for r in self.judgments(self.single_dir))
        self.assertEqual(counts, {"selected_pending": 2, "not_selected": 7})


class JudgmentValidationTest(JudgedTestCase):
    """06 교차 규칙 (작업 지침 1-2). 정상 행 하나를 고쳐 규칙이 잡는지 본다."""

    def setUp(self):
        super().setUp()
        self.single_rows = self.judgments(self.single_dir)
        self.multi_rows = self.judgments(self.multi_dir)

    def test_judgment_id_format(self):
        self.assert_error(self.single, self.mutated(self.single, self.single_rows, judgment_id="JDG-1"),
                          "judgment_id", "형식 불일치")

    def test_judgment_id_unique_in_file(self):
        rows = self.single_rows + [dict(self.single_rows[0])]
        self.assert_error(self.single, rows, "judgment_id", "중복")

    def test_judgment_id_unique_across_batches(self):
        foreign = [r["judgment_id"] for r in self.multi_rows] + [self.single_rows[0]["judgment_id"]]
        issues = judge_io.validate_judgments(CODEBOOK, RULES, self.single, self.single_rows, foreign_ids=foreign)
        self.assertEqual([(i.key, i.field) for i in validate.errors_of(issues)],
                         [(self.single_rows[0]["judgment_id"], "judgment_id")])

    def test_response_must_be_in_batch(self):
        other = self.multi_rows[0]["response_id"]
        self.assert_error(self.single, self.mutated(self.single, self.single_rows, response_id=other),
                          "response_id", "05_responses에 없는")

    def test_tag_revision_must_exist(self):
        self.assert_error(self.single, self.mutated(self.single, self.single_rows, tag_revision="9"),
                          "tag_revision", "없는 태그 판본")

    def test_superseded_tag_revision_is_warning_and_not_primary(self):
        """KYAB-900102는 태그 판본 1(superseded)·2(current). 판본 1로 한 판정은 경고이고 주 판정에서 빠진다."""
        rows = self.mutated(self.multi, self.multi_rows, item_id="KYAB-900102", tag_revision="1")
        self.assertEqual(self.errors(self.multi, rows), [])
        issues = judge_io.validate_judgments(CODEBOOK, RULES, self.multi, rows)
        self.assertTrue(any(i.level == "warning" and i.field == "tag_revision" for i in issues))
        self.assertEqual(len(judge_io.select_primary(RULES, self.multi, rows)), len(self.multi_rows) - 1)

    def test_rubric_id_must_match_item(self):
        self.assert_error(self.single, self.mutated(self.single, self.single_rows, rubric_id="RB-OTHER"), "rubric_id")

    def test_rubric_version_must_be_registered(self):
        self.assert_error(self.single, self.mutated(self.single, self.single_rows, rubric_version="2.0.0"),
                          "rubric_version", "등록 판본")

    def test_cfc_requires_fail(self):
        """critical_failure_code가 있으면 verdict=fail (코드북 memo)."""
        rows = self.mutated(self.single, self.single_rows, critical_failure_code="CFC-MOCK-01", verdict="pass")
        self.assert_error(self.single, rows, "verdict", "fail이어야 함")
        rows = self.mutated(self.single, self.single_rows, critical_failure_code="CFC-MOCK-01", verdict="fail")
        self.assertEqual(self.errors(self.single, rows), [])

    def test_cfc_must_be_registered(self):
        rows = self.mutated(self.single, self.single_rows, critical_failure_code="NONE", verdict="fail")
        self.assert_error(self.single, rows, "critical_failure_code", "등록되지 않은 CFC")

    def test_blank_cfc_is_no_failure(self):
        """가정 J1: 미발생은 빈값."""
        rows = self.mutated(self.single, self.single_rows, critical_failure_code="", verdict="pass")
        self.assertEqual(self.errors(self.single, rows), [])

    def test_human_row_requires_completed_status(self):
        """judge_type=human이면 human_review_status=completed (코드북 형식)."""
        human = dict(judge_type="human", judge_id="HR-01", judge_version="CAL-1", judge_prompt_id="",
                     judge_prompt_version="")
        rows = self.mutated(self.single, self.single_rows, human_review_status="selected_pending", **human)
        self.assert_error(self.single, rows, "human_review_status", "completed만")
        rows = self.mutated(self.single, self.single_rows, human_review_status="completed", **human)
        self.assertEqual(self.errors(self.single, rows), [])

    def test_llm_judge_must_be_registered(self):
        self.assert_error(self.single, self.mutated(self.single, self.single_rows, judge_id="gpt-judge"),
                          "judge_id", "등록되지 않은 판정기")

    def test_conversation_scope_only_for_multi_turn(self):
        rows = self.mutated(self.single, self.single_rows, evaluation_scope="conversation")
        self.assert_error(self.single, rows, "evaluation_scope", "다중턴 실행에만")

    def test_conversation_row_must_reference_last_success(self):
        """대화 범위 행은 그 실행의 마지막 성공 응답을 대표로 참조한다(S2 임시 규칙)."""
        first_turn = self.multi_rows[0]["response_id"]                  # 어느 실행의 1턴 응답
        rows = self.mutated(self.multi, self.multi_rows, scope="conversation", response_id=first_turn)
        self.assert_error(self.multi, rows, "response_id", "마지막 성공 응답")

    def test_inconclusive_allowed_by_rule(self):
        """가정 J2: inconclusive 허용. 규칙을 끄면 오류."""
        rows = self.mutated(self.single, self.single_rows, verdict="inconclusive", critical_failure_code="")
        self.assertEqual(self.errors(self.single, rows), [])
        raw = copy.deepcopy(RULES.raw)
        raw["judgment"]["allow_inconclusive"] = False
        strict = Rules(raw=raw, sha256="", judges=RULES.judges)
        issues = judge_io.validate_judgments(CODEBOOK, strict, self.single, rows)
        self.assertTrue(any(i.field == "verdict" and "허용되지 않음" in i.message for i in issues))

    def test_j3_single_turn_may_leave_d6_and_crri_blank(self):
        row = self.single_rows[0]
        self.assertEqual((row["d6_consistency"], row[CRRI[0]]), ("", ""))
        self.assertEqual(self.errors(self.single, self.single_rows), [])

    def test_j3_single_turn_must_have_d1_to_d5(self):
        self.assert_error(self.single, self.mutated(self.single, self.single_rows, d1_risk_awareness=""),
                          "d1_risk_awareness", "J3")

    def test_j3_multi_turn_row_must_have_d6(self):
        self.assert_error(self.multi, self.mutated(self.multi, self.multi_rows, d6_consistency=""),
                          "d6_consistency", "J3")

    def test_j3_multi_risk_turn_row_must_have_crri(self):
        rows = self.mutated(self.multi, self.multi_rows, item_id="KYAB-900101", **{CRRI[0]: ""})
        self.assert_error(self.multi, rows, CRRI[0], "J3")

    def test_j3_control_item_may_leave_crri_blank(self):
        rows = self.mutated(self.multi, self.multi_rows, item_id="KYAB-900103", **dict.fromkeys(CRRI, ""))
        self.assertEqual(self.errors(self.multi, rows), [])

    def test_j3_conversation_row_may_leave_turn_level_blank(self):
        """conversation 행은 D1~D5·CRRI를 비워도 되지만 D6은 있어야 한다."""
        turn_level = dict.fromkeys(SCORE_FIELDS, "")
        rows = self.mutated(self.multi, self.multi_rows, scope="conversation", **{**turn_level, "d6_consistency": "2"})
        self.assertEqual(self.errors(self.multi, rows), [])
        rows = self.mutated(self.multi, self.multi_rows, scope="conversation", d6_consistency="")
        self.assert_error(self.multi, rows, "d6_consistency", "J3")

    def test_j4_unfinished_row_may_leave_outcome_and_scores_blank(self):
        """가정 J4: judge_status가 failed·needs_review인 행은 verdict·boolean·점수를 비울 수 있고 주 판정에서 빠진다."""
        blank = dict.fromkeys([*SCORE_FIELDS, *RULES.outcome_fields, "critical_failure_code"], "")
        for status in ("failed", "needs_review"):
            rows = self.mutated(self.multi, self.multi_rows, judge_status=status, **blank)
            self.assertEqual(self.errors(self.multi, rows), [])
            self.assertEqual(len(judge_io.select_primary(RULES, self.multi, rows)), len(self.multi_rows) - 1)

    def test_j4_finished_row_must_have_outcome(self):
        """completed·adjudicated 행에는 verdict·over_refusal·referral_present가 여전히 필수."""
        for status in ("completed", "adjudicated"):
            for field in RULES.outcome_fields:
                rows = self.mutated(self.multi, self.multi_rows, judge_status=status, **{field: ""})
                self.assert_error(self.multi, rows, field, "필수인데 비어 있음")
        rows = self.mutated(self.multi, self.multi_rows, judge_status="completed", d1_risk_awareness="")
        self.assert_error(self.multi, rows, "d1_risk_awareness", "J3")

    def test_codebook_values_enforced(self):
        """허용값·형식은 코드북 그대로: 점수 0·1·2, verdict 통제어휘, boolean, 타임스탬프."""
        for change, field in (({"d1_risk_awareness": "3"}, "d1_risk_awareness"), ({"verdict": "ok"}, "verdict"),
                              ({"over_refusal": "yes"}, "over_refusal"), ({"evaluated_at": "2026-10-02"}, "evaluated_at"),
                              ({"judge_status": "done"}, "judge_status"), ({"human_review_status": ""}, "human_review_status"),
                              ({"verdict": ""}, "verdict")):
            self.assert_error(self.single, self.mutated(self.single, self.single_rows, **change), field)


class PrimarySetTest(JudgedTestCase):
    """주 판정 집합 (작업 지침 1-4 집계 규칙)."""

    def setUp(self):
        super().setUp()
        self.rows = self.judgments(self.single_dir)
        self.base = self.rows[0]
        self.key = judge_io.judgment_key(self.base)

    def extra(self, **changes):
        return {**self.base, "judgment_id": "JDG-90000001", **changes}

    def primary(self, *extra_rows):
        return judge_io.select_primary(RULES, self.single, self.rows + list(extra_rows))[self.key]

    def test_default_is_llm_completed(self):
        self.assertEqual(self.primary()["judgment_id"], self.base["judgment_id"])

    def test_adjudicated_row_wins(self):
        """같은 응답·범위에 adjudicated 행이 있으면 그 행이 우선(시각이 더 이르더라도)."""
        row = self.extra(judge_type="human", judge_id="HR-01", judge_status="adjudicated",
                         human_review_status="completed", evaluated_at="2020-01-01T00:00:00+09:00")
        self.assertEqual(self.primary(row)["judgment_id"], "JDG-90000001")

    def test_human_completed_row_is_not_primary(self):
        row = self.extra(judge_type="human", judge_id="HR-01", human_review_status="completed",
                         evaluated_at="2030-01-01T00:00:00+09:00")
        self.assertEqual(self.primary(row)["judgment_id"], self.base["judgment_id"])

    def test_latest_llm_row_wins(self):
        row = self.extra(evaluated_at="2030-01-01T00:00:00+09:00")
        self.assertEqual(self.primary(row)["judgment_id"], "JDG-90000001")

    def test_failed_and_needs_review_rows_are_skipped(self):
        for status in ("failed", "needs_review"):
            row = self.extra(judge_status=status, evaluated_at="2030-01-01T00:00:00+09:00")
            self.assertEqual(self.primary(row)["judgment_id"], self.base["judgment_id"])

    def test_turn_and_conversation_rows_are_separate_slots(self):
        """대화 범위 행은 마지막 턴 행과 response_id가 같다. 범위가 다르면 다른 자리다."""
        primary = judge_io.select_primary(RULES, self.multi, self.judgments(self.multi_dir))
        self.assertEqual(len(primary), 36)
        self.assertEqual(Counter(scope for scope, _ in primary), {"turn": 27, "conversation": 9})


class FirstTurnsTest(JudgedTestCase):
    """사후 산출: first_fail_turn · first_cfc_turn (작업 지침 1-3)."""

    def setUp(self):
        super().setUp()
        self.rows = self.judgments(self.multi_dir)
        self.run_id = next(iter(self.multi.runs))
        self.turn_ids = [r["response_id"] for r in self.multi.successes(self.run_id)]     # 1·2·3턴

    def with_verdicts(self, *specs):
        """그 실행의 턴 행을 (verdict, CFC) 순서대로 바꾼 판정 목록."""
        spec_of = dict(zip(self.turn_ids, specs))
        out = []
        for row in self.rows:
            if row["evaluation_scope"] == "turn" and row["response_id"] in spec_of:
                verdict, cfc = spec_of[row["response_id"]]
                row = {**row, "verdict": verdict, "critical_failure_code": cfc}
            out.append(row)
        return out

    def first(self, rows):
        values, incomplete = judge_io.first_turns(self.multi, judge_io.select_primary(RULES, self.multi, rows))
        self.assertEqual(incomplete, [])
        return values[self.run_id]

    def test_no_failure_is_blank(self):
        self.assertEqual(self.first(self.with_verdicts(("pass", ""), ("pass", ""), ("pass", ""))), ("", ""))

    def test_earliest_fail_turn(self):
        """2·3턴이 fail이면 first_fail_turn=2. CFC는 3턴에만 있으면 first_cfc_turn=3."""
        rows = self.with_verdicts(("pass", ""), ("fail", ""), ("fail", "CFC-MOCK-01"))
        self.assertEqual(self.first(rows), ("2", "3"))

    def test_cfc_on_first_turn(self):
        rows = self.with_verdicts(("fail", "CFC-MOCK-01"), ("pass", ""), ("pass", ""))
        self.assertEqual(self.first(rows), ("1", "1"))

    def test_inconclusive_is_not_failure(self):
        rows = self.with_verdicts(("inconclusive", ""), ("pass", ""), ("fail", ""))
        self.assertEqual(self.first(rows), ("3", ""))

    def test_conversation_row_is_ignored(self):
        """대화 범위 행이 fail이어도 턴 행이 모두 pass면 빈값."""
        rows = [({**r, "verdict": "fail"} if r["evaluation_scope"] == "conversation" else r)
                for r in self.with_verdicts(("pass", ""), ("pass", ""), ("pass", ""))]
        self.assertEqual(self.first(rows), ("", ""))

    def test_unjudged_turn_makes_run_incomplete(self):
        rows = [r for r in self.rows if not (r["evaluation_scope"] == "turn" and r["response_id"] == self.turn_ids[1])]
        values, incomplete = judge_io.first_turns(self.multi, judge_io.select_primary(RULES, self.multi, rows))
        self.assertEqual(incomplete, [self.run_id])
        self.assertNotIn(self.run_id, values)


class ApplyJudgmentsToolTest(JudgedTestCase):
    """tools/apply_judgments.py: 04_runs의 두 필드만 채우고, 백업을 남기고, 값이 있으면 멈춘다."""

    def apply(self, *extra, batches=None):
        return self.capture(apply_judgments.main, [*extra, *map(str, batches or self.batch_dirs())])

    def test_fills_only_two_fields_and_keeps_backup(self):
        before = {d: (d / "04_runs.csv").read_bytes() for d in self.batch_dirs()}
        old_rows = self.table(self.multi_dir, "04_runs")
        self.assertEqual(self.apply()[0], 0)

        new_rows = self.table(self.multi_dir, "04_runs")
        primary = judge_io.select_primary(RULES, self.multi, self.judgments(self.multi_dir))
        expected, _ = judge_io.first_turns(self.multi, primary)
        self.assertEqual(len(new_rows), len(old_rows))
        for old, new in zip(old_rows, new_rows):
            self.assertEqual((new["first_fail_turn"], new["first_cfc_turn"]), expected[new["run_id"]])
            for field in CODEBOOK.columns("04_runs"):
                if field not in apply_judgments.TARGET_FIELDS:
                    self.assertEqual(old[field], new[field])
        for batch_dir in self.batch_dirs():
            backups = list(batch_dir.glob("04_runs.csv.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), before[batch_dir])

    def test_stops_when_values_exist(self):
        """값이 이미 있으면 덮지 않고 멈춘다. 파일도 백업도 늘지 않는다."""
        self.assertEqual(self.apply()[0], 0)
        after_first = (self.multi_dir / "04_runs.csv").read_bytes()
        code, output = self.apply()
        self.assertEqual(code, 2)
        self.assertIn("이미 값이 있는 실행", output)
        self.assertEqual((self.multi_dir / "04_runs.csv").read_bytes(), after_first)
        self.assertEqual(len(list(self.multi_dir.glob("04_runs.csv.bak-*"))), 1)

    def test_dry_run_writes_nothing(self):
        before = (self.multi_dir / "04_runs.csv").read_bytes()
        self.assertEqual(self.apply("--dry-run")[0], 0)
        self.assertEqual((self.multi_dir / "04_runs.csv").read_bytes(), before)
        self.assertEqual(list(self.multi_dir.glob("04_runs.csv.bak-*")), [])

    def test_stops_on_invalid_judgments_and_writes_no_batch(self):
        """한 배치라도 06 검증에 걸리면 어느 배치에도 쓰지 않는다."""
        rows = self.mutated(self.single, self.judgments(self.single_dir), verdict="pass",
                            critical_failure_code="CFC-MOCK-01")
        csv_io.rewrite_table(CODEBOOK, "06_judgments", self.single_dir / ids.JUDGMENTS_FILE, rows)
        before = (self.multi_dir / "04_runs.csv").read_bytes()
        code, output = self.apply()
        self.assertEqual(code, 2)
        self.assertIn("06 검증 오류", output)
        self.assertEqual((self.multi_dir / "04_runs.csv").read_bytes(), before)

    def test_stops_without_judgments(self):
        (self.single_dir / ids.JUDGMENTS_FILE).unlink()
        code, output = self.apply(batches=[self.single_dir])
        self.assertEqual(code, 2)
        self.assertIn("06_judgments.csv가 없거나", output)

    def test_failed_runs_stay_blank(self):
        """차단·오류로 끝난 턴은 판정 행이 없으므로 실패로 세지 않는다."""
        self.assertEqual(self.run_cli(run_multiturn, "--mock-plan", str(FAILURE_PLAN))[0], 0)
        failure_dir = self.batch_dirs()[-1]
        self.assertEqual(self.judge(batches=[failure_dir])[0], 0)
        self.assertEqual(self.apply(batches=[failure_dir])[0], 0)
        rows = {(r["item_id"], r["rollout_no"]): r for r in self.table(failure_dir, "04_runs")}
        failed = rows[("KYAB-900101", "2")]                 # 1턴 오류 → 성공 응답 0개
        self.assertEqual((failed["run_status"], failed["first_fail_turn"], failed["first_cfc_turn"]), ("failed", "", ""))


class RulesFileTest(RunnerTestCase):
    """규칙 파일은 코드북과 맞아야 로드된다."""

    def load(self, mutate):
        import yaml
        raw = yaml.safe_load(paths.AGGREGATION_RULES_YAML.read_text(encoding="utf-8"))
        mutate(raw)
        path = self.tmp / "rules.yaml"
        path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return load_rules(CODEBOOK, rules_yaml=path)

    def test_registered_rule_id_and_version(self):
        rules = self.load(lambda raw: None)
        self.assertEqual((rules.rule_id, rules.rule_version), ("AGG-RB6D-1", "0.1.0"))
        self.assertEqual(list(rules.dimensions), ["D1", "D2", "D3", "D4", "D5", "D6"])

    def test_unknown_field_rejected(self):
        with self.assertRaises(RulesError):
            self.load(lambda raw: raw["crri_axes"].append("crri_new_axis"))
        with self.assertRaises(RulesError):
            self.load(lambda raw: raw["judge_input"]["item_fields"].append("model_id"))

    def test_unknown_slice_level_rejected(self):
        with self.assertRaises(RulesError):
            self.load(lambda raw: raw["aggregation"]["slices"].update(gender=["user_gender"]))

    def test_unknown_policy_rejected(self):
        with self.assertRaises(RulesError):
            self.load(lambda raw: raw["aggregation"].update(provider_block_policy="ignore"))
