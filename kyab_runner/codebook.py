"""코드북 명세 로드와 값 검사.

명세의 출처는 두 파일뿐이다.
  schema/codebook_v0.2.json          코드북 xlsx 추출본 (tools/extract_codebook.py)
  schema/overlay_v0.3_confirmed.yaml v0.3 xlsx가 오기 전까지의 확정·가정 변경

필드 이름·순서·허용값·정규식을 코드에 적지 않는다. 코드북이 바뀌면 추출만 다시 한다.
"""
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime

import yaml

from . import paths

# ── 값 종류 ─────────────────────────────────────────────────────────────
# 코드북 '들어갈 수 있는 값·형식' 원문에서 기계적으로 추론한다. 위에서부터 먼저 맞는 것.
_TYPE_KEYWORDS = [
    ("JSON 배열", "json_array"),
    ("JSON 객체", "json_object"),
    ("타임스탬프", "timestamp"),
    ("ISO 8601 날짜", "date"),
    ("SemVer", "semver"),
    ("MAJOR.MINOR.PATCH", "semver"),
    ("boolean", "bool"),
    ("정수", "int"),
    ("숫자", "number"),
]
_RE_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _infer_type(fmt):
    for keyword, value_type in _TYPE_KEYWORDS:
        if keyword in fmt:
            return value_type
    return "text"


@dataclass(frozen=True)
class FieldSpec:
    """코드북의 필드 1개."""
    table: str
    name: str
    stage: str            # '생성 단계·필수성' 원문. 예: '실행 자동기록 필수', '조건부'
    ai_delivery: str      # 'AI 전달' 원문: ○(항상) · △(해당 시) · ×(보내지 않음)
    format: str           # '들어갈 수 있는 값·형식' 원문
    value_type: str       # _infer_type 결과
    enum: tuple = ()      # 허용값. 비어 있으면 목록 검사 없음
    enum_kind: str = ""   # 'scalar' | 'array'(JSON 배열의 원소에 적용) | ''
    regex: str = ""       # 형식 정규식
    required: bool = False
    max_items: int = 0    # JSON 배열 최대 원소 수. 0이면 제한 없음

    def check(self, value):
        """CSV에서 읽은 문자열 값 1개를 검사한다. 문제가 없으면 None, 있으면 설명."""
        if value is None or value == "":
            return "필수인데 비어 있음" if self.required else None
        if self.value_type == "json_array":
            return self._check_array(value)
        problem = _check_type(self.value_type, value)
        if problem:
            return problem
        if self.enum and self.enum_kind == "scalar" and value not in self.enum:
            return f"허용값 아님: {value!r} (허용: {', '.join(self.enum)})"
        if self.regex and not re.match(self.regex, value):
            return f"형식 불일치: {value!r} (정규식 {self.regex})"
        return None

    def _check_array(self, value):
        try:
            items = json.loads(value)
        except ValueError:
            return f"JSON으로 읽을 수 없음: {value[:40]!r}"
        if not isinstance(items, list):
            return "JSON 배열이 아님"
        if self.max_items and len(items) > self.max_items:
            return f"원소가 {len(items)}개 (최대 {self.max_items}개)"
        if self.enum and self.enum_kind == "array":
            bad = [x for x in items if x not in self.enum]
            if bad:
                return f"허용값 아닌 원소: {bad}"
            if len(set(items)) != len(items):
                return f"중복 원소: {items}"
        return None


def _check_type(value_type, value):
    """값 종류별 형식 검사. json_array는 FieldSpec._check_array가 맡는다."""
    try:
        if value_type == "json_object":
            if not isinstance(json.loads(value), dict):
                return "JSON 객체가 아님"
        elif value_type == "timestamp":
            if datetime.fromisoformat(value).tzinfo is None:
                return f"시간대 없는 타임스탬프: {value!r}"
        elif value_type == "date":
            date.fromisoformat(value)
        elif value_type == "semver":
            if not _RE_SEMVER.match(value):
                return f"MAJOR.MINOR.PATCH 형식 아님: {value!r}"
        elif value_type == "bool":
            if value not in ("true", "false"):
                return f"boolean 아님: {value!r}"
        elif value_type == "int":
            int(value)
        elif value_type == "number":
            float(value)
    except ValueError:
        return f"{value_type} 형식 아님: {value[:40]!r}"
    return None


class Codebook:
    """테이블별 필드 명세 묶음."""

    def __init__(self, tables, applied_overlays):
        self._tables = tables                        # {표 이름: [FieldSpec, ...]} 코드북 순서
        self.applied_overlays = applied_overlays     # [{id, status, basis}, ...] 기록용

    def fields(self, table):
        return self._tables[table]

    def columns(self, table):
        """CSV 열 이름. 코드북 시트의 행 순서 그대로."""
        return [f.name for f in self._tables[table]]

    def field(self, table, name):
        return next(f for f in self._tables[table] if f.name == name)

    def check_row(self, table, row, stages=None, skip=()):
        """행 1개를 검사해 (필드명, 문제) 목록을 돌려준다.

        stages  주어지면 그 '생성 단계' 필드에만 필수 검사를 한다. 러너는 자기가 채우는
                단계만 넘겨, 판정·집계 단계 필드가 비어 있다고 오류를 내지 않게 한다.
        skip    검사하지 않을 필드 이름. 호출자가 다른 기준으로 직접 검사하는 필드.
        """
        problems = []
        for spec in self._tables[table]:
            if spec.name in skip:
                continue
            value = row.get(spec.name, "")
            if stages is not None and spec.stage not in stages and value == "":
                continue
            problem = spec.check(value)
            if problem:
                problems.append((spec.name, problem))
        return problems


# ── 로드 ────────────────────────────────────────────────────────────────
def _field_from_json(table, raw):
    """추출본의 필드 dict -> FieldSpec."""
    fmt = raw["format"]
    enum = tuple(str(v) for v in (raw["enum"] or ()))
    return FieldSpec(
        table=table, name=raw["name"], stage=raw["stage"], ai_delivery=raw["ai_delivery"],
        format=fmt,
        # 허용값이 정수 목록이면(예: rollout_no 1, 2, 3) 정수로 본다.
        value_type="int" if raw["enum"] and isinstance(raw["enum"][0], int) else _infer_type(fmt),
        enum=enum, enum_kind=raw["enum_kind"] or "", regex=raw["regex"] or "",
        # 단계 이름이 '…필수'로 끝나면 필수. 단, 형식 원문이 공란을 허용하면 필수가 아니다
        # (예: primary_risk "… 또는 조건부 공란", companion_context "… 아니면 공란").
        required=raw["stage"].endswith("필수") and "공란" not in fmt,
    )


def _apply_change(spec, change, taxonomy):
    """overlay의 apply 항목 1개를 FieldSpec에 반영한다."""
    updates = {}
    if "enum" in change:
        updates["enum"] = tuple(str(v) for v in change["enum"])
        updates["enum_kind"] = spec.enum_kind or "scalar"
    if "enum_add" in change:
        updates["enum"] = spec.enum + tuple(str(v) for v in change["enum_add"])
    if "enum_from" in change:
        source = {"taxonomy.major": taxonomy.major_codes, "taxonomy.sub": taxonomy.sub_codes}
        updates["enum"] = tuple(source[change["enum_from"]])
    for key in ("enum_kind", "required", "max_items"):
        if key in change:
            updates[key] = change[key]
    return replace(spec, **updates)


def load_codebook(taxonomy, codebook_json=paths.CODEBOOK_JSON, overlay_yaml=paths.OVERLAY_YAML):
    """코드북 추출본을 읽고 overlay를 덮어쓴 Codebook을 만든다.

    overlay 파일이 없으면(v0.3 추출 후 삭제한 상태) 추출본만 쓴다.
    """
    with open(codebook_json, encoding="utf-8") as f:
        raw = json.load(f)
    tables = {name: [_field_from_json(name, fld) for fld in table["fields"]]
              for name, table in raw["tables"].items()}

    applied = []
    if overlay_yaml.exists():
        with open(overlay_yaml, encoding="utf-8") as f:
            overlay = yaml.safe_load(f)
        for entry in overlay["changes"]:
            for change in entry["apply"]:
                specs = tables[change["table"]]
                index = next(i for i, s in enumerate(specs) if s.name == change["field"])
                specs[index] = _apply_change(specs[index], change, taxonomy)
            applied.append({"id": entry["id"], "status": entry["status"], "basis": entry["basis"]})
    return Codebook(tables, applied)
