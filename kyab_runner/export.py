"""납품 형식(JSONL) 내보내기: 코드북 7 CSV → items.jsonl · responses/ · judgments/ · results/ · manifest · sources · schema.

작업용 기록은 코드북 7 CSV다. 여기서는 그것을 **기계적으로 변환**만 한다(납품형식_JSONL스키마_v0.1.md).
  * 필드 이름과 값은 코드북 그대로. 새로 두는 것은 묶음 이름(turns·evaluation·metadata·tags·review·provenance·
    model·settings·outcome·scores·crri)과 파일 구성뿐이며 둘 다 연구실 A 제안(잠정)이다.
  * 값 형식은 코드북 FieldSpec의 종류를 따른다(to_value): JSON 배열·객체는 실제 값, 정수·숫자·boolean은 실제 형식이며
    이런 필드의 빈칸은 null. 문자열 종류(날짜·타임스탬프·SemVer 포함)의 빈칸은 "" 그대로.
  * 왕복 검증(roundtrip_diffs): JSONL을 다시 CSV 셀로 풀어 원본과 셀 단위로 대조한다. 문자열이 같으면 통과, 다르면
    JSON 필드는 파싱값·숫자 필드는 수치로 비교한다(다른 팀 CSV의 표기 차이 흡수). 그 밖은 차이로 보고한다.
  * JSON Schema는 코드북(+overlay)에서 만든다(field_schema). 손으로 쓰지 않는다.
  * 안전장치: 모의 판정 행은 기본 거부, 비밀값 패턴 검사, 실행 조건 섞임 거부, 입력·06 검증.

배치표(ITEM_TOP·ITEM_EVALUATION_LAYOUT·ITEM_METADATA·ITEM_REVIEW_LAYOUT·ITEM_PROVENANCE, RUN_*)가 01·04의 열을 빠짐없이·
겹치지 않게 덮지 못하면 내보내기가 거부된다(_check_layout). 레코드 만들기·스키마·왕복 검증이 같은 배치표를 읽는다.
원천 데이터셋 목록(sources.json)은 sources.py가 맡는다.
"""
import argparse
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from . import codebook as codebook_module
from . import csv_io, fileio, judge_io, paths, provenance
from .context import READ_ERRORS, SETUP_ERRORS, load_environment, open_views, report_read_error, setup_error_message
from .exitcodes import EXIT_INVALID, EXIT_OK
from .layout import (DELIVERY_ITEMS_FILE, DELIVERY_JUDGMENTS_DIR, DELIVERY_MANIFEST_FILE, DELIVERY_RESPONSES_DIR,
                     DELIVERY_RESULTS_DIR, DELIVERY_SCHEMA_DIR, DELIVERY_SOURCES_FILE, RESULTS_FOLDER_FILES)
from .records import RUN_PARAM_FIELDS
from .sources import DEFAULT_DATA_DIR, SourcesRegistryError, load_sources_registry, sources_skeleton   # noqa: F401 (호출자·테스트 호환)
from .spec import codebook_data
from .validate import RISK_CODE_FIELDS, item_key
from .vocab import TAG_CURRENT

FORMAT_VERSION = "0.1"           # 납품형식_JSONL스키마 문서 판본
ITEMS_FILE, MANIFEST_FILE, SOURCES_FILE = DELIVERY_ITEMS_FILE, DELIVERY_MANIFEST_FILE, DELIVERY_SOURCES_FILE
SCHEMA_KINDS = ("items", "responses", "judgments")           # schema/<kind>.schema.json, 검증 대상 JSONL의 종류

# ── items.jsonl 배치표: 01·02·03의 모든 열이 정확히 한 번씩 들어간다. 묶음 안 순서가 곧 출력 순서다 ─────────
ITEM_TOP = ("item_id", "item_version", "dataset_version", "case_type", "conversation_mode", "planned_round_count", "protocol_id")
ITEM_METADATA = ("scenario_type", "persona_text", "target_age_group", "user_gender", "gender_variant_group_id", "urgency")
ITEM_REVIEW = ("item_review_status", "lifecycle_status")
ITEM_PROVENANCE = ("source_benchmark", "source_item_id", "source_category", "source_mechanism", "source_language",
                   "original_text", "localization_type", "source_license", "parent_item_id")
TAG_KEYS = ("item_id", "item_version")                     # tags 묶음에서 빼는 02 열(상위에 이미 있음)
TAG_TO_EVALUATION = ("expected_response_tags",)            # 02에서 evaluation 묶음으로 옮기는 열
TAG_TO_REVIEW = ("risk_review_status", "review_status")    # 02에서 review 묶음으로 옮기는 열
TURN_KEYS = ("item_id", "item_version")                    # turns 원소에서 빼는 03 열
# 01·02 열이 섞이는 묶음은 (표, 열들) 순서로 적는다. 레코드·스키마·왕복이 같은 순서를 쓴다
ITEM_EVALUATION_LAYOUT = (("01_items", ("rubric_id",)), ("02_item_tags", TAG_TO_EVALUATION),
                          ("01_items", ("prohibited_response_json", "risk_cues_json")))
ITEM_REVIEW_LAYOUT = (("01_items", ITEM_REVIEW), ("02_item_tags", TAG_TO_REVIEW))
ITEM_EVALUATION = tuple(n for t, names in ITEM_EVALUATION_LAYOUT if t == "01_items" for n in names)

# ── responses: 04 열의 배치 ──
RUN_TOP = ("run_id", "run_batch_id", "item_id", "item_version", "dataset_version", "rollout_no")
RUN_MODEL = ("model_id", "provider", "model_version", "model_snapshot_date", "api_version")
RUN_SETTINGS = ("protocol_id", "system_prompt_hash", "temperature", "top_p", "max_output_tokens", "safety_profile",
                "tool_profile", "execution_library_version")
RUN_OUTCOME = ("run_status", "stop_reason", "actual_turn_count", "first_fail_turn", "first_cfc_turn", "started_at", "completed_at")
RUN_TO_MANIFEST = ("system_prompt_text",)                   # 원문은 manifest.system_prompts에 해시별로 한 번
RESPONSE_KEYS = ("run_id",)                                 # turns 원소에서 빼는 05 열
RESPONSE_TURN_HEAD = ("turn_id",)                           # turns 원소 맨 앞. 그 뒤에 turn_index(03에서)가 들어간다
RESPONSE_RENAMES = {"request_messages_json": "messages", "raw_response_json": "raw_response"}   # 묶음 안 이름(명세 4-2)
RESPONSE_RENAMES_BACK = {v: k for k, v in RESPONSE_RENAMES.items()}

# ── judgments ──
JUDGMENT_LINK = ("item_id", "item_version", "run_id", "rollout_no")     # 04에서 찾아 덧붙이는 연결 키
JUDGMENT_HEAD = ("judgment_id", "evaluation_scope", "response_id")

# manifest.links: 파일끼리 어떤 키로 이어지는지. 받는 쪽이 별도 문서 없이 조인할 수 있게 적는다(실제 묶음 구조 기준).
MANIFEST_LINKS = {
    "note": "파일 사이 조인 키. 이름과 값은 코드북 7 CSV의 키 열 그대로다.",
    "items.jsonl": {
        "key": ["item_id", "item_version"],
        "turns[]": {"key": "turn_id", "order": "turn_index"},
        "metadata.tag_history[]": {"key": "tag_revision", "current": "tag_status == 'current' (metadata.tags·evaluation·review에 쓴 판본)"},
    },
    "responses/<model_id>.jsonl": {
        "key": "run_id",
        "→ items.jsonl": ["item_id", "item_version"],
        "turns[]": {"key": "response_id", "→ items.jsonl.turns[]": "turn_id", "order": "turn_index"},
        "settings.system_prompt_hash → manifest.system_prompts": "해시별 시스템 프롬프트 원문",
        "model_id": "파일 이름 = model_id (한 모델에 실행 조건은 한 가지)",
    },
    "judgments/<model_id>.jsonl": {
        "key": "judgment_id",
        "→ responses.turns[]": "response_id",
        "→ responses": ["run_id", "rollout_no"],
        "→ items.jsonl": ["item_id", "item_version"],
        "evaluation_scope": "turn = 그 응답 1턴 / conversation = 실행 전체(response_id는 그 실행의 마지막 성공 응답)",
        "tag_revision → items.jsonl.metadata.tag_history[]": "판정 때 쓴 태그 판본",
    },
    "results/07_results.csv": {
        "key": "result_id",
        "→ results/results_denominators.csv": "result_id (1:N — 지표·성분별 분모 행)",
        "→ results/results_notes.json": "rows[result_id]",
        "model_id": "→ responses/<model_id>.jsonl, judgments/<model_id>.jsonl",
        "source_run_batch_ids": "→ responses.run_batch_id",
    },
}

# 비밀값으로 보이는 문자열. 출력 전체(JSONL·manifest)를 훑는다. 키 패턴은 내보내기를 거부하고(종료 2), 이메일은 응답 본문에
# 정상적으로 나올 수 있어(상담기관 안내 등) 경고와 위치 목록만 낸다(내부 검토 조정 2026-10-05).
SECRET_PATTERNS = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    "google_key": re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    "hf_token": re.compile(r"\bhf_[A-Za-z0-9]{10,}"),
    "bearer": re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}"),
}
WARNING_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
}
MOCK_JUDGE_WARNING = "모의 판정기 결과가 포함돼 있어 본평가·보고에 쓸 수 없습니다."
# model_id가 파일 이름(responses/<model_id>.jsonl)이 되므로 경로 구분자 등을 막는다. 등록 모델 코드는 모두 여기에 맞는다(파일 체계 규칙, 설정 아님)
MODEL_FILE_STEM = re.compile(r"[A-Za-z0-9._-]+")


class ExportError(Exception):
    """내보내기를 멈춰야 할 때(배치표 누락, 안전장치, 왕복 실패)."""


# ── 값 변환 ─────────────────────────────────────────────────────────────
def to_value(spec, cell):
    """CSV 셀 -> JSON 값 (코드북 필드 종류 기준). 코드북 검사를 통과한 셀만 받는다(형식 오류는 여기서 잡지 않는다)."""
    if spec.is_json:
        return json.loads(cell) if cell != "" else None
    if spec.value_type == "int":
        return int(cell) if cell != "" else None
    if spec.value_type == "number":
        return float(cell) if cell != "" else None
    if spec.value_type == "bool":
        return {"true": True, "false": False}[cell] if cell != "" else None
    return cell                                            # text·date·timestamp·semver: 빈칸은 ""


def cell_from_value(spec, value):
    """JSON 값 -> CSV 셀 (to_value의 역). csv_io.to_cell(값만 받음)과 이름이 겹치지 않게 한다."""
    if value is None:
        return ""
    if spec.is_json:
        return json.dumps(value, ensure_ascii=False)
    if spec.value_type == "bool":
        return "true" if value else "false"
    if spec.value_type == "number":
        return csv_io.to_cell(float(value))
    return str(value)


to_cell = cell_from_value       # 옛 이름(테스트가 쓴다)


def cells_equal(spec, original, restored):
    """왕복 비교: 문자열이 같으면 통과. 다르면 JSON은 파싱값, 숫자는 수치로 비교한다."""
    if original == restored:
        return True
    try:
        if spec.is_json:
            return json.loads(original) == json.loads(restored)
        if spec.value_type in ("number", "int"):
            return float(original) == float(restored)
    except ValueError:
        return False
    return False


def typed(codebook, table, row, fields, rename=None):
    """행의 일부 필드를 JSON 값으로. rename으로 묶음 안 이름을 바꿀 수 있다(코드북 이름은 왕복 때 되돌린다)."""
    rename = rename or {}
    return {rename.get(name, name): to_value(codebook.field(table, name), row[name]) for name in fields}


def _typed_block(codebook, rows_by_table, layout):
    """(표, 열들) 배치표 순서대로 typed()를 이어 붙인 묶음(evaluation·review처럼 01·02가 섞이는 묶음용)."""
    block = {}
    for table, names in layout:
        block.update(typed(codebook, table, rows_by_table[table], names))
    return block


def _to_row(codebook, table, flat):
    """typed()의 역: 묶음을 푼 dict → 코드북 열 순서의 CSV 행(없는 키는 빈칸)."""
    return {name: cell_from_value(codebook.field(table, name), flat.get(name)) for name in codebook.columns(table)}


# ── 배치표에서 파생하는 열 목록 ─────────────────────────────────────────
def _tag_block_fields(codebook):
    """metadata.tags 묶음의 02 열: 키·옮긴 열을 뺀 나머지."""
    return [c for c in codebook.columns("02_item_tags") if c not in (*TAG_KEYS, *TAG_TO_EVALUATION, *TAG_TO_REVIEW)]


def _item_turn_fields(codebook):
    """items.turns[] 원소의 03 열: 키를 뺀 나머지."""
    return [c for c in codebook.columns("03_prompts") if c not in TURN_KEYS]


def _response_turn_fields(codebook):
    """responses.turns[] 원소의 05 열(turn_id·turn_index 뒤에 오는 것): 키와 머리 열을 뺀 나머지."""
    return [c for c in codebook.columns("05_responses") if c not in RESPONSE_KEYS and c not in RESPONSE_TURN_HEAD]


def _check_layout(codebook):
    """배치표와 코드북이 맞는지.

    01·04는 묶음들이 열을 빠짐없이·겹치지 않게 덮는지, 02·03·05는 묶음에서 빼거나 옮기는 열이 코드북에 있는지,
    06 머리 열·04 연결 키가 코드북에 있는지 본다.
    """
    plans = {
        "01_items": [*ITEM_TOP, *ITEM_EVALUATION, *ITEM_METADATA, *ITEM_REVIEW, *ITEM_PROVENANCE],
        "04_runs": [*RUN_TOP, *RUN_MODEL, *RUN_SETTINGS, *RUN_OUTCOME, *RUN_TO_MANIFEST],
    }
    for table, planned in plans.items():
        columns = codebook.columns(table)
        missing, extra = set(columns) - set(planned), set(planned) - set(columns)
        if missing or extra or len(planned) != len(set(planned)):
            raise ExportError(f"내보내기 배치표가 코드북 {table}과 다릅니다 — 빠진 열 {sorted(missing)}, 모르는 열 {sorted(extra)}")
    referenced = (("02_item_tags", (*TAG_KEYS, *TAG_TO_EVALUATION, *TAG_TO_REVIEW)), ("03_prompts", TURN_KEYS),
                  ("05_responses", (*RESPONSE_KEYS, *RESPONSE_TURN_HEAD, *RESPONSE_RENAMES)),
                  ("06_judgments", JUDGMENT_HEAD), ("04_runs", JUDGMENT_LINK))
    for table, names in referenced:
        unknown = set(names) - set(codebook.columns(table))
        if unknown:
            raise ExportError(f"내보내기 배치표: 코드북 {table}에 없는 열 {sorted(unknown)}")


# ── 레코드 만들기 ───────────────────────────────────────────────────────
def item_record(codebook, item, tags, turns):
    """items.jsonl 한 줄. tags는 그 문항 판본의 02 행 전체(tag_revision 순), turns는 03 행(turn_index 순)."""
    current = next(t for t in tags if t["tag_status"] == TAG_CURRENT)
    rows = {"01_items": item, "02_item_tags": current}
    record = typed(codebook, "01_items", item, ITEM_TOP)
    record["turns"] = [typed(codebook, "03_prompts", t, _item_turn_fields(codebook)) for t in turns]
    record["evaluation"] = _typed_block(codebook, rows, ITEM_EVALUATION_LAYOUT)
    record["metadata"] = {
        **typed(codebook, "01_items", item, ITEM_METADATA),
        "tags": typed(codebook, "02_item_tags", current, _tag_block_fields(codebook)),
        "review": _typed_block(codebook, rows, ITEM_REVIEW_LAYOUT),
        "tag_history": [typed(codebook, "02_item_tags", t, codebook.columns("02_item_tags")) for t in tags],
    }
    record["provenance"] = typed(codebook, "01_items", item, ITEM_PROVENANCE)
    return record


def response_record(codebook, run, responses, turn_index_of):
    """responses/<model_id>.jsonl 한 줄: 04 실행 1행 + 그 실행의 05 응답 행(턴 순서)."""
    record = typed(codebook, "04_runs", run, RUN_TOP)
    record["model"] = typed(codebook, "04_runs", run, RUN_MODEL)
    record["settings"] = typed(codebook, "04_runs", run, RUN_SETTINGS)
    record["outcome"] = typed(codebook, "04_runs", run, RUN_OUTCOME)
    record["turns"] = [{**typed(codebook, "05_responses", response, RESPONSE_TURN_HEAD),
                        "turn_index": turn_index_of(response),
                        **typed(codebook, "05_responses", response, _response_turn_fields(codebook), RESPONSE_RENAMES)}
                       for response in responses]
    return record


def judgment_record(codebook, rules, row, run):
    """judgments/<model_id>.jsonl 한 줄: 06 행 + 04 연결 키. d1~d6은 scores, CRRI 4축은 crri 묶음(06 열 순서의 첫 자리에)."""
    dims, axes = set(rules.dimensions.values()), set(rules.crri_axes)
    record = typed(codebook, "06_judgments", row, JUDGMENT_HEAD)
    record.update(typed(codebook, "04_runs", run, JUDGMENT_LINK))
    for name in codebook.columns("06_judgments"):
        if name in JUDGMENT_HEAD:
            continue
        value = to_value(codebook.field("06_judgments", name), row[name])
        if name in dims:
            record.setdefault("scores", {})[name] = value
        elif name in axes:
            record.setdefault("crri", {})[name] = value
        else:
            record[name] = value
    return record


# ── JSON Schema (코드북에서 생성) ────────────────────────────────────────
_STRING_FORMATS = {"timestamp": "date-time", "date": "date"}
SEMVER_PATTERN = codebook_module._RE_SEMVER.pattern          # 코드북 검사와 같은 정규식(문자열 동일)


def field_schema(spec, extra_enum=()):
    """FieldSpec 1개 -> JSON Schema 조각. 필수가 아닌 종류 있는 필드는 null을 허용한다.

    extra_enum  허용값에 덧붙일 값(분류체계 판본에 따라 달라지는 필드의 이전 코드 R1~R5 등). 판본별 정확한 검사는
                validate.py가 하고, 스키마는 두 체계를 모두 받는다.
    """
    enum = list(spec.enum) + [v for v in extra_enum if v not in spec.enum]
    if spec.value_type == "json_array":
        schema = {"type": "array"}
        if spec.enum and spec.enum_kind == "array":
            schema["items"] = {"enum": enum}
            schema["uniqueItems"] = True
        if spec.max_items:
            schema["maxItems"] = spec.max_items
    elif spec.value_type == "json_object":
        schema = {"type": "object"}
    elif spec.value_type == "int":
        schema = {"type": "integer"}
        if spec.enum:
            schema = {"enum": [int(v) for v in spec.enum]}
    elif spec.value_type == "number":
        schema = {"type": "number"}
    elif spec.value_type == "bool":
        schema = {"type": "boolean"}
    else:
        schema = {"type": "string"}
        if spec.enum and spec.enum_kind == "scalar":
            schema = {"enum": enum + ([] if spec.required else [""])}
        pattern = spec.regex or (SEMVER_PATTERN if spec.value_type == "semver" else "")
        if pattern and "enum" not in schema:
            schema["pattern"] = pattern
            if not spec.required:                           # 선택 필드의 빈칸은 정규식 대신 ""로 허용
                schema = {"anyOf": [schema, {"const": ""}]}
        if spec.value_type in _STRING_FORMATS:
            schema["format"] = _STRING_FORMATS[spec.value_type]
        if spec.required and "enum" not in schema:
            schema["minLength"] = 1
        if spec.none_token:
            schema["description"] = f"'{spec.none_token}' = 해당 없음"
    if spec.value_type not in ("text", "timestamp", "date", "semver") and not spec.required:
        schema = {"anyOf": [schema, {"type": "null"}]}
    schema.setdefault("description", spec.format)
    return schema


def _object(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or list(properties), "additionalProperties": False}


def _fields(codebook, table, names, rename=None, extra=None):
    rename, extra = rename or {}, extra or {}
    return {rename.get(n, n): field_schema(codebook.field(table, n), extra.get(n, ())) for n in names}


def _schema_block(codebook, layout):
    """(표, 열들) 배치표 순서대로 _fields()를 이어 붙인 묶음 스키마(_typed_block과 같은 순서)."""
    block = {}
    for table, names in layout:
        block.update(_fields(codebook, table, names))
    return block


def build_schemas(codebook, rules, taxonomy=None):
    """items·responses·judgments 레코드 스키마. 필드 스키마는 코드북, 묶음 구조는 이 모듈의 배치표.

    taxonomy를 주면 분류체계 판본에 따라 달라지는 02 필드(primary_risk 등)에 이전 코드(R1~R5)도 허용한다
    (tag_history에 옛 체계 행이 있으므로). 판본별 검사는 validate.py의 몫이다.
    """
    legacy = tuple(taxonomy.legacy_risk_codes) if taxonomy else ()
    extra = {name: legacy for name in RISK_CODE_FIELDS}
    items = _object({
        **_fields(codebook, "01_items", ITEM_TOP),
        "turns": {"type": "array", "minItems": 1, "items": _object(_fields(codebook, "03_prompts", _item_turn_fields(codebook)))},
        "evaluation": _object(_schema_block(codebook, ITEM_EVALUATION_LAYOUT)),
        "metadata": _object({
            **_fields(codebook, "01_items", ITEM_METADATA),
            "tags": _object(_fields(codebook, "02_item_tags", _tag_block_fields(codebook), extra=extra)),
            "review": _object(_schema_block(codebook, ITEM_REVIEW_LAYOUT)),
            "tag_history": {"type": "array", "minItems": 1,
                            "items": _object(_fields(codebook, "02_item_tags", codebook.columns("02_item_tags"), extra=extra))},
        }),
        "provenance": _object(_fields(codebook, "01_items", ITEM_PROVENANCE)),
    })
    responses = _object({
        **_fields(codebook, "04_runs", RUN_TOP),
        "model": _object(_fields(codebook, "04_runs", RUN_MODEL)),
        "settings": _object(_fields(codebook, "04_runs", RUN_SETTINGS)),
        "outcome": _object(_fields(codebook, "04_runs", RUN_OUTCOME)),
        "turns": {"type": "array", "items": _object({
            **_fields(codebook, "05_responses", RESPONSE_TURN_HEAD),
            "turn_index": {"type": "integer", "minimum": 1, "description": "03_prompts turn_index"},
            **_fields(codebook, "05_responses", _response_turn_fields(codebook), RESPONSE_RENAMES)})},
    })
    dims, axes = list(rules.dimensions.values()), list(rules.crri_axes)
    rest = [c for c in codebook.columns("06_judgments") if c not in JUDGMENT_HEAD and c not in dims and c not in axes]
    judgments = _object({
        **_fields(codebook, "06_judgments", JUDGMENT_HEAD),
        **_fields(codebook, "04_runs", JUDGMENT_LINK),
        **_fields(codebook, "06_judgments", rest),
        "scores": _object(_fields(codebook, "06_judgments", dims)),
        "crri": _object(_fields(codebook, "06_judgments", axes)),
    })
    header = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "$comment": f"코드북 v0.2 + overlay에서 생성(kyab_runner.export, 납품형식 {FORMAT_VERSION}). 손으로 고치지 않는다."}
    return {"items": {**header, "title": "items.jsonl record", **items},
            "responses": {**header, "title": "responses/<model_id>.jsonl record", **responses},
            "judgments": {**header, "title": "judgments/<model_id>.jsonl record", **judgments}}


# ── 내보내기 ────────────────────────────────────────────────────────────
def find_patterns(root, patterns):
    """출력 폴더의 모든 텍스트 파일에서 패턴을 찾는다. 반환: [(파일, 패턴 이름, 줄 번호)]"""
    hits = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in (".jsonl", ".json", ".csv")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for name, pattern in patterns.items():
                if pattern.search(line):
                    hits.append((str(path.relative_to(root)), name, line_no))
    return hits


def find_secrets(root):
    """거부 대상 비밀값(API 키·토큰·Bearer)."""
    return find_patterns(root, SECRET_PATTERNS)


def _preflight(env, views, out_dir, allow_mock_judge):
    """파일을 쓰기 전에 끝내는 안전장치: 배치표, 빈 출력 폴더, 06 검증, 모의 판정 거부, 실행 조건 섞임 거부.

    반환: ({run_batch_id: 06 행 목록}, 쓰인 모의 판정기 집합). 거부되면 아무것도 남지 않는다.
    """
    codebook, rules = env.codebook, env.rules
    _check_layout(codebook)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ExportError(f"출력 폴더가 비어 있지 않습니다: {out_dir}")
    judgments_by_batch, mock_judges = {}, set()
    for view in views:
        judgments = judge_io.load_judgments(codebook, view.batch.dir)
        issues = judge_io.validate_judgments(codebook, rules, view, judgments)
        errors = [i for i in issues if i.level == "error"]
        if errors:
            raise ExportError(f"{view.batch.run_batch_id}: 06 검증 오류 {len(errors)}건 (예: {errors[0]})")
        judgments_by_batch[view.batch.run_batch_id] = judgments
        mock_judges |= {r["judge_id"] for r in judgments} & rules.non_production_judges()
    if mock_judges and not allow_mock_judge:
        raise ExportError(f"본평가에 쓸 수 없는 판정기의 판정이 있습니다 {sorted(mock_judges)}. 경로 확인용이면 --allow-mock-judge")
    combos = defaultdict(Counter)
    for view in views:
        for run in view.runs.values():
            combos[run["model_id"]][tuple(run[f] for f in RUN_PARAM_FIELDS)] += 1
    mixed = {m: dict(c) for m, c in combos.items() if len(c) > 1}
    if mixed:
        raise ExportError(f"같은 모델에 실행 조건이 섞여 있습니다 {mixed} — 한도가 다른 배치는 따로 내보내세요")
    bad_models = sorted({m for m in combos if not MODEL_FILE_STEM.fullmatch(m)})
    if bad_models:
        raise ExportError(f"model_id가 파일 이름으로 쓸 수 없는 값입니다 {bad_models} (허용: 영문·숫자·._-)")
    _check_unique_ids(views, judgments_by_batch)
    return judgments_by_batch, mock_judges


def _check_unique_ids(views, judgments_by_batch):
    """납품 묶음 안에서 run_id·response_id·judgment_id가 겹치면 거부한다.

    ID는 출력 루트 하나 안에서만 고유하므로(ids.py) 다른 루트의 배치를 섞거나 같은 배치를 두 번 주면 겹친다. 쓰기 전에 잡는다.
    """
    seen = {"run_id": defaultdict(list), "response_id": defaultdict(list), "judgment_id": defaultdict(list)}
    for view in views:
        name = view.batch.run_batch_id
        for run_id in view.runs:
            seen["run_id"][run_id].append(name)
        for response_id in view.responses:
            seen["response_id"][response_id].append(name)
        for row in judgments_by_batch[name]:
            seen["judgment_id"][row["judgment_id"]].append(name)
    for kind, by_id in seen.items():
        duplicates = {i: names for i, names in by_id.items() if len(names) > 1}
        if duplicates:
            sample = "; ".join(f"{i} ({', '.join(names)})" for i, names in list(duplicates.items())[:5])
            raise ExportError(f"{kind}가 겹치는 배치를 함께 내보낼 수 없습니다 {len(duplicates)}건 (예: {sample}) — "
                              "같은 배치를 두 번 주었거나 다른 출력 루트의 배치가 섞였습니다")


def _item_records(codebook, index):
    """items.jsonl 레코드(01 순서). 태그는 tag_revision 오름차순으로 묶는다."""
    tags_by_item = defaultdict(list)
    for row in sorted(index.tables["02_item_tags"], key=lambda r: int(r["tag_revision"])):
        tags_by_item[item_key(row)].append(row)
    return [item_record(codebook, item, tags_by_item[item_key(item)], index.turns[item_key(item)])
            for item in index.tables["01_items"]]


def _run_records(codebook, rules, views, judgments_by_batch):
    """모델별 responses·judgments 레코드와 manifest에 적을 시스템 프롬프트·코드 판본.

    순회 순서(배치 → 실행 → 판정)를 지킨다: manifest.system_prompts는 삽입 순서가 남는 dict다.
    반환: (responses_by_model, judgments_by_model, {프롬프트 해시: 원문}, {execution_library_version})
    """
    responses_by_model, judgments_by_model, prompts, library_versions = defaultdict(list), defaultdict(list), {}, set()
    for view in views:
        for run_id, run in view.runs.items():
            prompts[run["system_prompt_hash"]] = run["system_prompt_text"]
            library_versions.add(run["execution_library_version"])
            responses_by_model[run["model_id"]].append(response_record(codebook, run, view.responses_by_run[run_id], view.turn_index))
        for row in judgments_by_batch[view.batch.run_batch_id]:
            run = view.run_of(view.responses[row["response_id"]])
            judgments_by_model[run["model_id"]].append(judgment_record(codebook, rules, row, run))
    return responses_by_model, judgments_by_model, prompts, library_versions


def _copy_results(results_dir, out_dir):
    """집계 결과 폴더의 세 파일을 그대로 복사한다. 반환: 복사한 파일 이름 목록."""
    copied = []
    (out_dir / DELIVERY_RESULTS_DIR).mkdir()
    for name in RESULTS_FOLDER_FILES:
        source = Path(results_dir) / name
        if source.exists():
            shutil.copy2(source, out_dir / DELIVERY_RESULTS_DIR / name)
            copied.append(name)
    return copied


def _write_schemas(out_dir, env):
    (out_dir / DELIVERY_SCHEMA_DIR).mkdir()
    for name, schema in build_schemas(env.codebook, env.rules, env.taxonomy).items():
        fileio.write_json(out_dir / DELIVERY_SCHEMA_DIR / f"{name}.schema.json", schema)


def _count_rows(path):
    """manifest files[].rows: JSONL·CSV는 줄 수(CSV는 머리글을 뺌), 그 밖은 None. 물리적 줄 수다(셀 안 줄바꿈도 센다)."""
    if path.suffix not in (".jsonl", ".csv"):
        return None
    with open(path, encoding="utf-8") as f:
        rows = sum(1 for _ in f)
    return rows - 1 if path.suffix == ".csv" and rows else rows


def _file_inventory(out_dir):
    """납품 폴더의 모든 파일: {상대 경로: {rows, sha256}} (경로순). manifest.json을 쓰기 전에 만든다."""
    return {str(path.relative_to(out_dir)): {"rows": _count_rows(path), "sha256": fileio.sha256_file(path)}
            for path in sorted(p for p in out_dir.rglob("*") if p.is_file())}


def _build_manifest(env, index, views, records, mock_judges, copied, warnings, notices, files):
    """manifest.json. 키 순서가 곧 파일 순서다."""
    responses_by_model, _, prompts, library_versions = records
    git = _git_state()                                      # 커밋 해시·수정 유무. dirty면 경고(거부는 않음)
    if git and git["dirty"]:
        notices.append(f"러너에 커밋되지 않은 수정 {git['uncommitted']}건 (.dirty) — 커밋 뒤 내보내야 코드를 되짚을 수 있다")
    return {
        "format": f"납품형식_JSONL스키마_v{FORMAT_VERSION} (연구실 A 제안, 잠정)",
        "generated_by": f"kyab_runner.export {_library_version()}",
        "runner_git": git or {"commit": "", "dirty": None, "uncommitted": None, "note": "runner/가 git 저장소가 아님"},
        "generated_at": views[0].batch.now() if views else None,
        "dataset_version": sorted({i["dataset_version"] for i in index.tables["01_items"]}),
        "codebook": {"source": codebook_source(), "overlays": env.codebook.applied_overlays},
        "aggregation_rule": {"id": env.rules.rule_id, "version": env.rules.rule_version, "sha256": env.rules.sha256},
        "execution_library_versions": sorted(library_versions),
        "system_prompts": prompts,
        "models": sorted(responses_by_model),
        "batches": [v.batch.run_batch_id for v in views],
        "mock_judge_used": sorted(mock_judges),
        "warning": MOCK_JUDGE_WARNING if mock_judges else "",
        "results_copied": copied,
        "pattern_warnings": {"count": len(warnings), "locations": [f"{f}:{ln} ({n})" for f, n, ln in warnings[:50]],
                             "note": "이메일 패턴은 응답 본문에 정상적으로 나올 수 있어 경고만 낸다. 확인 후 납품"},
        "warnings": notices,
        "links": MANIFEST_LINKS,
        "files": files,
    }


def export(env, index, views, out_dir, results_dir=None, allow_mock_judge=False):
    """CSV 기록 -> 납품 폴더. 반환: manifest dict. 안전장치에 걸리면 ExportError.

    순서가 중요하다: ① 안전장치(06 검증·모의 판정·조건 섞임·ID 중복·model_id)와 원천 등록부 읽기는 파일을 쓰기 전에
    (거부되면 폴더를 만들지 않는다) ② items → responses·judgments → results → schema → sources ③ 비밀값 검사(거부)
    ④ 파일 목록(manifest.json이 생기기 전에) ⑤ manifest → manifest에만 있는 값까지 비밀값 검사.
    쓰기 도중 어떤 예외가 나도 출력 폴더를 지운다. 스키마·왕복 검증 실패(main)는 원인을 보도록 남긴다.
    """
    codebook, rules = env.codebook, env.rules
    out_dir = Path(out_dir)
    judgments_by_batch, mock_judges = _preflight(env, views, out_dir, allow_mock_judge)
    items = _item_records(codebook, index)
    sources = sources_skeleton(index)                       # 등록부(config/sources.yaml) 형식 오류도 여기서, 쓰기 전에
    notices = [f"sources.json: {w}" for w in sources["warnings"]]

    out_dir.mkdir(parents=True, exist_ok=True)
    try:                                                    # 쓰기 도중 어떤 예외든 반쪽 납품 폴더를 남기지 않는다(비밀값 거부 포함)
        fileio.write_jsonl(out_dir / ITEMS_FILE, items)
        records = _run_records(codebook, rules, views, judgments_by_batch)
        responses_by_model, judgments_by_model = records[0], records[1]
        for model_id, model_records in responses_by_model.items():
            fileio.write_jsonl(out_dir / DELIVERY_RESPONSES_DIR / f"{model_id}.jsonl", model_records)
        for model_id, model_records in judgments_by_model.items():
            fileio.write_jsonl(out_dir / DELIVERY_JUDGMENTS_DIR / f"{model_id}.jsonl", model_records)
        copied = _copy_results(results_dir, out_dir) if results_dir is not None else []
        _write_schemas(out_dir, env)
        fileio.write_json(out_dir / SOURCES_FILE, sources)

        hits = find_secrets(out_dir)                        # 키 패턴은 거부, 이메일은 경고(manifest에 건수·위치)
        if hits:
            raise ExportError("비밀값으로 보이는 문자열이 있어 출력을 지웠습니다: " + "; ".join(f"{f} {n} {ln}행" for f, n, ln in hits[:5]))
        warnings = find_patterns(out_dir, WARNING_PATTERNS)
        manifest = _build_manifest(env, index, views, records, mock_judges, copied, warnings, notices, _file_inventory(out_dir))
        fileio.write_json(out_dir / MANIFEST_FILE, manifest)
        if find_secrets(out_dir):                          # 시스템 프롬프트 원문 등 manifest에만 있는 값도 훑는다
            raise ExportError("manifest에 비밀값으로 보이는 문자열이 있어 출력을 지웠습니다")
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    return manifest


def codebook_source():
    """납품 manifest에 적는 코드북 출처: 원본 xlsx 이름·판본·sha256과 생성 모듈 이름."""
    return (f"{codebook_data.SOURCE_FILE} (v{codebook_data.CODEBOOK_VERSION}, sha256 {codebook_data.SOURCE_SHA256}; "
            f"추출 모듈 kyab_runner/spec/codebook_data.py)")


def _library_version():
    """실행기와 같은 표기(runner-<판본>+<커밋 SHA>[.dirty])."""
    return provenance.library_version()


def _git_state():
    """러너 저장소 커밋·수정 상태. git 저장소가 아니면 None. (테스트가 이 이름을 치환한다)"""
    return provenance.git_state()


# ── 왕복 검증 ───────────────────────────────────────────────────────────
def _read_jsonl(path):
    return fileio.read_jsonl(path)


def _jsonl_files(out_dir, kind):
    """납품 폴더의 <kind>/*.jsonl (경로순). 없는 폴더면 빈 목록."""
    return sorted((Path(out_dir) / kind).glob("*.jsonl"))


def _diff_rows(codebook, table, originals, restored, key_of, where):
    """원본 행과 복원 행을 키로 맞춰 셀 단위로 비교한다. 반환: 차이 설명 목록."""
    diffs = []
    restored_by_key = {key_of(r): r for r in restored}
    if len(restored_by_key) != len(restored):
        diffs.append(f"{where}: 복원 행의 키가 겹침")
    original_keys = {key_of(r) for r in originals}
    for key in original_keys ^ set(restored_by_key):
        diffs.append(f"{where} {key}: 원본에만 있음" if key in original_keys else f"{where} {key}: 복원에만 있음")
    for row in originals:
        other = restored_by_key.get(key_of(row))
        if other is None:
            continue
        for name in codebook.columns(table):
            if not cells_equal(codebook.field(table, name), row[name], other.get(name, "")):
                diffs.append(f"{where} {key_of(row)} {name}: 원본 {row[name]!r} ≠ 복원 {other.get(name, '')!r}")
    return diffs


def _item_rows(codebook, records):
    """items.jsonl 레코드 -> (01 행, 02 행, 03 행) 복원."""
    items, tags, prompts = [], [], []
    for record in records:
        meta = record.get("metadata", {})
        flat = {**{k: record.get(k) for k in ITEM_TOP}, **{k: record.get("evaluation", {}).get(k) for k in ITEM_EVALUATION},
                **{k: meta.get(k) for k in ITEM_METADATA}, **{k: meta.get("review", {}).get(k) for k in ITEM_REVIEW},
                **{k: record.get("provenance", {}).get(k) for k in ITEM_PROVENANCE}}
        items.append(_to_row(codebook, "01_items", flat))
        for tag in meta.get("tag_history", []):
            tags.append(_to_row(codebook, "02_item_tags", tag))
        for turn in record.get("turns", []):
            prompts.append(_to_row(codebook, "03_prompts", {"item_id": record.get("item_id"), "item_version": record.get("item_version"), **turn}))
    return items, tags, prompts


def _run_rows(codebook, records, system_prompts):
    """responses/*.jsonl 레코드 -> (04 행, 05 행) 복원. 시스템 프롬프트 원문은 manifest에서 해시로 찾는다."""
    runs, responses = [], []
    for record in records:
        flat = {**{k: record.get(k) for k in RUN_TOP}, **record.get("model", {}), **record.get("settings", {}), **record.get("outcome", {}),
                "system_prompt_text": system_prompts.get(record.get("settings", {}).get("system_prompt_hash"), "")}
        runs.append(_to_row(codebook, "04_runs", flat))
        for turn in record["turns"]:
            full = {"run_id": record["run_id"], **{RESPONSE_RENAMES_BACK.get(k, k): v for k, v in turn.items() if k != "turn_index"}}
            responses.append(_to_row(codebook, "05_responses", full))
    return runs, responses


def _judgment_rows(codebook, records):
    """judgments/*.jsonl 레코드 -> 06 행 복원(연결 키·묶음은 풀어서)."""
    rows = []
    for record in records:
        flat = {k: v for k, v in record.items() if k not in ("scores", "crri", *JUDGMENT_LINK)}
        flat.update(record.get("scores", {}))
        flat.update(record.get("crri", {}))
        rows.append(_to_row(codebook, "06_judgments", flat))
    return rows


def roundtrip_diffs(env, index, views, out_dir):
    """JSONL을 다시 01~06 CSV 셀로 풀어 원본과 대조한다. 반환: 차이 설명 목록(비어 있으면 무손실).

    순서: 01·02·03 → 04·05 → 06 셀 차이 → 판정 연결 키(04·05와 일치) 차이.
    """
    codebook = env.codebook
    out_dir = Path(out_dir)
    manifest = fileio.read_json(out_dir / MANIFEST_FILE)
    diffs = []

    items, tags, prompts = _item_rows(codebook, _read_jsonl(out_dir / ITEMS_FILE))
    diffs += _diff_rows(codebook, "01_items", index.tables["01_items"], items, item_key, "01_items")
    diffs += _diff_rows(codebook, "02_item_tags", index.tables["02_item_tags"], tags,
                        lambda r: item_key(r) + (r["tag_revision"],), "02_item_tags")
    diffs += _diff_rows(codebook, "03_prompts", index.tables["03_prompts"], prompts, lambda r: r["turn_id"], "03_prompts")

    response_records = [r for p in _jsonl_files(out_dir, DELIVERY_RESPONSES_DIR) for r in _read_jsonl(p)]
    judgment_records = [r for p in _jsonl_files(out_dir, DELIVERY_JUDGMENTS_DIR) for r in _read_jsonl(p)]
    runs, responses = _run_rows(codebook, response_records, manifest["system_prompts"])
    judgments = _judgment_rows(codebook, judgment_records)
    all_runs = [r for v in views for r in v.runs.values()]
    all_responses = [r for v in views for r in v.responses.values()]
    all_judgments = [r for v in views for r in judge_io.load_judgments(codebook, v.batch.dir)]
    diffs += _diff_rows(codebook, "04_runs", all_runs, runs, lambda r: r["run_id"], "04_runs")
    diffs += _diff_rows(codebook, "05_responses", all_responses, responses, lambda r: r["response_id"], "05_responses")
    diffs += _diff_rows(codebook, "06_judgments", all_judgments, judgments, lambda r: r["judgment_id"], "06_judgments")

    # 판정 연결 키가 04·05와 맞는지
    run_by_id = {r["run_id"]: r for r in all_runs}
    response_run = {r["response_id"]: r["run_id"] for r in all_responses}
    for record in judgment_records:
        run = run_by_id.get(response_run.get(record["response_id"]))
        linked = tuple(cell_from_value(codebook.field("04_runs", k), record[k]) for k in JUDGMENT_LINK)
        if run is None or linked != tuple(run[k] for k in JUDGMENT_LINK):
            diffs.append(f"judgments {record['judgment_id']}: 연결 키가 04·05와 다름")
    return diffs


def schema_violations(out_dir):
    """모든 JSONL 줄을 schema/*.schema.json으로 검증한다. 반환: [(파일, 줄 번호, 메시지)]"""
    import jsonschema                                    # 검증 단계에서만 필요한 의존성
    out_dir = Path(out_dir)
    schemas = {name: fileio.read_json(out_dir / DELIVERY_SCHEMA_DIR / f"{name}.schema.json") for name in SCHEMA_KINDS}
    targets = [(out_dir / ITEMS_FILE, "items")]
    for kind in (DELIVERY_RESPONSES_DIR, DELIVERY_JUDGMENTS_DIR):
        targets += [(p, kind) for p in _jsonl_files(out_dir, kind)]
    problems = []
    for path, kind in targets:
        validator = jsonschema.Draft202012Validator(schemas[kind])
        for line_no, record in enumerate(_read_jsonl(path), start=1):
            for error in validator.iter_errors(record):
                problems.append((str(path.relative_to(out_dir)), line_no, f"{'/'.join(map(str, error.absolute_path))}: {error.message[:120]}"))
    return problems


# ── 명령행 ──────────────────────────────────────────────────────────────
def main(argv=None):
    """python -m kyab_runner.export <배치…> --out <폴더> [--input DIR] [--results RESULTS 폴더] [--allow-mock-judge]

    내보내기 → 스키마 검증 → 왕복 검증. 하나라도 어긋나면 종료 2(출력 폴더는 남겨 두어 원인을 볼 수 있게 한다).
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batches", nargs="*", type=Path, help="배치 폴더 (없으면 items.jsonl만)")
    parser.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR, help="01·02·03 폴더")
    parser.add_argument("--out", type=Path, required=True, help="납품 폴더(비어 있어야 함)")
    parser.add_argument("--results", type=Path, help="복사할 RESULTS-… 폴더(07·분모·notes)")
    parser.add_argument("--allow-mock-judge", action="store_true", help="모의 판정이 섞여 있어도 내보낸다(경로 확인용)")
    args = parser.parse_args(argv)
    try:
        env = load_environment()
    except SETUP_ERRORS as exc:
        print(setup_error_message(exc))
        return EXIT_INVALID
    for warning in env.rules.load_warnings:
        print(f"주의: {warning}")
    try:
        index, views, notices = open_views(env, args.input, args.batches)
    except READ_ERRORS as exc:                    # 배치 폴더·입력 문제는 명세 오류와 구분해 알린다
        report_read_error(exc)
        return EXIT_INVALID
    for notice in notices:
        print(f"주의: {notice}")
    try:
        manifest = export(env, index, views, args.out, args.results, args.allow_mock_judge)
    except (ExportError, SourcesRegistryError) as exc:
        print(f"내보내기 거부: {exc}")
        return EXIT_INVALID
    except csv_io.CsvFormatError as exc:          # 06_judgments.csv 머리글이 깨진 배치
        print(f"내보내기 거부: 배치 기록을 읽을 수 없습니다: {exc}")
        return EXIT_INVALID
    for notice in manifest["warnings"]:
        print(f"주의: {notice}")
    if args.results is not None:
        missing = [name for name in RESULTS_FOLDER_FILES if name not in manifest["results_copied"]]
        if missing:
            where = "폴더 없음" if not Path(args.results).is_dir() else "없는 파일"
            print(f"주의: --results 폴더 {args.results}에 {where} {missing} — 복사하지 않았습니다")
    if manifest["mock_judge_used"]:
        print(f"주의: {manifest['warning']}")
    if manifest["pattern_warnings"]["count"]:
        print(f"주의: 이메일 패턴 {manifest['pattern_warnings']['count']}건 — manifest.json pattern_warnings 확인")
    violations = schema_violations(args.out)
    for file, line_no, message in violations[:20]:
        print(f"[schema] {file}:{line_no} {message}")
    diffs = roundtrip_diffs(env, index, views, args.out)
    for diff in diffs[:20]:
        print(f"[roundtrip] {diff}")
    print(f"파일 {len(manifest['files'])}개 → {args.out} | 스키마 위반 {len(violations)} | 왕복 차이 {len(diffs)}")
    return EXIT_INVALID if violations or diffs else EXIT_OK


if __name__ == "__main__":
    import sys
    sys.exit(main())
