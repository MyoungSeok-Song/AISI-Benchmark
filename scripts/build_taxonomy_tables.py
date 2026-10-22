"""Build the taxonomy reference tables and the R/M → A crosswalk from the codebook sheet.

    .venv/bin/python scripts/build_taxonomy_tables.py [--xlsx docs/01_*.xlsx] [--version 1.0.0]

Source: 01_수연팀_벤치마크비교와_재분류.xlsx, sheet 코드북49 (10 major + 39 sub, "이전 코드" column).
Writes data/taxonomy/:
- taxonomy_A_<version>.csv      A1~A10 / A1.01… with names, definitions, include/exclude, legacy code
- taxonomy_RM_0.7.0.csv         legacy R/M code list, derived from the "이전 코드" column (codes only)
- crosswalk_0.7.0_to_<version>.csv   code-system mapping, every row approval_status=proposed
- manifest.yaml                 source file hash, sheet, version note
A1~A10 were confirmed at the 2026-10 project meeting → version 1.0.0 (MAJOR ≥ 1 = A scheme).
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import openpyxl
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kyab.schemas import M_CODES, RISK_CODES  # noqa: E402
from kyab.taxonomy import CROSSWALK_COLUMNS, TAXONOMY_COLUMNS, TaxonomyRegistry  # noqa: E402

LEGACY_VERSION = "0.7.0"  # current tags (태그파일 v0.7)
SHEET = "코드북49"
HEADER = ("층위", "코드", "대분류 코드와 이름", "명칭", "정의", "포함·경계", "출처", None, "이전 코드")
NOTE_ALL = "코드 체계 대응. 개별 문항의 최종 A분류를 자동 변환하지 않음 (원문 재검토 후 확정)"
NOTE_M = "M은 과거 경계 검토 표시, A6~A10은 정식 분류 후보 (D1·D2) — 의미가 달라 재검토 필요"


def write_csv(path: Path, columns, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", help="기본: docs/01_*.xlsx (파일명이 NFD라 glob으로 찾음)")
    ap.add_argument("--version", default="1.0.0", help="A 기준표 taxonomy_version (MAJOR 1 이상 = A1~A10 체계, 0 = 이전 R/M)")
    ap.add_argument("--out", default=str(ROOT / "data" / "taxonomy"))
    args = ap.parse_args(argv)
    xlsx = Path(args.xlsx) if args.xlsx else Path(sorted(glob.glob(str(ROOT / "docs" / "01_*.xlsx")))[0])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    ws = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)[SHEET]
    rows = [r for r in ws.iter_rows(values_only=True) if any(c not in (None, "") for c in r)]
    if tuple(rows[0][:9]) != HEADER:
        sys.exit(f"{SHEET} 헤더가 예상과 다름: {rows[0]}")

    def s(v):
        return "" if v is None else str(v).strip()

    a_rows, legacy_rows, cw_rows = [], [], []
    for r in rows[1:]:
        layer, code, parent_label, name, definition, incl, legacy = (s(r[0]), s(r[1]), s(r[2]), s(r[3]), s(r[4]),
                                                                     s(r[5]), s(r[8]))
        level = {"상위": "major", "하위": "sub"}[layer]
        parent = "" if level == "major" else parent_label.split()[0]
        a_rows.append(dict(taxonomy_version=args.version, code=code, level=level, parent_code=parent,
                           name_ko=name, definition=definition, include_exclude=incl, legacy_code=legacy))
        legacy_parent = "" if level == "major" else legacy.split(".")[0]
        legacy_rows.append(dict(taxonomy_version=LEGACY_VERSION, code=legacy, level=level, parent_code=legacy_parent,
                                name_ko="", definition="", include_exclude="", legacy_code=""))
        cw_rows.append(dict(from_taxonomy_version=LEGACY_VERSION, from_code=legacy, to_taxonomy_version=args.version,
                            to_code=code, mapping_type="needs_review" if legacy.startswith("M") else "one_to_one",
                            note=NOTE_M if legacy.startswith("M") else NOTE_ALL, reviewed_by="",
                            approval_status="proposed", approved_at=""))

    legacy_majors = {r["code"] for r in legacy_rows if r["level"] == "major"}
    if legacy_majors != set(RISK_CODES) | set(M_CODES):
        sys.exit(f"이전 코드 대분류 {sorted(legacy_majors)} ≠ schemas의 R/M 목록")

    a_name = f"taxonomy_A_{args.version}.csv"
    cw_name = f"crosswalk_{LEGACY_VERSION}_to_{args.version}.csv"
    for stale in list(out.glob("taxonomy_A_*.csv")) + list(out.glob(f"crosswalk_{LEGACY_VERSION}_to_*.csv")):
        if stale.name not in (a_name, cw_name):
            stale.unlink()  # one A version at a time
    write_csv(out / a_name, TAXONOMY_COLUMNS, a_rows)
    write_csv(out / f"taxonomy_RM_{LEGACY_VERSION}.csv", TAXONOMY_COLUMNS, legacy_rows)
    write_csv(out / cw_name, CROSSWALK_COLUMNS, cw_rows)

    reg = TaxonomyRegistry.from_dir(out)  # every table must load and cross-check
    a, cw = reg.get(args.version), reg.crosswalk(LEGACY_VERSION, args.version)
    manifest = {
        "source_file": unicodedata.normalize("NFC", xlsx.name),  # docs copy has an NFD name
        "source_sha256": hashlib.sha256(xlsx.read_bytes()).hexdigest(),
        "source_sheet": SHEET, "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "builder": "scripts/build_taxonomy_tables.py",
        "tables": {a_name: {"taxonomy_version": args.version, "major": len(a.majors()), "sub": len(a.subs())},
                   f"taxonomy_RM_{LEGACY_VERSION}.csv": {"taxonomy_version": LEGACY_VERSION,
                                                        "note": "이전 코드 열에서 코드만 추출. 0.7.0 원본 명칭·정의는 미수록"},
                   cw_name: {"rows": len(cw.rows), "approval_status": "all proposed"}},
        "status": ("A1~A10 확정(2026-10 회의). taxonomy_version MAJOR ≥ 1 = A 체계, 0 = 이전 R/M (팀 러너와 같은 규칙). "
                   "item_tags 검증기가 이 기준표로 primary_risk·secondary_risks·sub_risk_codes·control_target_risk를 검사. "
                   "crosswalk는 코드 체계 대응일 뿐 문항 자동 재분류가 아님(전부 proposed)"),
    }
    (out / "manifest.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"{a_name}: 대분류 {len(a.majors())}, 소분류 {len(a.subs())}")
    print(f"{cw_name}: {len(cw.rows)}행 (one_to_one {sum(r.mapping_type == 'one_to_one' for r in cw.rows)}, "
          f"needs_review {sum(r.mapping_type == 'needs_review' for r in cw.rows)}, 전부 proposed)")
    print(f"출력: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
