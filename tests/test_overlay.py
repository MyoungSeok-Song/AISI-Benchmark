"""overlay 로더 검사: apply·add_field 연산, 열 추가 가드, 적용 기록(note 포함)."""
import tempfile
import unittest
from pathlib import Path

import yaml

import support                                                   # noqa: F401 (sys.path 설정)

from kyab_runner import paths, vocab                             # noqa: E402
from kyab_runner.codebook import Codebook, OverlayError, load_codebook   # noqa: E402
from kyab_runner.taxonomy import load_taxonomy                   # noqa: E402

TAXONOMY = load_taxonomy()
CODEBOOK = load_codebook(TAXONOMY)              # 실제 overlay를 적용한 코드북(FieldCheckTest)
CONFIRMED = {"id": "OV-T-CONFIRMED", "status": "confirmed", "date": "2026-10-05",
             "basis": "시험용 확정 항목", "apply": []}
ADD = {"table": "02_item_tags", "field": "test_added_field", "after": "secondary_risks",
       "stage": "조건부", "format": "시험용: A1~A10 중 1개 또는 공란", "enum_from": "taxonomy.major",
       "enum_kind": "scalar", "authorized_by": "OV-T-CONFIRMED"}


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kyab_overlay_"))

    def load(self, *entries):
        path = self.tmp / "overlay.yaml"
        path.write_text(yaml.safe_dump({"base": "codebook_v0.2.json", "changes": list(entries)}, allow_unicode=True),
                        encoding="utf-8")
        return load_codebook(TAXONOMY, overlay_yaml=path)

    def test_no_overlay_file_uses_extract_only(self):
        codebook = load_codebook(TAXONOMY, overlay_yaml=self.tmp / "missing.yaml")
        self.assertEqual(codebook.applied_overlays, [])
        self.assertEqual(len(codebook.columns("02_item_tags")), 29)

    def test_applied_record_keeps_note_and_empty_apply(self):
        """apply가 비어도 항목은 기록되고, note가 함께 남는다(매니페스트의 '잠정' 표시용)."""
        entry = {**CONFIRMED, "note": "이름·위치는 잠정"}
        codebook = self.load(entry)
        self.assertEqual(codebook.applied_overlays,
                         [{"id": "OV-T-CONFIRMED", "status": "confirmed", "basis": "시험용 확정 항목", "note": "이름·위치는 잠정"}])

    def test_add_field_inserts_after_anchor_with_enum(self):
        entry = {"id": "OV-T-P", "status": "provisional", "date": "2026-10-05", "basis": "잠정", "add_field": [ADD]}
        codebook = self.load(CONFIRMED, entry)
        columns = codebook.columns("02_item_tags")
        self.assertEqual(len(columns), 30)
        self.assertEqual(columns[columns.index("secondary_risks") + 1], "test_added_field")
        spec = codebook.field("02_item_tags", "test_added_field")
        self.assertEqual((spec.added_by, spec.required, spec.enum[:2]), ("OV-T-P", False, ("A1", "A2")))
        self.assertIsNone(spec.check("A3"))
        self.assertIsNotNone(spec.check("R1"))
        self.assertEqual(codebook.added_columns("02_item_tags"), ["test_added_field"])

    def test_enum_without_kind_still_checks_values(self):
        """enum_from·enum_add·enum에 enum_kind를 적지 않아도 허용값 검사가 꺼지지 않는다(기본 scalar)."""
        no_kind = {k: v for k, v in ADD.items() if k != "enum_kind"}
        codebook = self.load(CONFIRMED, {"id": "OV-T-P", "status": "provisional", "basis": "x", "add_field": [no_kind]})
        spec = codebook.field("02_item_tags", "test_added_field")
        self.assertEqual(spec.enum_kind, "scalar")
        self.assertIsNotNone(spec.check("garbage"))
        self.assertIsNone(spec.check("A1"))
        plain = {**no_kind, "enum": ["X", "Y"]}
        plain.pop("enum_from")
        spec = self.load(CONFIRMED, {"id": "OV-T-P", "status": "provisional", "basis": "x", "add_field": [plain]}).field(
            "02_item_tags", "test_added_field")
        self.assertIsNotNone(spec.check("Z"))
        # apply로 허용값을 더해도 같다 (sub_risk_codes는 array 종류를 유지)
        codebook = self.load({**CONFIRMED, "apply": [{"table": "01_items", "field": "scenario_type", "enum": ["s1"]}]})
        self.assertEqual(codebook.field("01_items", "scenario_type").enum_kind, "scalar")
        self.assertIsNotNone(codebook.field("01_items", "scenario_type").check("other"))

    def test_project_overlay_records_all_four_replies(self):
        """2026-10-05 회신 ①~④가 confirmed로 기록돼 매니페스트에 남는다. ④는 apply가 없다."""
        codebook = load_codebook(TAXONOMY, overlay_yaml=paths.OVERLAY_YAML)
        by_id = {e["id"]: e for e in codebook.applied_overlays}
        for n in (1, 2, 3, 4):
            self.assertEqual(by_id[f"OV-R1005-{n}"]["status"], "confirmed")
            self.assertIn("코드북 담당 회신(2026-10-05)", by_id[f"OV-R1005-{n}"]["basis"])
        self.assertIn("잠정", by_id["OV-R1005-4"]["note"])
        self.assertIn("잠정", by_id["OV-P4"]["basis"])
        self.assertEqual(codebook.field("06_judgments", "critical_failure_code").format, "승인된 CFC 코드 또는 NONE(치명적 실패 없음)")

    def test_add_field_requires_confirmed_authorization(self):
        """회신으로 확정된 항목 없이는 열을 늘릴 수 없다."""
        unauthorized = {**ADD, "authorized_by": None}
        with self.assertRaises(OverlayError) as ctx:
            self.load({"id": "OV-T-P", "status": "provisional", "basis": "잠정", "add_field": [unauthorized]})
        self.assertIn("회신", str(ctx.exception))
        provisional_target = {**ADD, "authorized_by": "OV-T-OTHER"}
        with self.assertRaises(OverlayError):
            self.load({"id": "OV-T-OTHER", "status": "provisional", "basis": "x", "apply": []},
                      {"id": "OV-T-P", "status": "provisional", "basis": "잠정", "add_field": [provisional_target]})

    def test_add_field_rejects_duplicates_and_unknown_anchor(self):
        with self.assertRaises(OverlayError):
            self.load(CONFIRMED, {"id": "OV-T-P", "status": "provisional", "basis": "x",
                                  "add_field": [{**ADD, "field": "primary_risk"}]})
        with self.assertRaises(OverlayError):
            self.load(CONFIRMED, {"id": "OV-T-P", "status": "provisional", "basis": "x",
                                  "add_field": [{**ADD, "after": "no_such_field"}]})

    def test_apply_unknown_field_is_an_error(self):
        with self.assertRaises(OverlayError):
            self.load({**CONFIRMED, "apply": [{"table": "04_runs", "field": "no_such_field", "required": False}]})

    def test_apply_format_and_none_token(self):
        entry = {**CONFIRMED, "apply": [{"table": "06_judgments", "field": "critical_failure_code",
                                         "format": "승인된 CFC 코드 또는 NONE", "none_token": "NONE"}]}
        spec = self.load(entry).field("06_judgments", "critical_failure_code")
        self.assertEqual((spec.format, spec.none_token), ("승인된 CFC 코드 또는 NONE", "NONE"))

    def test_project_overlay_loads(self):
        """실제 overlay 파일이 로드되고 모든 항목이 기록된다."""
        codebook = load_codebook(TAXONOMY, overlay_yaml=paths.OVERLAY_YAML)
        ids = [e["id"] for e in codebook.applied_overlays]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all("note" in e for e in codebook.applied_overlays))


class FieldCheckTest(unittest.TestCase):
    """코드북 형식 원문 해석(cross-06): '소수'는 수, 범위('0.0-1.0', '0 이상')는 check가 본다."""

    def test_rate_fields_are_numbers_with_bounds(self):
        spec = CODEBOOK.field("07_results", "failure_rate")
        self.assertEqual((spec.value_type, spec.bounds), ("number", (0.0, 1.0)))
        self.assertIn("형식 아님", spec.check("abc"))
        self.assertIn("범위 밖", spec.check("1.5"))
        self.assertIsNone(spec.check("0.454545"))
        self.assertIsNone(spec.check(""))                        # 분모 0이면 빈값

    def test_minimum_bound_on_int_fields(self):
        spec = CODEBOOK.field("04_runs", "actual_turn_count")
        self.assertEqual((spec.value_type, spec.bounds), ("int", (0.0, None)))
        self.assertIn("범위 밖", spec.check("-1"))
        self.assertIsNone(spec.check("0"))

    def test_is_json_property(self):
        self.assertTrue(CODEBOOK.field("05_responses", "request_messages_json").is_json)
        self.assertTrue(CODEBOOK.field("07_results", "slice_key_json").is_json)
        self.assertFalse(CODEBOOK.field("04_runs", "temperature").is_json)


class VocabularyTest(unittest.TestCase):
    """코드가 쓰는 통제어휘(vocab.USED)는 코드북 enum에 있어야 하고, 없어지면 로드가 멈춘다(cross-15)."""

    def test_current_codebook_has_every_used_value(self):
        vocab.check_vocabulary(CODEBOOK)                 # 예외 없음

    def test_missing_value_is_reported(self):
        import dataclasses
        tables = {t: list(CODEBOOK.fields(t)) for t in ("01_items", "02_item_tags", "03_prompts", "04_runs",
                                                        "05_responses", "06_judgments", "07_results")}
        tables["06_judgments"] = [dataclasses.replace(f, enum=("pass", "inconclusive")) if f.name == "verdict" else f
                                  for f in tables["06_judgments"]]
        broken = Codebook(tables, CODEBOOK.applied_overlays)
        with self.assertRaises(vocab.VocabularyError) as caught:
            vocab.check_vocabulary(broken)
        self.assertIn("06_judgments.verdict='fail'", str(caught.exception))
        self.assertIn("SetupError", [c.__name__ for c in type(caught.exception).__mro__])


class TagSchemeDispatchTest(unittest.TestCase):
    """태그 규칙 호출 순서 고정(codebook-08): 이전·새 체계 오류가 섞인 입력의 02 Issue 목록이 분리 전과 같다."""

    EXPECTED = [
        ("error", "KYAB-900003@1.0.0#rev1", "m_review_status", "허용값 아님: 'pending' (허용: unreviewed, issue_found, no_issue, hold, out_of_list)"),
        ("error", "KYAB-900101@1.0.0#rev1", "taxonomy_version", "MAJOR.MINOR.PATCH 형식 아님: 'x.0.0'"),
        ("error", "KYAB-900001@1.0.0#rev1", "primary_risk", "이전 체계 행인데 R 코드가 아님: 'A1'"),
        ("error", "KYAB-900001@1.0.0#rev1", "secondary_risks", "이전 체계 행의 허용값 아님: ['R9']"),
        ("error", "KYAB-900001@1.0.0#rev1", "m_review_codes", "이전 체계 행의 허용값 아님: ['M99']"),
        ("error", "KYAB-900001@1.0.0#rev1", "sub_risk_codes", "이전 체계에서는 세부 코드를 쓰지 않음(v0.2 '추후 확정')"),
        ("error", "KYAB-900001@1.0.0#rev1", "m_review_status", "이전 체계 행은 필수(v0.2)"),
        ("error", "KYAB-900003@1.0.0#rev1", "sub_risk_codes", "A2.01는 A1의 소분류가 아님 (소속: A2)"),
        ("error", "KYAB-900003@1.0.0#rev1", "secondary_risks", "주대분류 A1가 보조 위험에 다시 들어 있음"),
        ("error", "KYAB-900003@1.0.0#rev1", "m_review_codes", "새 분류체계 행에서는 비워 둠 (M01~M05는 A6~A10으로 편입)"),
        ("error", "KYAB-900003@1.0.0#rev1", "m_review_status", "새 분류체계 행에서는 비워 둠 (M01~M05는 A6~A10으로 편입)"),
    ]

    def test_issue_order_is_unchanged(self):
        import copy
        from kyab_runner import csv_io, validate
        from kyab_runner.config import load_config
        from kyab_runner.records import INPUT_FILES
        tables = {t: csv_io.read_table(CODEBOOK, t, paths.DEFAULT_INPUT_DIR / f) for t, f in INPUT_FILES.items()}
        tags = copy.deepcopy(tables["02_item_tags"])
        legacy = next(r for r in tags if r["item_id"] == "KYAB-900001" and r["tag_status"] == "current")
        legacy.update(taxonomy_version="0.1.0", primary_risk="A1", secondary_risks='["R9"]', sub_risk_codes='["A1.01"]',
                      m_review_codes='["M01", "M99"]', m_review_status="", risk_review_status="mapped")
        new = next(r for r in tags if r["item_id"] == "KYAB-900003" and r["tag_status"] == "current")
        new.update(primary_risk="A1", sub_risk_codes='["A2.01"]', secondary_risks='["A1"]', m_review_status="pending",
                   m_review_codes='["M01"]', risk_review_status="mapped")
        unreadable = next(r for r in tags if r["item_id"] == "KYAB-900101" and r["tag_status"] == "current")
        unreadable.update(taxonomy_version="x.0.0")
        issues = validate.validate_inputs(CODEBOOK, TAXONOMY, load_config(), tables["01_items"], tags, tables["03_prompts"])
        got = [(i.level, i.key, i.field, i.message) for i in issues
               if i.table == "02_item_tags" and any(k in i.key for k in ("900001", "900003", "900101"))]
        self.assertEqual(got, self.EXPECTED)
        self.assertEqual(validate._scheme_of(legacy), validate.SCHEME_LEGACY)
        self.assertEqual(validate._scheme_of(new), validate.SCHEME_NEW)
        self.assertIsNone(validate._scheme_of(unreadable))


class TaxonomyLoadTest(unittest.TestCase):
    def test_missing_legacy_codes_is_a_setup_error(self):
        from kyab_runner.spec import taxonomy_data
        from kyab_runner.taxonomy import TaxonomyError
        without_m = [r for r in taxonomy_data.CROSSWALK if r["legacy_scheme"] != "M"]
        with self.assertRaises(TaxonomyError):
            load_taxonomy(crosswalk=without_m)
        self.assertFalse(hasattr(TAXONOMY, "sort_order"))       # 읽는 곳이 없던 필드 제거


class OverlayStructureTest(OverlayTest):
    """overlay 파일 구조 오류(codebook-03)는 traceback 대신 항목 ID가 든 OverlayError."""

    def load_raw(self, text):
        path = self.tmp / "overlay_raw.yaml"
        path.write_text(text, encoding="utf-8")
        return load_codebook(TAXONOMY, overlay_yaml=path)

    def test_structure_problems(self):
        for text in ("", "changes:\n", "base: x\n", "changes:\n  - 3\n"):
            with self.assertRaises(OverlayError):
                self.load_raw(text)
        with self.assertRaises(OverlayError) as caught:
            self.load(CONFIRMED, {"id": "OV-T-BAD", "status": "provisional", "basis": "시험",
                                  "apply": [{"table": "99_nope", "field": "x", "enum": ["a"]}]})
        self.assertIn("OV-T-BAD", str(caught.exception))
        with self.assertRaises(OverlayError) as caught:
            self.load(CONFIRMED, {"id": "OV-T-BAD2", "status": "provisional", "basis": "시험",
                                  "apply": [{"table": "02_item_tags", "field": "primary_risk", "enum_from": "taxonomy.bogus"}]})
        self.assertIn("OV-T-BAD2", str(caught.exception))
        with self.assertRaises(OverlayError):
            self.load(CONFIRMED, {"id": "OV-T-BAD3", "status": "provisional", "basis": "시험", "apply": "not-a-list"})


class StrictNumberTest(unittest.TestCase):
    """수 형식(codebook-15): ASCII 숫자 표기만. int()·float()가 받아들이던 공백·밑줄·부호·nan·inf는 거부."""

    def test_int_and_number(self):
        turn_index = CODEBOOK.field("03_prompts", "turn_index")
        for bad in (" 7", "1_000", "+7", "７", "nan"):
            self.assertIn("형식 아님", turn_index.check(bad) or "", bad)
        for good in ("0", "1", "12"):
            self.assertIsNone(turn_index.check(good), good)
        temperature = CODEBOOK.field("04_runs", "temperature")
        for bad in ("nan", "inf", "1e3", " 0.0"):
            self.assertIn("형식 아님", temperature.check(bad) or "", bad)
        for good in ("0.0", "1.0", "0", "-1.5"):
            self.assertNotIn("형식 아님", temperature.check(good) or "", good)

    def test_unknown_field_is_keyerror_with_message(self):
        with self.assertRaises(KeyError) as caught:
            CODEBOOK.field("06_judgments", "no_such_field")
        self.assertIn("no_such_field", str(caught.exception))
        with self.assertRaises(KeyError):
            CODEBOOK.field("99_nope", "x")


class LegacyJsonShapeTest(unittest.TestCase):
    """이전 체계 행(codebook-02): 분류 코드 필드의 깨진 JSON·배열 아닌 값은 오류로 잡힌다."""

    def test_malformed_legacy_cells_are_errors(self):
        import copy
        from kyab_runner import csv_io, validate
        from kyab_runner.config import load_config
        from kyab_runner.records import INPUT_FILES
        tables = {t: csv_io.read_table(CODEBOOK, t, paths.DEFAULT_INPUT_DIR / f) for t, f in INPUT_FILES.items()}
        tags = copy.deepcopy(tables["02_item_tags"])
        legacy = next(r for r in tags if r["taxonomy_version"].startswith("0."))
        key = f"{legacy['item_id']}@{legacy['item_version']}#rev{legacy['tag_revision']}"
        legacy.update(sub_risk_codes="[", secondary_risks="[R2", m_review_codes="{bad")
        issues = validate.validate_inputs(CODEBOOK, TAXONOMY, load_config(), tables["01_items"], tags, tables["03_prompts"])
        fields = sorted(i.field for i in issues if i.key == key and i.level == "error")
        self.assertEqual(fields, ["m_review_codes", "secondary_risks", "sub_risk_codes"])
        legacy.update(sub_risk_codes='"R2"', secondary_risks='{"a": 1}', m_review_codes="3")
        issues = validate.validate_inputs(CODEBOOK, TAXONOMY, load_config(), tables["01_items"], tags, tables["03_prompts"])
        self.assertEqual(sorted(i.field for i in issues if i.key == key and i.level == "error"),
                         ["m_review_codes", "secondary_risks", "sub_risk_codes"])
