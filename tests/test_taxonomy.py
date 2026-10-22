import csv
import subprocess
import sys

import pytest
from conftest import ROOT

from kyab.schemas import M_CODES, RISK_CODES
from kyab.taxonomy import (CROSSWALK_COLUMNS, TAXONOMY_COLUMNS, TaxonomyError, TaxonomyRegistry, load_crosswalk,
                           load_taxonomy)

TAX_DIR = ROOT / "data" / "taxonomy"
DRAFT, LEGACY = "1.0.0", "0.7.0"


@pytest.fixture(scope="module")
def reg():
    return TaxonomyRegistry.from_dir(TAX_DIR)


# --------------------------------------------------------------------------- the generated draft tables

def test_draft_table_shape(reg):
    a = reg.get(DRAFT)
    assert len(a.majors()) == 10 and len(a.subs()) == 39
    assert [c.code for c in a.majors()] == [f"A{i}" for i in range(1, 11)]
    assert {m.code: len(a.subs(m.code)) for m in a.majors()} == {
        "A1": 4, "A2": 4, "A3": 3, "A4": 4, "A5": 4, "A6": 4, "A7": 4, "A8": 3, "A9": 6, "A10": 3}
    assert a.categories["A1"].label == "A1 그루밍·성적 착취"
    assert a.categories["A9.02"].name_ko == "음주·알코올"


def test_pair_check(reg):
    a = reg.get(DRAFT)
    assert a.check_pair("A1", "A1.01") is None
    assert "A2 소속" in a.check_pair("A1", "A2.01")
    assert "없는 소분류" in a.check_pair("A1", "A1.09")
    assert "없는 대분류" in a.check_pair("R1", "A1.01")  # legacy code is not valid in the A version


def test_legacy_table_matches_current_schema_codes(reg):
    legacy = reg.get(LEGACY)
    assert {c.code for c in legacy.majors()} == set(RISK_CODES) | set(M_CODES)
    assert len(legacy.subs()) == 39
    assert legacy.check_pair("M04", "M04.02") is None


def test_crosswalk_is_a_complete_code_mapping_and_only_a_proposal(reg):
    cw = reg.crosswalk(LEGACY, DRAFT)
    assert len(cw.rows) == 49
    assert sorted(r.from_code for r in cw.rows) == sorted(reg.get(LEGACY).categories)  # every legacy code once
    assert sorted(r.to_code for r in cw.rows) == sorted(reg.get(DRAFT).categories)  # every A code once
    assert {r.approval_status for r in cw.rows} == {"proposed"}
    assert [(r.to_code, r.mapping_type) for r in cw.candidates("R1.01")] == [("A1.01", "one_to_one")]
    # M was a boundary-review flag, A6~A10 are categories: never one_to_one
    assert {r.mapping_type for r in cw.rows if r.from_code.startswith("M")} == {"needs_review"}
    assert [r.to_code for r in cw.candidates("M04.02")] == ["A9.02"]
    assert reg.get(DRAFT).by_legacy("M04.02").code == "A9.02"
    assert [r.from_code for r in cw.reverse("A6")] == ["M01"]
    assert cw.candidates("R9") == []


@pytest.mark.skipif(not list((ROOT / "docs").glob("01_*.xlsx")),
                    reason="source xlsx (classification team's workbook) not in this checkout")
def test_build_is_reproducible(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts/build_taxonomy_tables.py"), "--out", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for p in TAX_DIR.glob("*.csv"):
        assert (tmp_path / p.name).read_bytes() == p.read_bytes(), p.name


# ------------------------------------------------------------------------------------- loader checks

def write(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})
    return path


def row(code, level, parent="", v="1.0.0", legacy=""):
    return dict(taxonomy_version=v, code=code, level=level, parent_code=parent, name_ko=code, legacy_code=legacy)


GOOD = [row("A1", "major", legacy="R1"), row("A1.01", "sub", "A1", legacy="R1.01"), row("A2", "major", legacy="R2")]


def test_good_table_loads(tmp_path):
    t = load_taxonomy(write(tmp_path / "t.csv", TAXONOMY_COLUMNS, GOOD))
    assert t.version == "1.0.0" and t.has("A1.01", "sub") and not t.has("A1.01", "major")


@pytest.mark.parametrize("rows,needle", [
    (GOOD + [row("A3", "group")], "level 'group'"),
    (GOOD + [row("A1", "major")], "코드 중복"),
    (GOOD + [row("A2.01", "sub", "A1")], "접두어 A2와 parent_code 'A1'"),
    (GOOD + [row("A3.01", "sub", "A3")], "상위 대분류 'A3'가 이 표에 없음"),
    (GOOD + [row("A1.1", "sub", "A1")], "소분류 코드 형식"),
    (GOOD + [row("A3", "major", parent="A1")], "대분류인데 parent_code"),
    (GOOD + [row("A3", "major", legacy="R1")], "legacy_code R1 중복"),
    (GOOD + [row("A1.02", "sub", "A1", legacy="R2.01")], "legacy R2.01의 상위"),
    (GOOD + [row("A3", "major", v="1.1.0")], "taxonomy_version은 하나"),
    ([dict(r, taxonomy_version="v1") for r in GOOD], "SemVer"),
])
def test_bad_tables_are_rejected(tmp_path, rows, needle):
    with pytest.raises(TaxonomyError, match=needle):
        load_taxonomy(write(tmp_path / "t.csv", TAXONOMY_COLUMNS, rows))


def two_versions(tmp_path):
    write(tmp_path / "taxonomy_RM_0.7.0.csv", TAXONOMY_COLUMNS,
          [row("R1", "major", v="0.7.0"), row("R1.01", "sub", "R1", v="0.7.0")])
    write(tmp_path / "taxonomy_A_1.0.0.csv", TAXONOMY_COLUMNS, GOOD)
    return TaxonomyRegistry.from_dir(tmp_path)


def cw(from_code, to_code, mt="one_to_one", **kw):
    return dict(from_taxonomy_version="0.7.0", from_code=from_code, to_taxonomy_version="1.0.0", to_code=to_code,
                mapping_type=mt, approval_status=kw.get("status", "proposed"), reviewed_by=kw.get("by", ""),
                approved_at=kw.get("at", ""))


@pytest.mark.parametrize("rows,needle", [
    ([cw("R9", "A1")], "0.7.0에 없는 코드 'R9'"),
    ([cw("R1", "A9")], "1.0.0에 없는 코드 'A9'"),
    ([cw("R1", "A1.01")], "층위가 다름"),
    ([cw("R1", "A1", "renamed")], "mapping_type 'renamed'"),
    ([cw("R1", "A1", status="approved")], "approved인데 reviewed_by/approved_at 공란"),
    ([cw("R1", "A1", "no_equivalent")], "no_equivalent인데 to_code"),
    ([cw("R1", "A1"), cw("R1", "A1")], "중복 행"),
])
def test_bad_crosswalks_are_rejected(tmp_path, rows, needle):
    reg = two_versions(tmp_path)
    with pytest.raises(TaxonomyError, match=needle):
        load_crosswalk(write(tmp_path / "x.csv", CROSSWALK_COLUMNS, rows), reg)


def test_split_and_no_equivalent_are_representable(tmp_path):
    reg = two_versions(tmp_path)
    c = load_crosswalk(write(tmp_path / "x.csv", CROSSWALK_COLUMNS, [
        cw("R1", "A1", "split"), cw("R1", "A2", "split"),
        cw("R1.01", "", "no_equivalent", status="approved", by="reviewer", at="2026-10-12")]), reg)
    assert [r.to_code for r in c.candidates("R1")] == ["A1", "A2"]
    with pytest.raises(KeyError):
        reg.crosswalk("0.7.0", "2.0.0")
