"""overlay 로더 검사: apply·add_field 연산, 열 추가 가드, 적용 기록(note 포함)."""
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kyab_runner import paths                                    # noqa: E402
from kyab_runner.codebook import OverlayError, load_codebook    # noqa: E402
from kyab_runner.taxonomy import load_taxonomy                   # noqa: E402

TAXONOMY = load_taxonomy()
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
