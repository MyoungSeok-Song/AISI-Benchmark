"""A1~A10 분류체계와 이전 코드(R/M) 대응표.

코드 목록은 tools/extract_taxonomy.py가 분류팀 xlsx에서 뽑은 생성 모듈(spec/taxonomy_data.py)에서만 읽는다.
이 모듈에 코드 값을 직접 적지 않는다.
"""
from dataclasses import dataclass

from .errors import SetupError
from .spec import taxonomy_data


class TaxonomyError(SetupError):
    """분류체계 데이터가 러너가 기대하는 꼴이 아닐 때(대응표에 이전 코드가 없음 등)."""


@dataclass(frozen=True)
class Taxonomy:
    """현재 분류체계(A)와 이전 체계(R/M)의 코드 목록."""
    major_codes: tuple          # ('A1', ..., 'A10') 시트 순서
    sub_codes: tuple            # ('A1.01', ...) 시트 순서
    parent_of: dict             # 소분류 코드 -> 대분류 코드
    legacy_risk_codes: tuple    # 이전 위험군 코드 ('R1', ..., 'R5')
    legacy_m_codes: tuple       # 이전 경계 검토 코드 ('M01', ..., 'M05')

    def is_child(self, sub_code, major_code):
        """소분류가 그 대분류에 속하는가."""
        return self.parent_of.get(sub_code) == major_code


def load_taxonomy(taxonomy=taxonomy_data.TAXONOMY, crosswalk=taxonomy_data.CROSSWALK):
    """생성 모듈의 분류체계 dict와 대응표 행 목록 -> Taxonomy. 인자는 시험용 치환 자리다.

    taxonomy["majors"]는 시트 순서의 대분류(각각 subs에 소분류). crosswalk는 이전 코드 행(level·legacy_scheme·legacy_code·new_code).
    이전 코드는 대응표의 대분류 행에서 가져온다(계열 R/M은 legacy_scheme 열). 하나라도 비면 이전 체계 행 검사가 전부
    엉뚱한 메시지로 실패하므로 여기서 TaxonomyError로 멈춘다.
    """
    majors = taxonomy["majors"]
    parent_of = {s["code"]: m["code"] for m in majors for s in m["subs"]}
    legacy_majors = [r for r in crosswalk if r["level"] == "major"]
    result = Taxonomy(
        major_codes=tuple(m["code"] for m in majors),
        sub_codes=tuple(parent_of),
        parent_of=parent_of,
        legacy_risk_codes=tuple(r["legacy_code"] for r in legacy_majors if r["legacy_scheme"] == "R"),
        legacy_m_codes=tuple(r["legacy_code"] for r in legacy_majors if r["legacy_scheme"] == "M"),
    )
    if not result.legacy_risk_codes or not result.legacy_m_codes:
        raise TaxonomyError("대응표에서 이전 R/M 대분류 코드를 찾지 못함: 생성 모듈 CROSSWALK의 level·legacy_scheme 열 확인")
    return result
