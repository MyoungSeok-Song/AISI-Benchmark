"""코드북 열 순서 그대로 CSV를 읽고 쓴다.

형식(C11 기본값): UTF-8(BOM), RFC 4180 따옴표, 줄바꿈 \\r\\n. 셀 안 줄바꿈은 따옴표로 보존.
모든 값은 문자열로 다룬다. 빈 값은 빈 문자열이다.
"""
import csv
import json
import os


class CsvFormatError(Exception):
    """CSV의 열 구성이 코드북과 다르거나 값이 명세에 어긋날 때."""


def to_cell(value):
    """파이썬 값을 CSV 셀 문자열로 바꾼다.

    None -> 빈 값, list·dict -> JSON(한글 그대로), bool -> true/false, 나머지 -> str.
    """
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def read_table(codebook, table, path):
    """CSV를 읽어 행 dict 목록으로 돌려준다. 머리글이 코드북과 정확히 같아야 한다."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        expected = codebook.columns(table)
        if header != expected:
            raise CsvFormatError(_header_diff(path, header or [], expected))
        rows = []
        for line_no, row in enumerate(reader, start=2):
            if not any(row):
                continue                                # 빈 줄은 건너뛴다
            if len(row) != len(expected):               # 따옴표·쉼표가 깨진 행
                raise CsvFormatError(f"{path} {line_no}번째 레코드: 셀 {len(row)}개 (열은 {len(expected)}개)")
            rows.append(dict(zip(expected, row)))
        return rows


def _header_diff(path, header, expected):
    missing = [c for c in expected if c not in header]
    extra = [c for c in header if c not in expected]
    detail = []
    if missing:
        detail.append(f"없는 열 {missing}")
    if extra:
        detail.append(f"코드북에 없는 열 {extra}")
    if not detail:
        detail.append("열 순서가 코드북과 다름")
    return f"{path}: 머리글이 코드북과 다릅니다 — " + ", ".join(detail)


def read_if_exists(codebook, table, path):
    return read_table(codebook, table, path) if os.path.exists(path) else []


def append_rows(codebook, table, path, rows, stages=None):
    """행을 검사한 뒤 파일 끝에 덧붙인다. 파일이 없으면 머리글부터 쓴다.

    하나라도 명세에 어긋나면 아무것도 쓰지 않고 CsvFormatError를 낸다
    (코드북 원칙 3: 허용값만 기록).
    """
    columns = codebook.columns(table)
    cells = [{c: to_cell(row.get(c)) for c in columns} for row in rows]
    for row, cell_row in zip(rows, cells):
        unknown = set(row) - set(columns)
        problems = codebook.check_row(table, cell_row, stages=stages)
        if unknown or problems:
            raise CsvFormatError(f"{table} 기록 거부: 코드북에 없는 열 {sorted(unknown)}, 값 문제 {problems}")

    is_new = not os.path.exists(path)
    # 새 파일만 BOM을 붙인다. 이어 쓸 때 BOM이 중간에 끼면 안 된다.
    with open(path, "a", encoding="utf-8-sig" if is_new else "utf-8", newline="") as f:
        writer = csv.writer(f)      # 기본 dialect = RFC 4180 (\r\n, 필요한 셀만 따옴표)
        if is_new:
            writer.writerow(columns)
        writer.writerows([r[c] for c in columns] for r in cells)
        f.flush()
        os.fsync(f.fileno())


def rewrite_table(codebook, table, path, rows):
    """파일 전체를 다시 쓴다. 임시 파일에 쓴 뒤 바꿔치기해 중간 상태가 남지 않게 한다.

    이미 기록된 행을 고치는 용도가 아니다. 중단된 실행이 남긴 짝 없는 행을
    재시작 때 걷어낼 때만 쓴다(session.discard_orphan_responses).
    """
    columns = codebook.columns(table)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows([row[c] for c in columns] for row in rows)
    os.replace(tmp, path)
