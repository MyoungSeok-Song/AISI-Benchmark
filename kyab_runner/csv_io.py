"""코드북 열 순서 그대로 CSV를 읽고 쓴다.

형식(C11 기본값): UTF-8(BOM), RFC 4180 따옴표, 줄바꿈 \\r\\n. 셀 안 줄바꿈은 따옴표로 보존.
모든 값은 문자열로 다룬다. 빈 값은 빈 문자열이다.
"""
import csv
import json
import os
import sys


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
    """CSV를 읽어 행 dict 목록으로 돌려준다. 머리글이 코드북과 정확히 같아야 한다.

    예외: overlay가 넣은 열(codebook.added_columns)만 빠진 옛 머리글은 읽어 준다. 그 열은 공란으로
    채우고 큰 경고를 낸다(다른 팀이 코드북 원본 머리글로 만든 파일을 막지 않기 위해). 쓰기는 항상 새 머리글이다.
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None) or []
        expected = codebook.columns(table)
        missing_added = [c for c in codebook.added_columns(table) if c not in header]
        if header != expected and header != [c for c in expected if c not in missing_added]:
            raise CsvFormatError(_header_diff(path, header, expected))
        if missing_added:
            print(f"경고: {path}: 옛 머리글({len(header)}열)입니다. overlay가 넣은 열 {missing_added}을 공란으로 채워 읽습니다. "
                  f"이 열이 비면 그 문항은 집계의 위험군 행에서 빠집니다. 새 머리글({len(expected)}열)로 바꿔 주세요.",
                  file=sys.stderr)
        rows = []
        for line_no, row in enumerate(reader, start=2):
            if not any(row):
                continue                                # 빈 줄은 건너뛴다
            if len(row) != len(header):                 # 따옴표·쉼표가 깨진 행
                raise CsvFormatError(f"{path} {line_no}번째 레코드: 셀 {len(row)}개 (열은 {len(header)}개)")
            record = dict(zip(header, row))
            rows.append({c: record.get(c, "") for c in expected})
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


def write_plain_csv(path, columns, rows):
    """열 이름 목록 순서로 CSV를 새로 쓴다(UTF-8 BOM, RFC 4180). 임시 파일에 쓴 뒤 바꿔치기한다.

    코드북 표가 아닌 보조표(results_denominators.csv)도 같은 바이트 규약을 쓰도록 여기 한 곳에 둔다.
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows([row[c] for c in columns] for row in rows)
    os.replace(tmp, path)


def rewrite_table(codebook, table, path, rows):
    """코드북 표 파일 전체를 다시 쓴다(임시 파일 → os.replace로 중간 상태가 남지 않게).

    행 검사는 하지 않으므로 호출자가 검사한 행만 넘긴다. 쓰는 곳:
      session.Batch.discard_orphan_responses   재시작 때 짝 없는 05 행 걷어내기
      judge_io.write_template                  빈 06 틀 재생성
      tools/apply_judgments.py                 판정에서 나온 04 first_fail_turn·first_cfc_turn 채우기(원본은 .bak으로 백업)
      tools/build_samples.py                   샘플 입력 생성
    """
    write_plain_csv(path, codebook.columns(table), rows)
