"""The delivered codebook v0.3 draft (scripts/build_codebook_v03.py) describes exactly what the runner implements."""
import importlib.util

import openpyxl
from conftest import ROOT

from kyab.schemas import PERSONA_PROMPTS, PERSONAS, TABLES

EXTRA_COLUMNS = {"item_tags": ["control_target_risk"]}  # documented, read when present, not required by the runner


def build(tmp_path):
    spec = importlib.util.spec_from_file_location("bcb", ROOT / "scripts" / "build_codebook_v03.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "cb.xlsx"
    assert mod.main(["--out", str(out)]) == 0
    return openpyxl.load_workbook(out)


def rows(ws):
    return [r for r in ws.iter_rows(min_row=4, values_only=True) if r[3]]


def test_table_sheets_match_schema(tmp_path):
    wb = build(tmp_path)
    for name, t in TABLES.items():
        xl = rows(wb[t.sheet])
        fields = [r[3] for r in xl if r[3] not in EXTRA_COLUMNS.get(name, [])]
        assert fields == t.columns, name
        by = {r[3]: r for r in xl}
        for f in t.fields:
            assert (by[f.name][0], by[f.name][1]) == (f.req.value, f.ai.value), f"{name}.{f.name}"
        assert wb[t.sheet]["A2"].value.startswith(f"v0.3(안) 기준 · {len(xl)}개 필드")
    tags = [r[3] for r in rows(wb["02_item_tags"])]
    assert tags[tags.index("secondary_risks") + 1] == "control_target_risk"


def test_persona_extension_sheet_matches_schema(tmp_path):
    xl = rows(build(tmp_path)["09_personas(확장)"])
    fields = [r[3] for r in xl]
    assert fields == PERSONAS.columns + PERSONA_PROMPTS.columns
