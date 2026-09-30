"""A1~A10 분류체계와 이전 코드(R/M) 대응표.

코드 목록은 tools/extract_taxonomy.py가 분류팀 xlsx에서 뽑은 파일에서만 읽는다.
이 모듈에 코드 값을 직접 적지 않는다.
"""
import csv
import json
from dataclasses import dataclass

from . import paths


@dataclass(frozen=True)
class Taxonomy:
    """현재 분류체계(A)와 이전 체계(R/M)의 코드 목록."""
    major_codes: tuple          # ('A1', ..., 'A10') 시트 순서
    sub_codes: tuple            # ('A1.01', ...) 시트 순서
    parent_of: dict             # 소분류 코드 -> 대분류 코드
    sort_order: dict            # 대분류 코드 -> 1..10 (문자열 정렬은 A1, A10, A2 순이 되므로 필요)
    legacy_risk_codes: tuple    # 이전 위험군 코드 ('R1', ..., 'R5')
    legacy_m_codes: tuple       # 이전 경계 검토 코드 ('M01', ..., 'M05')

    def is_child(self, sub_code, major_code):
        """소분류가 그 대분류에 속하는가."""
        return self.parent_of.get(sub_code) == major_code


def load_taxonomy(taxonomy_json=paths.TAXONOMY_JSON, crosswalk_csv=paths.CROSSWALK_CSV):
    with open(taxonomy_json, encoding="utf-8") as f:
        data = json.load(f)
    majors = data["majors"]
    parent_of = {s["code"]: m["code"] for m in majors for s in m["subs"]}

    # 이전 코드는 대응표의 대분류 행에서 가져온다. 계열(R/M)은 legacy_scheme 열.
    with open(crosswalk_csv, encoding="utf-8-sig", newline="") as f:
        legacy_majors = [r for r in csv.DictReader(f) if r["level"] == "major"]

    return Taxonomy(
        major_codes=tuple(m["code"] for m in majors),
        sub_codes=tuple(parent_of),
        parent_of=parent_of,
        sort_order={m["code"]: m["sort_order"] for m in majors},
        legacy_risk_codes=tuple(r["legacy_code"] for r in legacy_majors if r["legacy_scheme"] == "R"),
        legacy_m_codes=tuple(r["legacy_code"] for r in legacy_majors if r["legacy_scheme"] == "M"),
    )
