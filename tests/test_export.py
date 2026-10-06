"""납품 형식(JSONL) 내보내기 검사: 구조, 왕복 무손실, 스키마, 연계 키, 안전장치."""
import json
import shutil

from test_judge_io import ENV, RULES, JudgedTestCase

from kyab_runner import csv_io, export, run_multiturn, run_single                    # noqa: E402
from kyab_runner.context import open_views                                         # noqa: E402
from kyab_runner.records import InputIndex                                         # noqa: E402
from test_runner import CODEBOOK, FAILURE_PLAN, TAXONOMY                           # noqa: E402
from kyab_runner import paths                                                      # noqa: E402


class ExportTestCase(JudgedTestCase):
    """모의 배치 2개(단일 9·3턴 9, 모의 판정 완료)를 내보낸다."""

    def setUp(self):
        super().setUp()
        self.export_dir = self.tmp / "export"

    def run_export(self, *extra, batches=None, out=None):
        argv = [*map(str, self.batch_dirs() if batches is None else batches), "--out", str(out or self.export_dir), *extra]
        return self.capture(export.main, argv)

    def lines(self, relative):
        with open(self.export_dir / relative, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def views(self, input_dir=None):
        return open_views(ENV, input_dir or paths.DEFAULT_INPUT_DIR, self.batch_dirs())


class StructureTest(ExportTestCase):
    def setUp(self):
        super().setUp()
        code, self.output = self.run_export("--allow-mock-judge")
        self.assertEqual(code, 0, self.output)

    def test_files_and_manifest(self):
        manifest = json.loads((self.export_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest["files"]), ["items.jsonl", "judgments/mock-echo.jsonl", "responses/mock-echo.jsonl",
                                                     "schema/items.schema.json", "schema/judgments.schema.json",
                                                     "schema/responses.schema.json", "sources.json"])
        self.assertEqual((manifest["files"]["items.jsonl"]["rows"], manifest["files"]["responses/mock-echo.jsonl"]["rows"],
                          manifest["files"]["judgments/mock-echo.jsonl"]["rows"]), (6, 18, 45))
        self.assertEqual(manifest["mock_judge_used"], ["mock-judge"])
        self.assertIn("본평가", manifest["warning"])
        self.assertEqual(manifest["aggregation_rule"]["version"], RULES.rule_version)
        self.assertEqual(len(manifest["system_prompts"]), 1)
        self.assertTrue(manifest["generated_by"].startswith("kyab_runner.export runner-"))
        for info in manifest["files"].values():
            self.assertEqual(len(info["sha256"]), 64)
        sources = json.loads((self.export_dir / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(set(sources["sources"]), {"CAREBench", "MinorBench", "NEW"})
        # 원본 CSV는 러너 밖(프로젝트 data/)에 있어 환경에 따라 없을 수 있다: 있으면 sha256, 없으면 TODO에 표시
        care = sources["sources"]["CAREBench"]
        if (export.DEFAULT_DATA_DIR / "CAREBench_prompts_500.csv").exists():
            self.assertEqual(len(care["sha256"]), 64)
        else:
            self.assertEqual(care["sha256"], "")
            self.assertTrue(any("원본 파일 없음" in t for t in care["TODO"]))
        self.assertIn("version", sources["sources"]["NEW"]["TODO"])

    def _registry(self, care_sha):
        return {"CAREBench": {"local_file": "CAREBench_prompts_500.csv", "hf_repo": "org/CAREBench", "hf_commit": "a" * 40,
                              "hf_file": "prompts.csv", "sha256": care_sha, "basis": "시험"},
                "MinorBench": {"local_file": "MinorBench_original_299.csv", "hf_repo": "org/MinorBench", "hf_commit": "b" * 40,
                               "hf_file": "test.csv", "sha256": "c" * 64, "basis": "시험"}}

    def test_sources_filled_only_when_sha256_matches_registry(self):
        """원천 판본·위치: 등록부 sha256 = 로컬 원본 sha256일 때만 채움. 다르면 TODO+경고, 파일 없으면 TODO+경고."""
        import hashlib
        data_dir = self.tmp / "data"
        data_dir.mkdir()
        (data_dir / "CAREBench_prompts_500.csv").write_text("case_uid,prompt\nX,Y\n", encoding="utf-8")
        local_sha = hashlib.sha256((data_dir / "CAREBench_prompts_500.csv").read_bytes()).hexdigest()
        index, _, _ = self.views()
        # 일치: version=HF 커밋, location에 저장소·커밋·파일이 모두 보임, TODO에는 acquired_at만
        result = export.sources_skeleton(index, data_dir, self._registry(local_sha))
        care, minor, new = (result["sources"][k] for k in ("CAREBench", "MinorBench", "NEW"))
        self.assertEqual((care["sha256"], care["version"]), (local_sha, "a" * 40))
        self.assertIn("org/CAREBench", care["location"])
        self.assertIn("a" * 40, care["location"])
        self.assertTrue(care["location"].endswith("/prompts.csv"))
        self.assertEqual(care["origin"], {"hf_repo": "org/CAREBench", "hf_commit": "a" * 40, "hf_file": "prompts.csv"})
        self.assertEqual(care["TODO"], ["acquired_at"])
        # 파일 없음(MinorBench): 빈칸 + TODO + 경고
        self.assertEqual((minor["sha256"], minor["version"], minor["location"]), ("", "", ""))
        self.assertTrue(any("원본 파일 없음" in t for t in minor["TODO"]))
        self.assertNotIn("origin", minor)
        # 등록부에 없음(NEW): 빈칸 + TODO, 경고 없음
        self.assertEqual(new["TODO"][:3], ["version", "location", "acquired_at"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("MinorBench", result["warnings"][0])
        # 불일치: 로컬 파일은 있지만 등록부 sha256과 다름 → 채우지 않고 경고
        result = export.sources_skeleton(index, data_dir, self._registry("d" * 64))
        care = result["sources"]["CAREBench"]
        self.assertEqual((care["sha256"], care["version"], care["location"]), (local_sha, "", ""))
        self.assertTrue(any("등록부(config/sources.yaml)와 다름" in t for t in care["TODO"]))
        self.assertEqual(len(result["warnings"]), 2)

    def test_sources_registry_file(self):
        """실제 등록부(config/sources.yaml)는 형식 검사를 통과하고, 깨진 등록부는 거부된다."""
        registry = export.load_sources_registry()
        self.assertEqual(set(registry), {"CAREBench", "MinorBench"})
        bad = self.tmp / "sources_bad.yaml"
        bad.write_text("sources:\n  CAREBench:\n    local_file: x.csv\n    sha256: zz\n", encoding="utf-8")
        with self.assertRaises(export.SourcesRegistryError):
            export.load_sources_registry(bad)
        self.assertEqual(export.load_sources_registry(self.tmp / "none.yaml"), {})

    def test_manifest_runner_git_and_links(self):
        """manifest: 러너 git 커밋·dirty 표시(dirty면 경고, 거부는 않음)와 파일 사이 조인 키(links)."""
        from unittest import mock
        manifest = json.loads((self.export_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("runner_git", manifest)
        self.assertEqual(set(manifest["links"]), {"note", "items.jsonl", "responses/<model_id>.jsonl",
                                                  "judgments/<model_id>.jsonl", "results/07_results.csv"})
        self.assertEqual(manifest["links"]["items.jsonl"]["key"], ["item_id", "item_version"])
        self.assertEqual(manifest["links"]["responses/<model_id>.jsonl"]["key"], "run_id")
        self.assertEqual(manifest["links"]["judgments/<model_id>.jsonl"]["→ responses.turns[]"], "response_id")
        for state, expect_warning in (({"commit": "f" * 40, "dirty": True, "uncommitted": 2}, True),
                                      ({"commit": "f" * 40, "dirty": False, "uncommitted": 0}, False),
                                      (None, False)):
            out = self.tmp / f"export_git_{expect_warning}_{state is None}"
            with mock.patch.object(export, "_git_state", return_value=state):
                code, output = self.run_export("--allow-mock-judge", out=out)
            self.assertEqual(code, 0, output)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            dirty_warnings = [w for w in manifest["warnings"] if ".dirty" in w]
            self.assertEqual(bool(dirty_warnings), expect_warning, manifest["warnings"])
            self.assertEqual(("커밋되지 않은 수정 2건" in output), expect_warning, output)
            if state is None:
                self.assertEqual(manifest["runner_git"]["dirty"], None)
            else:
                self.assertEqual(manifest["runner_git"], state)

    def test_items_single_and_multi_share_structure(self):
        """단일·3턴 문항의 키 구성이 같고 turns 길이만 1·3으로 다르다. 01·02·03 전 필드가 한 번씩 들어간다."""
        items = {r["item_id"]: r for r in self.lines("items.jsonl")}
        self.assertEqual(len(items), 6)
        single, multi = items["KYAB-900001"], items["KYAB-900101"]
        self.assertEqual(list(single), list(multi))
        for block in ("evaluation", "metadata", "provenance"):
            self.assertEqual(list(single[block]), list(multi[block]))
        self.assertEqual((len(single["turns"]), len(multi["turns"])), (1, 3))
        self.assertEqual([t["turn_index"] for t in multi["turns"]], [1, 2, 3])
        self.assertIsInstance(single["evaluation"]["prohibited_response_json"], list)
        self.assertEqual(single["planned_round_count"], 1)
        self.assertEqual(multi["metadata"]["tags"]["primary_risk"], "A5")
        self.assertEqual(items["KYAB-900102"]["metadata"]["tags"]["tag_revision"], 2)
        self.assertEqual([t["tag_revision"] for t in items["KYAB-900102"]["metadata"]["tag_history"]], [1, 2])
        self.assertEqual(items["KYAB-900102"]["metadata"]["tag_history"][0]["primary_risk"], "R4")
        self.assertEqual(items["KYAB-900103"]["metadata"]["tags"]["control_target_risk"], "A4")
        # 전 필드 한 번씩
        def leaves(record, skip=("turns", "tag_history")):
            out = []
            for key, value in record.items():
                if key in skip:
                    continue
                out += leaves(value, skip) if isinstance(value, dict) else [key]
            return out
        flat = leaves(single)
        self.assertEqual(len(flat), len(set(flat)))
        expected = set(CODEBOOK.columns("01_items")) | (set(CODEBOOK.columns("02_item_tags")) - {"item_id", "item_version"})
        self.assertEqual(set(flat), expected)
        self.assertEqual(set(single["turns"][0]), set(CODEBOOK.columns("03_prompts")) - {"item_id", "item_version"})

    def test_responses_and_judgments_link_to_items(self):
        items = {(r["item_id"], r["item_version"]) for r in self.lines("items.jsonl")}
        responses = self.lines("responses/mock-echo.jsonl")
        self.assertEqual(len(responses), 18)
        multi = next(r for r in responses if r["item_id"] == "KYAB-900101")
        self.assertEqual((len(multi["turns"]), multi["outcome"]["actual_turn_count"], multi["settings"]["max_output_tokens"]), (3, 3, 8192))
        self.assertEqual(multi["turns"][2]["messages"][0]["role"], "system")
        self.assertEqual(len(multi["turns"][2]["messages"]), 6)                     # system + 3 user + 2 assistant
        self.assertIsInstance(multi["turns"][0]["raw_response"], dict)
        self.assertNotIn("system_prompt_text", multi["settings"])
        for r in responses:
            self.assertIn((r["item_id"], r["item_version"]), items)
        judgments = self.lines("judgments/mock-echo.jsonl")
        self.assertEqual(len(judgments), 45)
        run_ids = {r["run_id"] for r in responses}
        for j in judgments:
            self.assertIn((j["item_id"], j["item_version"]), items)
            self.assertIn(j["run_id"], run_ids)
            self.assertIn(j["critical_failure_code"], ("NONE", "CFC-MOCK-01"))
            self.assertEqual(set(j["scores"]), set(RULES.dimensions.values()))
            self.assertEqual(set(j["crri"]), set(RULES.crri_axes))
        single = next(j for j in judgments if j["item_id"] == "KYAB-900001")
        self.assertIsNone(single["scores"]["d6_consistency"])
        self.assertIsInstance(single["over_refusal"], bool)

    def test_roundtrip_is_lossless_and_schema_valid(self):
        index, views, _ = self.views()
        self.assertEqual(export.roundtrip_diffs(ENV, index, views, self.export_dir), [])
        self.assertEqual(export.schema_violations(self.export_dir), [])
        self.assertIn("왕복 차이 0", self.output)

    def test_schema_catches_bad_values(self):
        """스키마는 코드북에서 나온다: 허용값 밖·형식 위반·누락 키를 잡는다."""
        path = self.export_dir / "judgments" / "mock-echo.jsonl"
        rows = self.lines("judgments/mock-echo.jsonl")
        rows[0]["verdict"] = "ok"
        rows[1]["scores"]["d1_risk_awareness"] = 3
        rows[2]["judgment_id"] = "JDG-1"
        del rows[3]["evaluated_at"]
        rows[4]["extra"] = 1
        export._write_jsonl(path, rows)
        problems = export.schema_violations(self.export_dir)
        self.assertEqual(sorted(p[1] for p in problems), [1, 2, 3, 4, 5])
        # 왕복 검증도 값 변경을 잡는다
        index, views, _ = self.views()
        diffs = export.roundtrip_diffs(ENV, index, views, self.export_dir)
        self.assertTrue(any("verdict" in d for d in diffs))

    def test_schema_files_come_from_codebook(self):
        schema = json.loads((self.export_dir / "schema" / "items.schema.json").read_text(encoding="utf-8"))
        props = schema["properties"]
        self.assertEqual(props["case_type"]["enum"], ["risk_case", "safe_control", ""])
        self.assertEqual(props["conversation_mode"]["enum"], ["single", "multi"])
        tags = props["metadata"]["properties"]["tags"]["properties"]
        self.assertIn("R1", tags["primary_risk"]["enum"])                      # 옛 체계 행도 받는다
        self.assertEqual(tags["tag_revision"]["type"], "integer")
        self.assertIn("pattern", props["item_id"])
        responses = json.loads((self.export_dir / "schema" / "responses.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(responses["properties"]["settings"]["properties"]["max_output_tokens"]["enum"], [8192])


class SafetyTest(ExportTestCase):
    def test_mock_judge_refused_by_default(self):
        code, output = self.run_export()
        self.assertEqual(code, 2)
        self.assertIn("본평가에 쓸 수 없는 판정기", output)
        self.assertFalse((self.export_dir / "items.jsonl").exists())

    def test_secret_pattern_refuses_and_removes_output(self):
        responses = self.table(self.single_dir, "05_responses")
        responses[0]["response_text"] = "제 키는 sk-abcdefghijklmnop 입니다"
        csv_io.rewrite_table(CODEBOOK, "05_responses", self.single_dir / "05_responses.csv", responses)
        code, output = self.run_export("--allow-mock-judge")
        self.assertEqual(code, 2)
        self.assertIn("비밀값", output)
        self.assertFalse(self.export_dir.exists())

    def test_email_is_warning_only(self):
        responses = self.table(self.single_dir, "05_responses")
        responses[0]["response_text"] = "도움이 필요하면 help@example.org 로 연락하세요."
        csv_io.rewrite_table(CODEBOOK, "05_responses", self.single_dir / "05_responses.csv", responses)
        code, output = self.run_export("--allow-mock-judge")
        self.assertEqual(code, 0, output)
        self.assertIn("이메일 패턴 1건", output)
        manifest = json.loads((self.export_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["pattern_warnings"]["count"], 1)
        self.assertIn("responses/mock-echo.jsonl", manifest["pattern_warnings"]["locations"][0])

    def test_mixed_run_params_refused(self):
        runs = self.table(self.single_dir, "04_runs")
        for row in runs:
            row["max_output_tokens"] = "1024"
        csv_io.rewrite_table(CODEBOOK, "04_runs", self.single_dir / "04_runs.csv", runs)
        code, output = self.run_export("--allow-mock-judge")
        self.assertEqual(code, 2)
        self.assertIn("실행 조건이 섞여", output)

    def test_non_empty_output_folder_refused(self):
        self.export_dir.mkdir()
        (self.export_dir / "x").write_text("x")
        code, output = self.run_export("--allow-mock-judge")
        self.assertEqual(code, 2)
        self.assertIn("비어 있지 않습니다", output)

    def test_invalid_judgments_refused(self):
        rows = self.mutated(self.single, self.judgments(self.single_dir), verdict="pass", critical_failure_code="CFC-MOCK-01")
        from kyab_runner import ids
        csv_io.rewrite_table(CODEBOOK, "06_judgments", self.single_dir / ids.JUDGMENTS_FILE, rows)
        code, output = self.run_export("--allow-mock-judge")
        self.assertEqual((code, "06 검증 오류" in output), (2, True))


class MultiModelTest(ExportTestCase):
    def test_models_and_rollouts_are_kept_apart(self):
        """모델이 둘이면 responses·judgments 파일이 모델별로 나뉘고, 반복 3회가 run_id·rollout_no로 구분된다."""
        runs = self.table(self.multi_dir, "04_runs")
        for row in runs:
            row["model_id"] = "mock-other"
        csv_io.rewrite_table(CODEBOOK, "04_runs", self.multi_dir / "04_runs.csv", runs)
        code, output = self.run_export("--allow-mock-judge")
        self.assertEqual(code, 0, output)
        self.assertEqual(sorted(p.name for p in (self.export_dir / "responses").iterdir()), ["mock-echo.jsonl", "mock-other.jsonl"])
        self.assertEqual(len(self.lines("responses/mock-other.jsonl")), 9)
        self.assertEqual(len(self.lines("judgments/mock-other.jsonl")), 36)
        rollouts = sorted((r["item_id"], r["rollout_no"]) for r in self.lines("responses/mock-other.jsonl"))
        self.assertEqual(len(set(rollouts)), 9)
        index, views, _ = self.views()
        self.assertEqual(export.roundtrip_diffs(ENV, index, views, self.export_dir), [])

    def test_failure_batches_roundtrip(self):
        """차단·오류·절단이 섞인 배치도 무손실로 왕복한다(raw_response·finish_reason·error_message 포함)."""
        self.assertEqual(self.run_cli(run_single, "--mock-plan", str(FAILURE_PLAN))[0], 0)
        self.assertEqual(self.run_cli(run_multiturn, "--mock-plan", str(FAILURE_PLAN))[0], 0)
        self.assertEqual(self.judge(batches=self.batch_dirs()[2:])[0], 0)
        code, output = self.run_export("--allow-mock-judge")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.lines("responses/mock-echo.jsonl")), 36)

    def test_items_only_export(self):
        code, output = self.run_export("--allow-mock-judge", batches=[])
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.lines("items.jsonl")), 6)
        self.assertFalse((self.export_dir / "responses").exists())

    def test_results_are_copied_when_given(self):
        from kyab_runner import run_aggregate
        self.assertEqual(self.capture(run_aggregate.main, ["--allow-mock-judge", *map(str, self.batch_dirs())])[0], 0)
        results_dir = next(p for p in self.out.iterdir() if p.name.startswith("RESULTS-") and ".tmp" not in p.name)
        code, output = self.run_export("--allow-mock-judge", "--results", str(results_dir))
        self.assertEqual(code, 0, output)
        self.assertTrue((self.export_dir / "results" / "07_results.csv").exists())
        self.assertTrue((self.export_dir / "results" / "results_denominators.csv").exists())


class LayoutTest(ExportTestCase):
    def test_layout_covers_every_codebook_column(self):
        export._check_layout(CODEBOOK)
        from kyab_runner.codebook import load_codebook
        self.assertEqual(export._check_layout(load_codebook(TAXONOMY)), None)

    def test_value_conversion_roundtrip(self):
        for table, name, cell in (("02_item_tags", "sub_risk_codes", '["A3.01"]'), ("04_runs", "temperature", "0.0"),
                                  ("04_runs", "first_fail_turn", ""), ("06_judgments", "over_refusal", "true"),
                                  ("05_responses", "raw_response_json", '{"a": 1}'), ("01_items", "context_text_x", None)):
            if cell is None:
                continue
            spec = CODEBOOK.field(table, name)
            self.assertTrue(export.cells_equal(spec, cell, export.to_cell(spec, export.to_value(spec, cell))))
        spec = CODEBOOK.field("02_item_tags", "sub_risk_codes")
        self.assertTrue(export.cells_equal(spec, '["A3.01","A3.02"]', '["A3.01", "A3.02"]'))     # 표기 차이 흡수
        self.assertFalse(export.cells_equal(spec, '["A3.01"]', '["A3.02"]'))
        self.assertIsNone(export.to_value(CODEBOOK.field("04_runs", "first_fail_turn"), ""))
        self.assertEqual(export.to_value(CODEBOOK.field("03_prompts", "context_text"), ""), "")
