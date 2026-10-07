"""코드북 명세 로드와 값 검사.

명세의 출처는 둘뿐이다.
  spec/codebook_data.py              코드북 xlsx 추출본(자동 생성 모듈, tools/extract_codebook.py)
  schema/overlay_v0.3_confirmed.yaml v0.3 xlsx가 오기 전까지의 확정·가정 변경(사람이 쓰는 yaml)

필드 이름·순서·허용값·정규식을 코드에 적지 않는다. 코드북이 바뀌면 추출만 다시 한다.

overlay 항목은 두 종류의 변경을 담을 수 있다.
  apply      있는 필드의 허용값·필수성·형식 원문 등을 덮어쓴다
  add_field  필드를 새로 넣는다. 코드북 담당 회신으로 확정된 항목(status confirmed)을
             authorized_by로 가리켜야만 허용된다 — 회신 없이는 열을 늘릴 수 없다(프로젝트 규칙).
"""
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime

from . import paths
from .errors import SetupError, load_yaml
from .spec import codebook_data

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
# 07 비율 필드('0.0-1.0 소수', '0 이상의 소수'). 낱말 '소수'만으로는 수로 보지 않는다 — '성소수자'·'소수 의견' 같은 설명이
# 형식 원문에 들어오면 텍스트 필드가 number로 바뀌어 모든 값을 거부하게 되므로(R07), 수 범위 표기와 함께 있을 때만 수다.
_RE_DECIMAL = re.compile(r"(?:-?\d+\.\d+-\s*-?\d+\.\d+|\d+(?:\.\d+)?\s*이상의)\s*소수")
_RE_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# int()/float()는 ' 7'·'+7'·'1_000'·'nan'·'inf'·전각 숫자도 받아들여 뒤 단계(isdigit 비교, 키 조립)와 어긋난다. ASCII 표기만 허용
_RE_INT = re.compile(r"^-?[0-9]+$")
_RE_NUMBER = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
_RE_RANGE = re.compile(r"(-?\d+\.\d+)-(-?\d+\.\d+)")      # '0.0-1.0 소수', '-1.0-1.0 소수'
_RE_MINIMUM = re.compile(r"(\d+) 이상")                    # '0 이상의 정수', '0 이상의 소수'
_RE_FIXED = re.compile(r"(-?[0-9]+(?:\.[0-9]+)?)\s*(?:으|)로 고정")      # '숫자 0.0으로 고정', '양의 정수 1024로 고정'


def fixed_value(fmt):
    """형식 원문이 '…로 고정'이면 그 값(문자열), 아니면 None. 코드북이 고정값을 enum 없이 적은 필드용."""
    match = _RE_FIXED.search(fmt)
    return match.group(1) if match else None


def value_range(fmt):
    """형식 원문에서 수의 범위를 읽는다. 반환: (하한, 상한) — 상한이 없으면 (하한, None), 범위가 없으면 None."""
    match = _RE_RANGE.search(fmt)
    if match:
        return float(match.group(1)), float(match.group(2))
    match = _RE_MINIMUM.search(fmt)
    return (float(match.group(1)), None) if match else None


def _infer_type(fmt):
    """형식 원문 → 값 종류. 키워드는 위에서부터 먼저 맞는 것('JSON 객체'·'정수'가 '소수'보다 먼저), 그다음 수 범위가 붙은 '소수'."""
    for keyword, value_type in _TYPE_KEYWORDS:
        if keyword in fmt:
            return value_type
    if _RE_DECIMAL.search(fmt):
        return "number"
    return "text"


class OverlayError(SetupError):
    """overlay 항목이 규칙에 어긋날 때(허가 없는 열 추가, 없는 필드·위치 등)."""


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
    none_token: str = ""  # '해당 없음'을 뜻하는 기록값(예: critical_failure_code의 NONE). 없으면 빈 문자열
    added_by: str = ""    # overlay add_field로 들어온 필드면 그 항목 ID. 코드북 원본 필드는 빈 문자열

    @property
    def is_json(self):
        """셀이 JSON 본문(배열·객체)인 필드. 값으로 풀어 쓰는 쪽(판정 입력·납품 JSONL)이 함께 쓴다."""
        return self.value_type in ("json_array", "json_object")

    @property
    def bounds(self):
        """형식 원문의 수 범위 (하한, 상한). 저장하지 않고 format에서 매번 읽어 overlay가 format을 바꿔도 어긋나지 않는다."""
        return value_range(self.format)

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
        if self.value_type in ("int", "number") and self.bounds is not None:
            low, high = self.bounds
            number = float(value)
            if number < low or (high is not None and number > high):
                return f"범위 밖: {value} (형식: {self.format})"
        return None

    def _check_array(self, value):
        items, problem = parse_json_array(value)
        if problem:
            return problem
        if self.max_items and len(items) > self.max_items:
            return f"원소가 {len(items)}개 (최대 {self.max_items}개)"
        if self.enum and self.enum_kind == "array":
            bad = [x for x in items if x not in self.enum]
            if bad:
                return f"허용값 아닌 원소: {bad}"
            if len(set(items)) != len(items):
                return f"중복 원소: {items}"
        return None


def parse_json_array(value):
    """JSON 배열 셀 -> (원소 목록, 문제 설명). 배열이 아니거나 읽을 수 없으면 (None, 설명). 필드 검사와 이전 체계 행 검사가 함께 쓴다."""
    try:
        items = json.loads(value)
    except ValueError:
        return None, f"JSON으로 읽을 수 없음: {value[:40]!r}"
    if not isinstance(items, list):
        return None, "JSON 배열이 아님"
    return items, None


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
            if not _RE_INT.match(value):
                return f"int 형식 아님: {value[:40]!r}"
        elif value_type == "number":
            if not _RE_NUMBER.match(value):
                return f"number 형식 아님: {value[:40]!r}"
    except ValueError:
        return f"{value_type} 형식 아님: {value[:40]!r}"
    return None


class Codebook:
    """테이블별 필드 명세 묶음."""

    def __init__(self, tables, applied_overlays):
        self._tables = tables                        # {표 이름: [FieldSpec, ...]} 코드북 순서
        self._index = {table: {spec.name: spec for spec in specs} for table, specs in tables.items()}   # 이름 조회용
        self.applied_overlays = applied_overlays     # [{id, status, basis, note}, ...] 매니페스트 기록용

    def fields(self, table):
        return self._tables[table]

    def columns(self, table):
        """CSV 열 이름. 코드북 시트의 행 순서 그대로(overlay가 넣은 필드는 지정한 위치에)."""
        return [f.name for f in self._tables[table]]

    def added_columns(self, table):
        """overlay add_field로 들어온 열 이름. 옛 머리글(코드북 원본)로 쓰인 파일을 읽을 때 쓴다."""
        return [f.name for f in self._tables[table] if f.added_by]

    def field(self, table, name):
        """필드 명세. 없는 표·필드면 메시지 있는 KeyError(규칙 파일·코드가 옛 이름을 쓸 때 traceback 대신 설정 오류로 잡힌다)."""
        try:
            return self._index[table][name]
        except KeyError:
            raise KeyError(f"코드북 {table}에 없는 필드 {name!r}") from None

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
def _field_from_spec(table, raw):
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


# overlay 항목의 키 가운데 FieldSpec 필드에 그대로 옮기는 것(값 변환 없음)
_PASSTHROUGH_KEYS = ("enum_kind", "required", "max_items", "format", "none_token", "regex")


def _overlay_updates(spec, change, taxonomy):
    """apply·add_field 항목 1개 -> FieldSpec에 덮어쓸 필드 dict.

    다루는 키(적용 순서대로, 뒤가 앞을 덮는다): enum → enum_add → enum_from → enum_kind·required·max_items·format·none_token·regex.
    format이 바뀌고 허용값이 없으면 value_type을 새 원문에서 다시 추론한다. 허용값이 생겼는데 종류(scalar·array)가
    비어 있으면 scalar로 본다(check()는 enum_kind가 비면 허용값을 보지 않는다).
    """
    updates = {}
    if "enum" in change:
        updates["enum"] = tuple(str(v) for v in change["enum"])
    if "enum_add" in change:
        updates["enum"] = spec.enum + tuple(str(v) for v in change["enum_add"])
    if "enum_from" in change:
        source = dict(zip(_ENUM_SOURCES, (taxonomy.major_codes, taxonomy.sub_codes)))
        updates["enum"] = tuple(source[change["enum_from"]])
    for key in _PASSTHROUGH_KEYS:
        if key in change:
            updates[key] = change[key]
    if "format" in change and "enum" not in change and not spec.enum:
        updates["value_type"] = _infer_type(change["format"])        # 형식 원문이 바뀌면 값 종류도 다시 읽는다
    if updates.get("enum") and not (updates.get("enum_kind") or spec.enum_kind):
        updates["enum_kind"] = "scalar"
    return updates


def _apply_change(spec, change, taxonomy):
    """overlay의 apply·add_field 항목 1개를 FieldSpec에 반영한다."""
    return replace(spec, **_overlay_updates(spec, change, taxonomy))


def _find(specs, table, name):
    index = next((i for i, s in enumerate(specs) if s.name == name), None)
    if index is None:
        raise OverlayError(f"overlay: {table}에 없는 필드 {name!r}")
    return index


def _add_field(tables, entry, change, confirmed_ids, taxonomy):
    """overlay add_field 1개: 새 FieldSpec을 after 필드 바로 뒤에 넣는다.

    가드: authorized_by가 가리키는 confirmed 항목(코드북 담당 회신)이 있어야 한다. 프로젝트 규칙
    "열 추가·삭제 금지"의 유일한 예외가 코드북 담당 회신이므로, 회신 없이는 열을 늘릴 수 없다.
    """
    authorized_by = change.get("authorized_by")
    if authorized_by not in confirmed_ids:
        raise OverlayError(f"overlay {entry['id']}: add_field는 코드북 담당 회신으로 확정된(confirmed) 항목을 "
                           f"authorized_by로 가리켜야 합니다 (현재 {authorized_by!r}). 회신 없이는 열을 늘릴 수 없습니다.")
    specs = tables[change["table"]]
    if any(s.name == change["field"] for s in specs):
        raise OverlayError(f"overlay {entry['id']}: {change['table']}에 이미 있는 필드 {change['field']!r}")
    fmt = change["format"]
    spec = FieldSpec(table=change["table"], name=change["field"], stage=change["stage"],
                     ai_delivery=change.get("ai_delivery", "×"), format=fmt, value_type=_infer_type(fmt),
                     required=change.get("required", False), added_by=entry["id"])
    spec = _apply_change(spec, change, taxonomy)
    specs.insert(_find(specs, change["table"], change["after"]) + 1, spec)


_ENUM_SOURCES = ("taxonomy.major", "taxonomy.sub")     # enum_from이 가리킬 수 있는 분류체계 코드 목록
_TOP_KEYS = {"base", "changes"}                        # 최상위 키. base는 사람이 읽는 출처 표기(코드는 읽지 않는다)
_ENTRY_KEYS = {"id", "status", "date", "basis", "note", "apply", "add_field"}
_APPLY_KEYS = {"table", "field", "enum", "enum_add", "enum_from", "enum_kind", "required", "max_items", "format",
               "none_token", "regex"}
_ADD_KEYS = {"table", "field", "after", "stage", "ai_delivery", "format", "enum", "enum_from", "enum_kind", "required",
             "regex", "authorized_by"}
_STATUSES = ("confirmed", "provisional")


def _check_overlay_schema(overlay, tables):
    """overlay 파일의 구조·키·상태값·ID 중복·표 이름·enum_from을 검사한다. 오타가 조용히 무시되거나 traceback으로 끝나지 않게."""
    if not isinstance(overlay, dict) or not isinstance(overlay.get("changes"), list):
        raise OverlayError("overlay 파일은 'changes' 목록을 가진 매핑이어야 합니다(빈 파일·changes 누락 불가)")
    unknown_top = sorted(set(overlay) - _TOP_KEYS)
    if unknown_top:                                   # 최상위 키도 검사한다(R08): 모르는 키가 조용히 무시되지 않게
        raise OverlayError(f"overlay 파일 최상위에 알 수 없는 키 {unknown_top} (가능: {sorted(_TOP_KEYS)})")
    seen = set()
    for entry in overlay["changes"]:
        if not isinstance(entry, dict):
            raise OverlayError(f"overlay 항목이 매핑이 아님: {entry!r}")
        unknown = set(entry) - _ENTRY_KEYS
        if unknown or "id" not in entry or "status" not in entry or "basis" not in entry:
            raise OverlayError(f"overlay 항목 {entry.get('id')!r}: 알 수 없는 키 {sorted(unknown)} 또는 id·status·basis 누락")
        if entry["status"] not in _STATUSES:
            raise OverlayError(f"overlay {entry['id']}: status는 {_STATUSES} 중 하나여야 함 (현재 {entry['status']!r})")
        if entry["id"] in seen:
            raise OverlayError(f"overlay ID 중복: {entry['id']}")
        seen.add(entry["id"])
        for kind in ("apply", "add_field"):
            if entry.get(kind) is not None and not (isinstance(entry[kind], list) and all(isinstance(c, dict) for c in entry[kind])):
                raise OverlayError(f"overlay {entry['id']} {kind}: 매핑의 목록이어야 함")
        for change in entry.get("apply") or []:
            if set(change) - _APPLY_KEYS or not {"table", "field"} <= set(change):
                raise OverlayError(f"overlay {entry['id']} apply: 키 확인 {sorted(change)}")
        for change in entry.get("add_field") or []:
            missing = {"table", "field", "after", "stage", "format"} - set(change)
            if set(change) - _ADD_KEYS or missing:
                raise OverlayError(f"overlay {entry['id']} add_field: 알 수 없는 키 {sorted(set(change) - _ADD_KEYS)} 또는 누락 {sorted(missing)}")
        for change in [*(entry.get("apply") or []), *(entry.get("add_field") or [])]:
            if change["table"] not in tables:
                raise OverlayError(f"overlay {entry['id']}: 코드북에 없는 표 {change['table']!r}")
            if "enum_from" in change and change["enum_from"] not in _ENUM_SOURCES:
                raise OverlayError(f"overlay {entry['id']}: enum_from은 {_ENUM_SOURCES} 중 하나여야 함 (현재 {change['enum_from']!r})")


def load_codebook(taxonomy, raw=codebook_data.CODEBOOK, overlay_yaml=paths.OVERLAY_YAML):
    """코드북 추출본(생성 모듈의 dict)에 overlay를 덮어쓴 Codebook을 만든다.

    overlay 파일이 없으면(v0.3 추출 후 삭제한 상태) 추출본만 쓴다.
    적용한 overlay 기록(applied_overlays)에는 id·status·basis와 note가 남아 매니페스트에서
    '잠정' 표시를 읽을 수 있다. raw 인자는 시험용 치환 자리다(읽기만 하므로 복사하지 않는다).
    """
    tables = {name: [_field_from_spec(name, fld) for fld in table["fields"]]
              for name, table in raw["tables"].items()}

    applied = []
    if overlay_yaml.exists():
        overlay = load_yaml(overlay_yaml, OverlayError)
        _check_overlay_schema(overlay, tables)
        confirmed_ids = {e["id"] for e in overlay["changes"] if e["status"] == "confirmed"}
        for entry in overlay["changes"]:
            for change in entry.get("apply") or []:
                specs = tables[change["table"]]
                index = _find(specs, change["table"], change["field"])
                specs[index] = _apply_change(specs[index], change, taxonomy)
            for change in entry.get("add_field") or []:
                _add_field(tables, entry, change, confirmed_ids, taxonomy)
            applied.append({"id": entry["id"], "status": entry["status"], "basis": entry["basis"],
                            "note": entry.get("note", "")})
    return Codebook(tables, applied)
