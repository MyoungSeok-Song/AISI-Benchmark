"""Write a fixed copy of a dataset directory: UTF-8 without BOM, every CSV cell in Unicode NFC.

Fixes only what text_qa reports as mechanical errors (file encoding, NFD/non-NFC text). It does not
touch whitespace, zero-width or other invisible characters, or wording — those stay human decisions.
The source directory is never modified; the destination must not exist yet.

    .venv/bin/python scripts/normalize_inputs.py SRC_DIR DST_DIR [--source-encoding cp949]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import sys
import unicodedata
from collections import Counter
from pathlib import Path

csv.field_size_limit(1 << 30)


def decode(data: bytes, forced: str | None) -> tuple[str, str]:
    if forced:
        return data.decode(forced), forced
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    for enc in ("utf-8", "cp949"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise SystemExit("UTF-8도 CP949도 아님 — --source-encoding으로 지정")


def normalize_csv(src: Path, dst: Path, forced: str | None) -> dict:
    text, enc = decode(src.read_bytes(), forced)
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header = rows[0] if rows else []
    changed: Counter = Counter()
    out = []
    for r_i, row in enumerate(rows):
        new = []
        for c_i, cell in enumerate(row):
            fixed = unicodedata.normalize("NFC", cell)
            if fixed != cell:
                changed[header[c_i] if r_i and c_i < len(header) else "(header)"] += 1
            new.append(fixed)
        out.append(new)
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\r\n" if "\r\n" in text else "\n").writerows(out)
    dst.write_text(buf.getvalue(), encoding="utf-8", newline="")
    return {"source_encoding": enc, "rows": max(len(rows) - 1, 0), "nfc_changed_cells": dict(changed)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--source-encoding", help="자동 판별(UTF-8 → CP949) 대신 지정, 예: cp949")
    args = ap.parse_args(argv)
    src, dst = Path(args.src).resolve(), Path(args.dst).resolve()
    if not src.is_dir():
        sys.exit(f"원본 폴더 없음: {src}")
    if dst.exists():
        sys.exit(f"대상 폴더가 이미 있음 (덮어쓰지 않음): {dst}")
    dst.mkdir(parents=True)
    report = {"source_dir": str(src), "files": {}}
    for p in sorted(src.iterdir()):
        if p.is_file() and p.suffix.lower() == ".csv":
            report["files"][p.name] = normalize_csv(p, dst / p.name, args.source_encoding)
        elif p.is_file():
            shutil.copy2(p, dst / p.name)
    (dst / "normalize_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
    for name, r in report["files"].items():
        n = sum(r["nfc_changed_cells"].values())
        print(f"{name}: {r['source_encoding']} → utf-8, {r['rows']}행, NFC 변경 {n}셀 {r['nfc_changed_cells'] or ''}")
    print(f"출력: {dst} (보이지 않는 문자·공백·문구는 손대지 않음 — 검증 경고를 사람이 확인)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
