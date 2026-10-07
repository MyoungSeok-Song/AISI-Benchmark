"""ETRI_7CSV_codebook_v<판본>.xlsx -> kyab_runner/spec/codebook_data.py (코드북 명세 모듈).

코드북 xlsx가 유일한 기준이다. 이 스크립트는 01~07 시트의 필드 행을 시트에 적힌 순서 그대로 옮기고,
원문 텍스트를 변형 없이 보존한다. 허용값(enum)·정규식은 '들어갈 수 있는 값·형식' 원문에서 기계적으로만
추출하며, 추출 결과가 애매하면 enum을 비워 두고 원문(format)을 따른다.

  * 열은 위치가 아니라 머리글 이름으로 찾는다(열이 옮겨져도 깨지지 않게).
  * 시트의 '…개 필드' 선언과 읽은 필드 수가 하나라도 어긋나면 모듈을 쓰지 않고 종료 코드 1로 끝낸다.
  * 생성 모듈은 손으로 고치지 않는다. 판본이 바뀌면 CODEBOOK_VERSION만 올리고 다시 돌린다.

실행:  .venv/bin/python tools/extract_codebook.py [--xlsx 경로]   (runner/ 폴더에서)
       기본 원본 위치는 프로젝트 폴더의 'project proposal/'(저장소 밖)이다.
"""
import argparse
import re
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _xlsx_common import SPEC_DIR, file_sha256, nfc, render_module, resolve_xlsx, write_module   # noqa: E402

# ── 판본 (코드북 xlsx 새 판이 오면 여기만 바꾼다) ────────────────────────
CODEBOOK_VERSION = "0.2"
SRC_NAME_KEY = f"codebook_v{CODEBOOK_VERSION}"          # 파일명에 들어 있는 고정 문자열
OUT_MODULE = SPEC_DIR / "codebook_data.py"
COMMAND = "python tools/extract_codebook.py"

# ── 시트 구성 ───────────────────────────────────────────────────────────
# 머리글(xlsx 원문) → 추출본 키. 추출본 키 순서가 곧 필드 dict의 키 순서다.
HEADER_LABELS = [
    ("생성 단계·필수성", "stage"),
    ("AI 전달", "ai_delivery"),
    ("입력 주체", "input_by"),
    ("필드명", "name"),
    ("필드 설명", "description"),
    ("들어갈 수 있는 값·형식", "format"),
    ("필요한 이유", "reason"),
    ("키·연결 관계", "key"),
    ("운영 메모", "memo"),
    ("근거·합의 상태", "agreement"),
]
CSV_SHEETS = ["01_items", "02_item_tags", "03_prompts", "04_runs", "05_responses", "06_judgments", "07_results"]

# ── 허용값·정규식 추출 ──────────────────────────────────────────────────
TOKEN = r"[A-Za-z0-9_\-]+(?:\.[0-9]+)*"


def _values(segment):
    """첫 문장까지만 쓴다: 'a, b, c. 부연 설명' -> ['a', 'b', 'c']."""
    segment = re.split(r"\.\s|。|\s[A-Za-z_]+\s\+", segment)[0]
    vals = [v.strip().rstrip(".") for v in re.split(r",|\s중\s", segment)]
    return [v for v in vals if re.fullmatch(TOKEN, v)]


def parse_enum(fmt):
    """원문에 목록이 명시된 경우만 추출한다. 반환: (kind, values)

    kind = 'scalar'(단일값 통제어휘) | 'array'(JSON 배열 원소 허용값) | None
    """
    m = re.search(r"통제어휘[^:：]*[:：]\s*(.+)", fmt)
    if m:
        vals = _values(m.group(1))
        if vals:
            return "scalar", vals
    m = re.search(r"허용값[:：]\s*(.+)", fmt)
    if m:
        vals = _values(m.group(1))
        if vals:
            return ("array" if "JSON 배열" in fmt else "scalar"), vals
    m = re.search(r"잠정 통제어휘\s+(.+?)\s중", fmt) or re.search(r"잠정[:：]\s*(.+)", fmt)
    if m:
        vals = _values(m.group(1))
        if vals:
            return "scalar", vals
    m = re.search(r"정수\s+([0-9,\s]+)(?:중|$)", fmt)
    if m:
        return "scalar", [int(x) for x in re.findall(r"[0-9]+", m.group(1))]
    return None, None


def parse_regex(fmt):
    """원문 안의 '^…$' 정규식 1개. 없으면 None."""
    m = re.search(r"(\^[^\s$]+\$)", fmt)
    return m.group(1) if m else None


# ── 시트 읽기 ───────────────────────────────────────────────────────────
def cell_text(value):
    return "" if value is None else str(value)


def read_sheet(ws):
    """시트 1개 -> (제목, 부제, 선언 필드 수, 필드 dict 목록). 머리글이 없으면 중단."""
    rows = list(ws.iter_rows(values_only=True))
    title, subtitle = cell_text(rows[0][0]), cell_text(rows[1][0])
    header_index = next((i for i, r in enumerate(rows) if r and "필드명" in [cell_text(c) for c in r]), None)
    if header_index is None:
        sys.exit(f"[{ws.title}] '필드명' 머리글 행을 찾지 못했습니다")
    position = {cell_text(c): i for i, c in enumerate(rows[header_index]) if cell_text(c)}
    missing = [label for label, _ in HEADER_LABELS if label not in position]
    if missing:
        sys.exit(f"[{ws.title}] 머리글 {missing}이 없습니다: {list(position)}")
    fields = []
    for row in rows[header_index + 1:]:
        padded = list(row) + [None] * (len(position) + 1)        # 짧은 행도 같은 길이로
        if not cell_text(padded[position["필드명"]]):
            continue
        field = {key: cell_text(padded[position[label]]) for label, key in HEADER_LABELS}
        kind, values = parse_enum(field["format"])
        field["enum_kind"], field["enum"] = kind, values
        field["regex"] = parse_regex(field["format"])
        fields.append(field)
    m = re.search(r"(\d+)개 필드", subtitle)
    declared = int(m.group(1)) if m else None
    return title, subtitle, declared, fields


# ── 산출물 ──────────────────────────────────────────────────────────────
def build_codebook(xlsx):
    """xlsx -> 추출본 dict. 반환: (dict, 선언·실제 필드 수가 어긋난 시트 목록)."""
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    out = {"source_file": nfc(Path(xlsx).name), "codebook_version": CODEBOOK_VERSION, "tables": {}}
    mismatched = []
    for sheet in CSV_SHEETS:
        title, subtitle, declared, fields = read_sheet(wb[sheet])
        out["tables"][sheet] = {"csv": sheet + ".csv", "title": title, "subtitle": subtitle,
                                "declared_field_count": declared, "fields": fields}
        status = "OK" if declared == len(fields) else "MISMATCH"
        if status == "MISMATCH":
            mismatched.append(sheet)
        print(f"{sheet:14s} 선언 {declared} 읽음 {len(fields)} {status}")
    return out, mismatched


def render(codebook, xlsx):
    doc = ("코드북 명세 데이터(xlsx 추출본). 러너는 이 dict만 읽고 overlay(schema/overlay_v0.3_confirmed.yaml)를 덮어쓴다.\n\n"
           "CODEBOOK['tables'][표 이름]['fields']는 시트 순서 그대로의 필드 dict 목록이다\n"
           "(stage·ai_delivery·input_by·name·description·format·reason·key·memo·agreement·enum_kind·enum·regex).")
    return render_module(doc, xlsx, COMMAND, [
        ("CODEBOOK_VERSION", codebook["codebook_version"]),
        ("SOURCE_FILE", codebook["source_file"]),
        ("SOURCE_SHA256", file_sha256(xlsx)),           # 납품 manifest의 codebook.source에 적는다
        ("CODEBOOK", codebook),
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", help=f"코드북 xlsx 경로 (기본: project proposal/ 아래 '{SRC_NAME_KEY}' 파일)")
    parser.add_argument("--out", type=Path, default=OUT_MODULE, help="생성할 모듈 경로")
    args = parser.parse_args(argv)
    xlsx = resolve_xlsx(args.xlsx, SRC_NAME_KEY)
    codebook, mismatched = build_codebook(xlsx)
    if mismatched:
        sys.exit(f"선언 필드 수와 읽은 필드 수가 다른 시트 {mismatched} — 모듈을 쓰지 않았습니다")
    write_module(args.out, render(codebook, xlsx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
