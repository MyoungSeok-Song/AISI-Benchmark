"""ETRI_7CSV_codebook_v0.2.xlsx -> runner/schema/codebook_v0.2.json

코드북 xlsx가 유일한 기준이다. 이 스크립트는 01~07 시트의 필드 행을
시트에 적힌 순서 그대로 옮기고, 원문 텍스트를 변형 없이 보존한다.
허용값(enum)·정규식은 '들어갈 수 있는 값·형식' 원문에서 기계적으로만 추출하며,
추출 결과가 애매하면 enum을 비워 두고 원문(format)을 따른다.
"""
import glob, json, os, re, sys, unicodedata
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC_DIR = os.path.join(ROOT, "project proposal")
OUT = os.path.join(ROOT, "runner", "schema", "codebook_v0.2.json")

HEADER = ["stage", "ai_delivery", "input_by", "name", "description",
          "format", "reason", "key", "memo", "agreement"]
CSV_SHEETS = ["01_items", "02_item_tags", "03_prompts", "04_runs",
              "05_responses", "06_judgments", "07_results"]

def find_xlsx():
    for f in glob.glob(os.path.join(SRC_DIR, "*.xlsx")):
        if "codebook_v0.2" in unicodedata.normalize("NFC", os.path.basename(f)):
            return f
    sys.exit("codebook v0.2 xlsx not found")

TOKEN = r"[A-Za-z0-9_\-]+(?:\.[0-9]+)*"

def _values(segment):
    # 첫 문장까지만 사용: "a, b, c. 부연 설명" -> "a, b, c"
    segment = re.split(r"\.\s|。|\s[A-Za-z_]+\s\+", segment)[0]
    vals = [v.strip().rstrip(".") for v in re.split(r",|\s중\s", segment)]
    return [v for v in vals if re.fullmatch(TOKEN, v)]

def parse_enum(fmt):
    """원문에 목록이 명시된 경우만 추출한다. 반환: (kind, values)
    kind = 'scalar'(단일값 통제어휘) | 'array'(JSON 배열 원소 허용값) | None"""
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
    m = re.search(r"(\^[^\s$]+\$)", fmt)
    return m.group(1) if m else None

def main():
    xlsx = find_xlsx()
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    out = {"source_file": os.path.basename(xlsx), "codebook_version": "0.2", "tables": {}}
    for sheet in CSV_SHEETS:
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        title = rows[0][0]
        subtitle = rows[1][0]
        hdr_idx = next(i for i, r in enumerate(rows) if r and r[3] == "필드명")
        fields = []
        for r in rows[hdr_idx + 1:]:
            if not r or not r[3]:
                continue
            d = {k: ("" if v is None else str(v)) for k, v in zip(HEADER, r)}
            kind, vals = parse_enum(d["format"])
            d["enum_kind"], d["enum"] = kind, vals
            d["regex"] = parse_regex(d["format"])
            fields.append(d)
        m = re.search(r"(\d+)개 필드", subtitle or "")
        declared = int(m.group(1)) if m else None
        out["tables"][sheet] = {"csv": sheet + ".csv", "title": title, "subtitle": subtitle,
                                "declared_field_count": declared, "fields": fields}
        status = "OK" if declared == len(fields) else "MISMATCH"
        print(f"{sheet:14s} declared={declared} parsed={len(fields)} {status}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("wrote", OUT)

if __name__ == "__main__":
    main()
