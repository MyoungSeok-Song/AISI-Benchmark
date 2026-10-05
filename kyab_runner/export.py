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

01·02·03의 모든 필드가 items.jsonl에 한 번씩 들어간다. 배치표(ITEM_LAYOUT)가 코드북 열을 모두 덮지 못하면 내보내기가 거부된다.
"""
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from . import csv_io, judge_io, paths
from .session import RUN_PARAM_FIELDS
from .validate import item_key

FORMAT_VERSION = "0.1"           # 납품형식_JSONL스키마 문서 판본
ITEMS_FILE = "items.jsonl"
MANIFEST_FILE = "manifest.json"
SOURCES_FILE = "sources.json"

# ── items.jsonl 배치표: 01·02·03의 모든 열이 정확히 한 번씩 들어간다 ─────────
ITEM_TOP = ("item_id", "item_version", "dataset_version", "case_type", "conversation_mode", "planned_round_count", "protocol_id")
ITEM_EVALUATION = ("rubric_id", "prohibited_response_json", "risk_cues_json")          # + 02 expected_response_tags
ITEM_METADATA = ("scenario_type", "persona_text", "target_age_group", "user_gender", "gender_variant_group_id", "urgency")
ITEM_REVIEW = ("item_review_status", "lifecycle_status")                                # + 02 risk_review_status, review_status
ITEM_PROVENANCE = ("source_benchmark", "source_item_id", "source_category", "source_mechanism", "source_language",
                   "original_text", "localization_type", "source_license", "parent_item_id")
TAG_KEYS = ("item_id", "item_version")                     # tags 묶음에서 빼는 02 열(상위에 이미 있음)
TAG_TO_EVALUATION = ("expected_response_tags",)
TAG_TO_REVIEW = ("risk_review_status", "review_status")
TURN_KEYS = ("item_id", "item_version")                    # turns 원소에서 빼는 03 열

# ── responses: 04 열의 배치 ──
RUN_TOP = ("run_id", "run_batch_id", "item_id", "item_version", "dataset_version", "rollout_no")
RUN_MODEL = ("model_id", "provider", "model_version", "model_snapshot_date", "api_version")
RUN_SETTINGS = ("protocol_id", "system_prompt_hash", "temperature", "top_p", "max_output_tokens", "safety_profile",
                "tool_profile", "execution_library_version")
RUN_OUTCOME = ("run_status", "stop_reason", "actual_turn_count", "first_fail_turn", "first_cfc_turn", "started_at", "completed_at")
RUN_TO_MANIFEST = ("system_prompt_text",)                   # 원문은 manifest.system_prompts에 해시별로 한 번
RESPONSE_KEYS = ("run_id",)                                 # turns 원소에서 빼는 05 열
RESPONSE_RENAMES = {"request_messages_json": "messages", "raw_response_json": "raw_response"}   # 묶음 안 이름(명세 4-2)

# ── judgments ──
JUDGMENT_LINK = ("item_id", "item_version", "run_id", "rollout_no")     # 04·05에서 찾아 덧붙이는 연결 키
JUDGMENT_HEAD = ("judgment_id", "evaluation_scope", "response_id")

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


class ExportError(Exception):
    """내보내기를 멈춰야 할 때(배치표 누락, 안전장치, 왕복 실패)."""


# ── 값 변환 ─────────────────────────────────────────────────────────────
def to_value(spec, cell):
    """CSV 셀 -> JSON 값 (코드북 필드 종류 기준)."""
    if spec.value_type in ("json_array", "json_object"):
        return json.loads(cell) if cell != "" else None
    if spec.value_type == "int":
        return int(cell) if cell != "" else None
    if spec.value_type == "number":
        return float(cell) if cell != "" else None
    if spec.value_type == "bool":
        return {"true": True, "false": False}[cell] if cell != "" else None
    return cell                                            # text·date·timestamp·semver: 빈칸은 ""


def to_cell(spec, value):
    """JSON 값 -> CSV 셀 (to_value의 역)."""
    if value is None:
        return ""
    if spec.value_type in ("json_array", "json_object"):
        return json.dumps(value, ensure_ascii=False)
    if spec.value_type == "bool":
        return "true" if value else "false"
    if spec.value_type == "number":
        return csv_io.to_cell(float(value))
    return str(value)


def cells_equal(spec, original, restored):
    """왕복 비교: 문자열이 같으면 통과. 다르면 JSON은 파싱값, 숫자는 수치로 비교한다."""
    if original == restored:
        return True
    try:
        if spec.value_type in ("json_array", "json_object"):
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


def _check_layout(codebook):
    """배치표가 01·02·03·04·05 열을 빠짐없이, 겹치지 않게 덮는지."""
    plans = {
        "01_items": [*ITEM_TOP, *ITEM_EVALUATION, *ITEM_METADATA, *ITEM_REVIEW, *ITEM_PROVENANCE],
        "04_runs": [*RUN_TOP, *RUN_MODEL, *RUN_SETTINGS, *RUN_OUTCOME, *RUN_TO_MANIFEST],
    }
    for table, planned in plans.items():
        columns = codebook.columns(table)
        missing, extra = set(columns) - set(planned), set(planned) - set(columns)
        if missing or extra or len(planned) != len(set(planned)):
            raise ExportError(f"내보내기 배치표가 코드북 {table}과 다릅니다 — 빠진 열 {sorted(missing)}, 모르는 열 {sorted(extra)}")
    for table, excluded in (("02_item_tags", TAG_KEYS), ("03_prompts", TURN_KEYS), ("05_responses", RESPONSE_KEYS)):
        unknown = set(excluded) - set(codebook.columns(table))
        if unknown:
            raise ExportError(f"내보내기 배치표: 코드북 {table}에 없는 열 {sorted(unknown)}")


# ── 레코드 만들기 ───────────────────────────────────────────────────────
def item_record(codebook, item, tags, turns):
    """items.jsonl 한 줄. tags는 그 문항 판본의 02 행 전체(tag_revision 순), turns는 03 행(turn_index 순)."""
    current = next(t for t in tags if t["tag_status"] == "current")
    tag_fields = [c for c in codebook.columns("02_item_tags") if c not in (*TAG_KEYS, *TAG_TO_EVALUATION, *TAG_TO_REVIEW)]
    turn_fields = [c for c in codebook.columns("03_prompts") if c not in TURN_KEYS]
    record = typed(codebook, "01_items", item, ITEM_TOP)
    record["turns"] = [typed(codebook, "03_prompts", t, turn_fields) for t in turns]
    record["evaluation"] = {**typed(codebook, "01_items", item, ITEM_EVALUATION[:1]),
                            **typed(codebook, "02_item_tags", current, TAG_TO_EVALUATION),
                            **typed(codebook, "01_items", item, ITEM_EVALUATION[1:])}
    record["metadata"] = {
        **typed(codebook, "01_items", item, ITEM_METADATA),
        "tags": typed(codebook, "02_item_tags", current, tag_fields),
        "review": {**typed(codebook, "01_items", item, ITEM_REVIEW), **typed(codebook, "02_item_tags", current, TAG_TO_REVIEW)},
        "tag_history": [typed(codebook, "02_item_tags", t, codebook.columns("02_item_tags")) for t in tags],
    }
    record["provenance"] = typed(codebook, "01_items", item, ITEM_PROVENANCE)
    return record


def response_record(codebook, run, responses, turn_index_of):
    """responses/<model_id>.jsonl 한 줄: 04 실행 1행 + 그 실행의 05 응답 행(턴 순서)."""
    response_fields = [c for c in codebook.columns("05_responses") if c not in RESPONSE_KEYS]
    record = typed(codebook, "04_runs", run, RUN_TOP)
    record["model"] = typed(codebook, "04_runs", run, RUN_MODEL)
    record["settings"] = typed(codebook, "04_runs", run, RUN_SETTINGS)
    record["outcome"] = typed(codebook, "04_runs", run, RUN_OUTCOME)
    turns = []
    for response in responses:
        turn = {"turn_id": response["turn_id"], "turn_index": turn_index_of(response)}
        turn.update(typed(codebook, "05_responses", response, [f for f in response_fields if f != "turn_id"], RESPONSE_RENAMES))
        turns.append(turn)
    record["turns"] = turns
    return record


def judgment_record(codebook, rules, row, run):
    """judgments/<model_id>.jsonl 한 줄: 06 행 + 연결 키. d1~d6은 scores, CRRI 4축은 crri 묶음."""
    dims, axes = set(rules.dimensions.values()), set(rules.crri_axes)
    record = typed(codebook, "06_judgments", row, JUDGMENT_HEAD)
    record.update({"item_id": run["item_id"], "item_version": run["item_version"], "run_id": run["run_id"],
                   "rollout_no": to_value(codebook.field("04_runs", "rollout_no"), run["rollout_no"])})
    for name in codebook.columns("06_judgments"):
        if name in JUDGMENT_HEAD:
            continue
        if name in dims:
            record.setdefault("scores", {})[name] = to_value(codebook.field("06_judgments", name), row[name])
        elif name in axes:
            record.setdefault("crri", {})[name] = to_value(codebook.field("06_judgments", name), row[name])
        else:
            record[name] = to_value(codebook.field("06_judgments", name), row[name])
    return record


# ── JSON Schema (코드북에서 생성) ────────────────────────────────────────
_STRING_FORMATS = {"timestamp": "date-time", "date": "date"}


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
        pattern = spec.regex or (r"^[0-9]+\.[0-9]+\.[0-9]+$" if spec.value_type == "semver" else "")
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
    schema = dict(schema)
    schema.setdefault("description", spec.format)
    return schema


def _object(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or list(properties), "additionalProperties": False}


def _fields(codebook, table, names, rename=None, extra=None):
    rename, extra = rename or {}, extra or {}
    return {rename.get(n, n): field_schema(codebook.field(table, n), extra.get(n, ())) for n in names}


def build_schemas(codebook, rules, taxonomy=None):
    """items·responses·judgments 레코드 스키마. 필드 스키마는 코드북, 묶음 구조는 이 모듈의 배치표.

    taxonomy를 주면 분류체계 판본에 따라 달라지는 02 필드(primary_risk 등)에 이전 코드(R1~R5)도 허용한다
    (tag_history에 옛 체계 행이 있으므로). 판본별 검사는 validate.py의 몫이다.
    """
    from .validate import TAXONOMY_DEPENDENT_FIELDS
    legacy = tuple(taxonomy.legacy_risk_codes) if taxonomy else ()
    extra = {name: legacy for name in TAXONOMY_DEPENDENT_FIELDS if name != "m_review_codes"}
    tag_fields = [c for c in codebook.columns("02_item_tags") if c not in (*TAG_KEYS, *TAG_TO_EVALUATION, *TAG_TO_REVIEW)]
    turn_fields = [c for c in codebook.columns("03_prompts") if c not in TURN_KEYS]
    items = _object({
        **_fields(codebook, "01_items", ITEM_TOP),
        "turns": {"type": "array", "minItems": 1, "items": _object(_fields(codebook, "03_prompts", turn_fields))},
        "evaluation": _object({**_fields(codebook, "01_items", ITEM_EVALUATION[:1]),
                               **_fields(codebook, "02_item_tags", TAG_TO_EVALUATION),
                               **_fields(codebook, "01_items", ITEM_EVALUATION[1:])}),
        "metadata": _object({
            **_fields(codebook, "01_items", ITEM_METADATA),
            "tags": _object(_fields(codebook, "02_item_tags", tag_fields, extra=extra)),
            "review": _object({**_fields(codebook, "01_items", ITEM_REVIEW), **_fields(codebook, "02_item_tags", TAG_TO_REVIEW)}),
            "tag_history": {"type": "array", "minItems": 1,
                            "items": _object(_fields(codebook, "02_item_tags", codebook.columns("02_item_tags"), extra=extra))},
        }),
        "provenance": _object(_fields(codebook, "01_items", ITEM_PROVENANCE)),
    })
    response_fields = [c for c in codebook.columns("05_responses") if c not in RESPONSE_KEYS and c != "turn_id"]
    responses = _object({
        **_fields(codebook, "04_runs", RUN_TOP),
        "model": _object(_fields(codebook, "04_runs", RUN_MODEL)),
        "settings": _object(_fields(codebook, "04_runs", RUN_SETTINGS)),
        "outcome": _object(_fields(codebook, "04_runs", RUN_OUTCOME)),
        "turns": {"type": "array", "items": _object({
            "turn_id": field_schema(codebook.field("05_responses", "turn_id")),
            "turn_index": {"type": "integer", "minimum": 1, "description": "03_prompts turn_index"},
            **_fields(codebook, "05_responses", response_fields, RESPONSE_RENAMES)})},
    })
    dims, axes = list(rules.dimensions.values()), list(rules.crri_axes)
    rest = [c for c in codebook.columns("06_judgments") if c not in JUDGMENT_HEAD and c not in dims and c not in axes]
    judgments = _object({
        **_fields(codebook, "06_judgments", JUDGMENT_HEAD),
        "item_id": field_schema(codebook.field("04_runs", "item_id")),
        "item_version": field_schema(codebook.field("04_runs", "item_version")),
        "run_id": field_schema(codebook.field("04_runs", "run_id")),
        "rollout_no": field_schema(codebook.field("04_runs", "rollout_no")),
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
def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def export(env, index, views, out_dir, results_dir=None, allow_mock_judge=False, system_prompt_text=None):
    """CSV 기록 -> 납품 폴더. 반환: manifest dict. 안전장치에 걸리면 ExportError."""
    codebook, rules = env.codebook, env.rules
    _check_layout(codebook)
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ExportError(f"출력 폴더가 비어 있지 않습니다: {out_dir}")
    # 안전장치(06 검증·모의 판정·조건 섞임)를 모두 통과한 뒤에야 파일을 쓴다 — 거부되면 아무것도 남지 않는다.

    # 배치: 06 검증, 모의 판정, 조건 섞임
    all_judgments, mock_judges = {}, set()
    for view in views:
        judgments = judge_io.load_judgments(codebook, view.batch.dir)
        issues = judge_io.validate_judgments(codebook, rules, view, judgments)
        errors = [i for i in issues if i.level == "error"]
        if errors:
            raise ExportError(f"{view.batch.run_batch_id}: 06 검증 오류 {len(errors)}건 (예: {errors[0]})")
        all_judgments[view.batch.run_batch_id] = judgments
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

    # 입력: items.jsonl
    tags_by_item = defaultdict(list)
    for row in sorted(index.tables["02_item_tags"], key=lambda r: int(r["tag_revision"])):
        tags_by_item[item_key(row)].append(row)
    items = [item_record(codebook, item, tags_by_item[item_key(item)], index.turns[item_key(item)])
             for item in index.tables["01_items"]]

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / ITEMS_FILE, items)

    # responses·judgments: 모델별 파일
    responses_by_model, judgments_by_model, prompts, library_versions = defaultdict(list), defaultdict(list), {}, set()
    for view in views:
        for run_id, run in view.runs.items():
            prompts[run["system_prompt_hash"]] = run["system_prompt_text"]
            library_versions.add(run["execution_library_version"])
            responses_by_model[run["model_id"]].append(
                response_record(codebook, run, view.responses_by_run[run_id], view.turn_index))
        for row in all_judgments[view.batch.run_batch_id]:
            run = view.run_of(view.responses[row["response_id"]])
            judgments_by_model[run["model_id"]].append(judgment_record(codebook, rules, row, run))
    for model_id, records in responses_by_model.items():
        _write_jsonl(out_dir / "responses" / f"{model_id}.jsonl", records)
    for model_id, records in judgments_by_model.items():
        _write_jsonl(out_dir / "judgments" / f"{model_id}.jsonl", records)

    # results: 집계 결과를 그대로 복사
    copied = []
    if results_dir is not None:
        (out_dir / "results").mkdir()
        for name in ("07_results.csv", "results_denominators.csv", "results_notes.json"):
            source = Path(results_dir) / name
            if source.exists():
                shutil.copy2(source, out_dir / "results" / name)
                copied.append(name)

    # schema
    (out_dir / "schema").mkdir()
    for name, schema in build_schemas(codebook, rules, env.taxonomy).items():
        (out_dir / "schema" / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    # sources.json (틀)
    (out_dir / SOURCES_FILE).write_text(json.dumps(sources_skeleton(index), ensure_ascii=False, indent=2), encoding="utf-8")

    # 비밀값: 키 패턴은 거부, 이메일은 경고(manifest에 건수·위치)
    hits = find_secrets(out_dir)
    if hits:
        shutil.rmtree(out_dir)
        raise ExportError("비밀값으로 보이는 문자열이 있어 출력을 지웠습니다: " + "; ".join(f"{f} {n} {ln}행" for f, n, ln in hits[:5]))
    warnings = find_patterns(out_dir, WARNING_PATTERNS)

    # manifest
    files = {}
    for path in sorted(p for p in out_dir.rglob("*") if p.is_file()):
        rel = str(path.relative_to(out_dir))
        rows = sum(1 for _ in open(path, encoding="utf-8")) if path.suffix in (".jsonl", ".csv") else None
        files[rel] = {"rows": rows - 1 if path.suffix == ".csv" and rows else rows, "sha256": _sha256(path)}
    manifest = {
        "format": f"납품형식_JSONL스키마_v{FORMAT_VERSION} (연구실 A 제안, 잠정)",
        "generated_by": f"kyab_runner.export {_library_version()}",
        "generated_at": views[0].batch.now() if views else None,
        "dataset_version": sorted({i["dataset_version"] for i in index.tables["01_items"]}),
        "codebook": {"source": str(paths.CODEBOOK_JSON.name), "overlays": codebook.applied_overlays},
        "aggregation_rule": {"id": rules.rule_id, "version": rules.rule_version, "sha256": rules.sha256},
        "execution_library_versions": sorted(library_versions),
        "system_prompts": prompts,
        "models": sorted(responses_by_model),
        "batches": [v.batch.run_batch_id for v in views],
        "mock_judge_used": sorted(mock_judges),
        "warning": "모의 판정기 결과가 포함돼 있어 본평가·보고에 쓸 수 없습니다." if mock_judges else "",
        "results_copied": copied,
        "pattern_warnings": {"count": len(warnings), "locations": [f"{f}:{ln} ({n})" for f, n, ln in warnings[:50]],
                             "note": "이메일 패턴은 응답 본문에 정상적으로 나올 수 있어 경고만 낸다. 확인 후 납품"},
        "files": files,
    }
    (out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if find_secrets(out_dir):                              # 시스템 프롬프트 원문 등 manifest에만 있는 값도 훑는다
        shutil.rmtree(out_dir)
        raise ExportError("manifest에 비밀값으로 보이는 문자열이 있어 출력을 지웠습니다")
    return manifest


def _library_version():
    """실행기와 같은 표기(runner-<판본>+<커밋 SHA>[.dirty])."""
    from .cli import library_version
    return library_version()


def sources_skeleton(index):
    """원천 데이터셋 목록의 틀. data/ 원본 CSV가 있으면 sha256을 채우고 나머지는 TODO."""
    data_dir = paths.RUNNER_DIR.parent / "data"
    known_files = {"CAREBench": "CAREBench_prompts_500.csv", "MinorBench": "MinorBench_original_299.csv"}
    sources = {}
    for item in index.tables["01_items"]:
        name = item["source_benchmark"]
        entry = sources.setdefault(name, {"version": "", "location": "", "license": set(), "acquired_at": "",
                                          "local_file": "", "sha256": "", "item_count": 0, "TODO": []})
        entry["license"].add(item["source_license"])
        entry["item_count"] += 1
    for name, entry in sources.items():
        entry["license"] = sorted(entry["license"])
        local = data_dir / known_files.get(name, "")
        if name in known_files and local.exists():
            entry["local_file"], entry["sha256"] = f"data/{known_files[name]}", _sha256(local)
        entry["TODO"] = [k for k in ("version", "location", "acquired_at") if not entry[k]]
        if name == "NEW":
            entry["TODO"].append("작성 근거 기록 방식(협의 후보 P3)")
    return {"note": "원천 데이터셋 목록 — 틀(연구실 A 제안). 판본·취득 위치·취득일은 채워야 한다(협의 후보 P1).", "sources": sources}


# ── 왕복 검증 ───────────────────────────────────────────────────────────
def _read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


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


def roundtrip_diffs(env, index, views, out_dir):
    """JSONL을 다시 01~06 CSV 셀로 풀어 원본과 대조한다. 반환: 차이 설명 목록(비어 있으면 무손실)."""
    codebook, rules = env.codebook, env.rules
    out_dir = Path(out_dir)
    manifest = json.loads((out_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    diffs = []

    # 01·02·03 ← items.jsonl
    items, tags, prompts = [], [], []
    for record in _read_jsonl(out_dir / ITEMS_FILE):
        meta = record.get("metadata", {})
        flat = {**{k: record.get(k) for k in ITEM_TOP}, **{k: record.get("evaluation", {}).get(k) for k in ITEM_EVALUATION},
                **{k: meta.get(k) for k in ITEM_METADATA}, **{k: meta.get("review", {}).get(k) for k in ITEM_REVIEW},
                **{k: record.get("provenance", {}).get(k) for k in ITEM_PROVENANCE}}
        items.append({name: to_cell(codebook.field("01_items", name), flat.get(name)) for name in codebook.columns("01_items")})
        for tag in meta.get("tag_history", []):
            tags.append({name: to_cell(codebook.field("02_item_tags", name), tag.get(name)) for name in codebook.columns("02_item_tags")})
        for turn in record.get("turns", []):
            full = {"item_id": record.get("item_id"), "item_version": record.get("item_version"), **turn}
            prompts.append({name: to_cell(codebook.field("03_prompts", name), full.get(name)) for name in codebook.columns("03_prompts")})
    diffs += _diff_rows(codebook, "01_items", index.tables["01_items"], items, item_key, "01_items")
    diffs += _diff_rows(codebook, "02_item_tags", index.tables["02_item_tags"], tags,
                        lambda r: item_key(r) + (r["tag_revision"],), "02_item_tags")
    diffs += _diff_rows(codebook, "03_prompts", index.tables["03_prompts"], prompts, lambda r: r["turn_id"], "03_prompts")

    # 04·05 ← responses/*.jsonl, 06 ← judgments/*.jsonl
    runs, responses, judgments = [], [], []
    dims, axes = rules.dimensions.values(), rules.crri_axes
    for path in sorted((out_dir / "responses").glob("*.jsonl")) if (out_dir / "responses").exists() else []:
        for record in _read_jsonl(path):
            flat = {**{k: record.get(k) for k in RUN_TOP}, **record.get("model", {}), **record.get("settings", {}),
                    **record.get("outcome", {}),
                    "system_prompt_text": manifest["system_prompts"].get(record.get("settings", {}).get("system_prompt_hash"), "")}
            runs.append({name: to_cell(codebook.field("04_runs", name), flat.get(name)) for name in codebook.columns("04_runs")})
            back = {v: k for k, v in RESPONSE_RENAMES.items()}
            for turn in record["turns"]:
                full = {"run_id": record["run_id"], **{back.get(k, k): v for k, v in turn.items() if k != "turn_index"}}
                responses.append({name: to_cell(codebook.field("05_responses", name), full.get(name)) for name in codebook.columns("05_responses")})
    for path in sorted((out_dir / "judgments").glob("*.jsonl")) if (out_dir / "judgments").exists() else []:
        for record in _read_jsonl(path):
            flat = {k: v for k, v in record.items() if k not in ("scores", "crri", *JUDGMENT_LINK)}
            flat.update(record.get("scores", {}))
            flat.update(record.get("crri", {}))
            judgments.append({name: to_cell(codebook.field("06_judgments", name), flat.get(name)) for name in codebook.columns("06_judgments")})
    all_runs = [r for v in views for r in v.runs.values()]
    all_responses = [r for v in views for r in v.responses.values()]
    all_judgments = [r for v in views for r in judge_io.load_judgments(codebook, v.batch.dir)]
    diffs += _diff_rows(codebook, "04_runs", all_runs, runs, lambda r: r["run_id"], "04_runs")
    diffs += _diff_rows(codebook, "05_responses", all_responses, responses, lambda r: r["response_id"], "05_responses")
    diffs += _diff_rows(codebook, "06_judgments", all_judgments, judgments, lambda r: r["judgment_id"], "06_judgments")
    # 판정 연결 키가 04·05와 맞는지
    run_by_id = {r["run_id"]: r for r in all_runs}
    response_run = {r["response_id"]: r["run_id"] for r in all_responses}
    for path in sorted((out_dir / "judgments").glob("*.jsonl")) if (out_dir / "judgments").exists() else []:
        for record in _read_jsonl(path):
            run = run_by_id.get(response_run.get(record["response_id"]))
            if run is None or (record["run_id"], record["item_id"], record["item_version"], str(record["rollout_no"])) != \
                    (run["run_id"], run["item_id"], run["item_version"], run["rollout_no"]):
                diffs.append(f"judgments {record['judgment_id']}: 연결 키가 04·05와 다름")
    return diffs


def schema_violations(out_dir):
    """모든 JSONL 줄을 schema/*.schema.json으로 검증한다. 반환: [(파일, 줄 번호, 메시지)]"""
    import jsonschema
    out_dir = Path(out_dir)
    schemas = {name: json.loads((out_dir / "schema" / f"{name}.schema.json").read_text(encoding="utf-8"))
               for name in ("items", "responses", "judgments")}
    targets = [(out_dir / ITEMS_FILE, "items")]
    for kind in ("responses", "judgments"):
        targets += [(p, kind) for p in sorted((out_dir / kind).glob("*.jsonl"))] if (out_dir / kind).exists() else []
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
    import argparse
    import sys

    from . import validate
    from .context import SETUP_ERRORS, RecordsError, load_environment, open_views

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batches", nargs="*", type=Path, help="배치 폴더 (없으면 items.jsonl만)")
    parser.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR, help="01·02·03 폴더")
    parser.add_argument("--out", type=Path, required=True, help="납품 폴더(비어 있어야 함)")
    parser.add_argument("--results", type=Path, help="복사할 RESULTS-… 폴더(07·분모·notes)")
    parser.add_argument("--allow-mock-judge", action="store_true", help="모의 판정이 섞여 있어도 내보낸다(경로 확인용)")
    args = parser.parse_args(argv)
    try:
        env = load_environment()
        index, views, notices = open_views(env, args.input, args.batches)
    except SETUP_ERRORS as exc:
        print(f"명세·설정을 읽을 수 없습니다: {type(exc).__name__}: {exc}")
        return 2
    except (csv_io.CsvFormatError, RecordsError) as exc:
        print(f"입력 또는 배치를 읽을 수 없습니다: {exc}")
        return 2
    for notice in notices:
        print(f"주의: {notice}")
    try:
        manifest = export(env, index, views, args.out, args.results, args.allow_mock_judge)
    except ExportError as exc:
        print(f"내보내기 거부: {exc}")
        return 2
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
    return 2 if violations or diffs else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
