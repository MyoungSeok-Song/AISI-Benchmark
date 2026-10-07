"""판정·집계 규칙(config/aggregation_rules.yaml)과 판정기 등록부(config/judges.yaml) 로드.

코드북이 정하지 않은 판정·집계 결정은 모두 aggregation_rules.yaml에 있다. 코드는 이 모듈을
거쳐서만 그 값을 읽는다. 로드할 때 규칙 파일이 가리키는 필드·허용값이 코드북에 실제로
있는지 확인해, 코드북이 바뀌었는데 규칙 파일이 옛 이름을 쓰는 일을 막는다.
"""
import hashlib
from dataclasses import dataclass

import yaml

from . import paths
from .config import build_entry
from .errors import SetupError, load_yaml

PROVIDER_BLOCK_POLICIES = ("count_as_refusal", "exclude")
DIMENSION_SOURCES = ("conversation_then_turn_mean", "conversation_only")
# 평가 단위는 이 조합만 구현돼 있다: 단일턴은 turn 행, 다중턴은 conversation 행.
SUPPORTED_EVALUATION_UNIT = {"single": "turn", "multi": "conversation"}
SUPPORTED_CI_METHODS = ("wilson_95",)
# primary_judgment_set.tag_revision이 가질 수 있는 값(현재 태그 판본만 구현). 다른 값은 조용히 '모든 판본 허용'이 되므로 막는다.
# 규칙 파일의 키워드다 — 02 tag_status 값(vocab.TAG_CURRENT)과 철자가 같을 뿐 별개. 코드북이 tag_status 값을 바꿔도
# 이 키워드는 그대로이며, judge_io는 이 상수와만 비교한다(둘을 한 상수로 묶으면 코드북 변경 때 한정이 조용히 풀린다).
TAG_REVISION_CURRENT = "current"
SUPPORTED_TAG_REVISION = (TAG_REVISION_CURRENT,)
PRIMARY_SET_KEYS = ("judge_type", "judge_status", "adjudicated_first", "tag_revision")
# judgment.blank_allowed[].when에 쓸 수 있는 조건 키(judge_io.blank_allowed_fields가 보는 사실). 규칙 파일 설명(yaml) 참고
BLANK_ALLOWED_WHEN_KEYS = ("conversation_mode", "case_type", "evaluation_scope")


class RulesError(SetupError):
    """규칙 파일이 코드북과 맞지 않거나 지원하지 않는 값을 담고 있을 때."""


@dataclass(frozen=True)
class JudgeEntry:
    """judges.yaml의 판정기 1개. 06_judgments의 판정기 관련 필드의 원천."""
    judge_id: str
    adapter: str
    judge_type: str
    judge_version: str
    judge_prompt_id: str
    judge_prompt_version: str
    production: bool


@dataclass(frozen=True)
class Rules:
    """aggregation_rules.yaml 전체 + 판정기 등록부."""
    raw: dict
    sha256: str             # 규칙 파일의 해시. 집계 기록(results_notes.json)에 남긴다
    judges: dict            # {judge_id: JudgeEntry}
    self_identification_patterns: tuple = ()    # 눈가림 점검용 문자열 (judges.yaml)
    none_token: str = ""    # critical_failure_code의 '치명적 실패 없음' 기록값(NONE). 출처는 overlay(코드북 FieldSpec)
    load_warnings: tuple = ()   # 로드 때 기본값으로 채운 항목 등 사람이 봐야 할 안내

    def __getitem__(self, key):
        return self.raw[key]

    @property
    def rule_id(self):
        return self.raw["aggregation_rule_id"]

    @property
    def rule_version(self):
        return self.raw["aggregation_rule_version"]

    @property
    def dimensions(self):
        """{'D1': 'd1_risk_awareness', ...} 규칙 파일에 적힌 순서."""
        return self.raw["rubric_dimensions"]

    @property
    def crri_axes(self):
        return self.raw["crri_axes"]

    @property
    def score_fields(self):
        """0~2 점수 필드 전체 (루브릭 6차원 + CRRI 4축). 06 열 이름."""
        return list(self.dimensions.values()) + list(self.crri_axes)

    @property
    def outcome_fields(self):
        """판정 결과 필드(verdict, over_refusal, referral_present, critical_failure_code).

        판정이 끝난 행(completed·adjudicated)에는 필수다(가정 J4). CFC는 NONE 또는 승인 코드여야 한다.
        """
        return self.raw["judgment"]["outcome_fields"]

    def has_critical_failure(self, row):
        """이 판정 행에 치명적 실패가 있는가: NONE(none_token)이 아닌 코드가 있을 때.

        빈칸도 '있음'은 아니지만 '없음'을 적은 것도 아니다(미채점·누락 — 완료 행에서는 rule_outcome_required가 오류로 잡는다).
        여기서는 집계·사후 산출이 빈칸을 실패로 세지 않게만 한다. 빈칸과 NONE을 구분하는 일은 판정 검증(rule_cfc·
        rule_outcome_required)의 몫이다. 이 판단을 쓰는 곳: first_cfc_turn, CFR, 가상 판정, 모의 판정기.
        """
        return row["critical_failure_code"] not in ("", self.none_token)

    def is_unfinished(self, row):
        """가정 J4: 판정이 끝나지 못한 행인가 (judge_status가 failed·needs_review)."""
        return row["judge_status"] in self.raw["judgment"]["unfinished_judge_status"]

    def rubric_version(self, rubric_id):
        """루브릭 ID의 등록 판본. 등록되지 않았으면 None."""
        return self.raw["judgment"]["rubric_versions"].get(rubric_id)

    def non_production_judges(self):
        return {judge_id for judge_id, entry in self.judges.items() if not entry.production}


def _require_columns(codebook, table, names, where):
    unknown = [n for n in names if n not in codebook.columns(table)]
    if unknown:
        raise RulesError(f"aggregation_rules.yaml {where}: 코드북 {table}에 없는 필드 {unknown}")


def _check_cfc_tokens(codebook, raw):
    """CFC 기록값의 출처 일치: 규칙 파일의 NONE 값은 overlay(코드북)의 none_token과 같아야 한다.

    코드북 CFC 필드에 허용값 목록이 생기면(v0.3) 등록 코드와 NONE이 모두 그 안에 있어야 한다.
    """
    spec = codebook.field("06_judgments", "critical_failure_code")
    declared = raw["judgment"].get("no_critical_failure_code", "")
    if not spec.none_token:
        raise RulesError("코드북 06 critical_failure_code에 none_token이 없습니다 (overlay OV-R1005-3). "
                         "치명적 실패 없음을 적을 값을 정할 수 없습니다")
    if declared != spec.none_token:
        raise RulesError(f"aggregation_rules.yaml no_critical_failure_code={declared!r}가 코드북(overlay) none_token="
                         f"{spec.none_token!r}과 다릅니다. 값의 출처는 overlay 한 곳이어야 합니다")
    registered = raw["judgment"]["registered_cfc_codes"]
    if not registered:
        raise RulesError("registered_cfc_codes가 비어 있습니다. 승인 코드 목록(또는 자리표시 1개)이 있어야 합니다")
    if spec.none_token in registered or "" in registered:
        raise RulesError(f"registered_cfc_codes에 {spec.none_token!r}나 빈 값이 들어 있습니다. NONE은 코드가 아니라 '없음' 표기입니다")
    if "critical_failure_code" not in raw["judgment"]["outcome_fields"]:
        raise RulesError("none_token이 있으면 outcome_fields에 critical_failure_code가 있어야 합니다(완료 행의 빈칸을 오류로 잡기 위해)")
    if spec.enum:
        missing = [v for v in [*registered, spec.none_token] if v not in spec.enum]
        if missing:
            raise RulesError(f"코드북 06 critical_failure_code 허용값에 없는 값 {missing} (등록 CFC 코드·NONE은 허용값에 있어야 함)")
    return spec.none_token


def _check_field_references(codebook, raw):
    """규칙 파일이 가리키는 필드 이름·허용값이 코드북에 실제로 있는지 확인한다(코드북이 바뀌었는데 규칙이 옛 이름을 쓰는 일 방지)."""
    _require_columns(codebook, "06_judgments", raw["rubric_dimensions"].values(), "rubric_dimensions")
    _require_columns(codebook, "06_judgments", raw["crri_axes"], "crri_axes")
    for entry in raw["judgment"]["blank_allowed"]:
        _require_columns(codebook, "06_judgments", entry["fields"], f"blank_allowed {entry['id']}")
    _require_columns(codebook, "06_judgments", raw["judgment"]["outcome_fields"], "judgment.outcome_fields")
    statuses = codebook.field("06_judgments", "judge_status").enum
    unknown = [v for v in raw["judgment"]["unfinished_judge_status"] if v not in statuses]
    if unknown:
        raise RulesError(f"aggregation_rules.yaml unfinished_judge_status: 코드북 06 judge_status 허용값이 아님 {unknown}")
    judge_input = raw["judge_input"]
    _require_columns(codebook, "01_items", judge_input["item_fields"], "judge_input.item_fields")
    _require_columns(codebook, "02_item_tags", judge_input["tag_fields"], "judge_input.tag_fields")
    _require_columns(codebook, "03_prompts", judge_input["turn_fields"], "judge_input.turn_fields")

    aggregation = raw["aggregation"]
    slice_levels = codebook.field("07_results", "slice_level").enum
    unknown = [name for name in aggregation["slices"] if name not in slice_levels]
    if unknown:
        raise RulesError(f"aggregation_rules.yaml slices: 코드북 07 slice_level 허용값이 아님 {unknown}")
    item_or_tag = set(codebook.columns("01_items")) | set(codebook.columns("02_item_tags"))
    keyed = {**{f"slices.{k}": v for k, v in aggregation["slices"].items()},
             **{f"notes_slices.{k}": v for k, v in aggregation["notes_slices"].items()},
             **{f"populations.{k}": list(v) for k, v in aggregation["populations"].items()},
             "turn_type_field": [aggregation["turn_type_field"]], "age_band_field": [aggregation["age_band_field"]]}
    for where, keys in keyed.items():
        missing = [k for k in keys if k not in item_or_tag]
        if missing:
            raise RulesError(f"aggregation_rules.yaml {where}: 01·02에 없는 필드 {missing}")


def _check_judgment_options(codebook, raw):
    """주 판정 집합·빈값 허용 조건의 키와 값(judge-09). 오타가 '모든 판본 허용'이나 검증 중 KeyError로 새지 않게."""
    spec = raw["aggregation"]["primary_judgment_set"]
    if set(spec) != set(PRIMARY_SET_KEYS):
        raise RulesError(f"aggregation_rules.yaml primary_judgment_set의 키는 {PRIMARY_SET_KEYS}여야 함 (현재 {sorted(spec)})")
    for field in ("judge_type", "judge_status"):
        allowed = codebook.field("06_judgments", field).enum
        if spec[field] not in allowed:
            raise RulesError(f"primary_judgment_set.{field}={spec[field]!r}: 코드북 06 {field} 허용값 {allowed}이 아님")
    if not isinstance(spec["adjudicated_first"], bool):
        raise RulesError(f"primary_judgment_set.adjudicated_first는 true/false여야 함 (현재 {spec['adjudicated_first']!r})")
    if spec["tag_revision"] not in SUPPORTED_TAG_REVISION:
        raise RulesError(f"primary_judgment_set.tag_revision={spec['tag_revision']!r}: 지원 {SUPPORTED_TAG_REVISION}")
    for entry in raw["judgment"]["blank_allowed"]:
        unknown = sorted(set(entry.get("when", {})) - set(BLANK_ALLOWED_WHEN_KEYS))
        if unknown:
            raise RulesError(f"blank_allowed {entry.get('id')}: when의 키 {unknown}는 쓸 수 없음 (가능: {BLANK_ALLOWED_WHEN_KEYS})")


def _check_supported_options(raw):
    """코드북과 무관하게 러너가 구현한 선택지만 허용하는 항목(평가 단위·차원 출처·CI 방법·차단 정책)."""
    aggregation = raw["aggregation"]
    if aggregation["evaluation_unit"] != SUPPORTED_EVALUATION_UNIT:
        raise RulesError(f"지원하지 않는 evaluation_unit {aggregation['evaluation_unit']} (가능: {SUPPORTED_EVALUATION_UNIT})")
    if aggregation["multi_turn_dimension_source"] not in DIMENSION_SOURCES:
        raise RulesError(f"알 수 없는 multi_turn_dimension_source {aggregation['multi_turn_dimension_source']!r} "
                         f"(가능: {DIMENSION_SOURCES})")
    if aggregation["ci_method"] not in SUPPORTED_CI_METHODS:
        raise RulesError(f"지원하지 않는 ci_method {aggregation['ci_method']!r} (가능: {SUPPORTED_CI_METHODS})")
    if aggregation["provider_block_policy"] not in PROVIDER_BLOCK_POLICIES:
        raise RulesError(f"알 수 없는 provider_block_policy {aggregation['provider_block_policy']!r} "
                         f"(가능: {PROVIDER_BLOCK_POLICIES})")


# aggregation.inconclusive_report의 기본값. 블록이 없는 옛 규칙 파일(0.2.0 이전)도 읽히게 한다.
INCONCLUSIVE_REPORT_DEFAULTS = {"warn_rate": None, "report_failure_rate_if_inconclusive_failed": False}


def _fill_defaults(raw):
    """없는 선택 블록을 기본값으로 채우고, 채운 블록의 값 종류를 검사한다(RulesError). 반환: 안내 문구 목록."""
    warnings = []
    aggregation = raw["aggregation"]
    if "substitute_control_target_risk" not in aggregation:
        # 재현성: 옛 판본(0.2.1 이전) 규칙 파일로 집계하면 그 판본의 동작(대조 문항을 위험군 행에 넣지 않음)이 나와야 한다
        aggregation["substitute_control_target_risk"] = False
        warnings.append("규칙 파일에 aggregation.substitute_control_target_risk가 없어 옛 동작(false: 대조 문항을 "
                        "위험군 행에 넣지 않음)으로 집계합니다 (0.2.2 이전 형식)")
    if not isinstance(aggregation["substitute_control_target_risk"], bool):
        raise RulesError("aggregation.substitute_control_target_risk는 true/false여야 함")
    if "inconclusive_report" not in aggregation:
        aggregation["inconclusive_report"] = dict(INCONCLUSIVE_REPORT_DEFAULTS)
        warnings.append("규칙 파일에 aggregation.inconclusive_report 블록이 없어 기본값(경고·참고값 꺼짐)을 씁니다 "
                        f"(판본 {raw['aggregation_rule_version']}, 0.2.1 이전 형식)")
    else:
        report = aggregation["inconclusive_report"]
        if report is None:                                    # 'inconclusive_report:' 빈 블록 = 기본값을 쓴다(경고 없음)
            report = aggregation["inconclusive_report"] = dict(INCONCLUSIVE_REPORT_DEFAULTS)
        if not isinstance(report, dict):
            raise RulesError(f"aggregation_rules.yaml inconclusive_report는 매핑(키: 값)이어야 함: {report!r}")
        unknown = set(report) - set(INCONCLUSIVE_REPORT_DEFAULTS)
        if unknown:
            raise RulesError(f"aggregation_rules.yaml inconclusive_report: 알 수 없는 키 {sorted(unknown)}")
        for key, default in INCONCLUSIVE_REPORT_DEFAULTS.items():
            report.setdefault(key, default)
        rate = report["warn_rate"]
        if rate is not None and (isinstance(rate, (bool, str)) or not 0 <= rate <= 1):
            raise RulesError(f"inconclusive_report.warn_rate는 0~1 사이 수 또는 null이어야 함: {rate!r}")
        if not isinstance(report["report_failure_rate_if_inconclusive_failed"], bool):
            raise RulesError("inconclusive_report.report_failure_rate_if_inconclusive_failed는 true/false여야 함")
    return warnings


def load_rules(codebook, rules_yaml=paths.AGGREGATION_RULES_YAML, judges_yaml=paths.JUDGES_YAML):
    """규칙 파일과 판정기 등록부 -> Rules. 규칙 파일 해시는 바이트 그대로 센다(results_notes.json rules_sha256)."""
    try:
        data = rules_yaml.read_bytes()
        raw = yaml.safe_load(data)
    except FileNotFoundError as exc:
        raise RulesError(f"{rules_yaml.name}: 파일이 없습니다 ({rules_yaml})") from exc
    except yaml.YAMLError as exc:
        raise RulesError(f"{rules_yaml.name}: yaml 문법 오류 — {exc}") from exc
    if not isinstance(raw, dict):
        raise RulesError(f"{rules_yaml.name}: 매핑(키: 값)이어야 합니다")
    load_warnings = _fill_defaults(raw)
    _check_field_references(codebook, raw)
    _check_supported_options(raw)
    _check_judgment_options(codebook, raw)
    none_token = _check_cfc_tokens(codebook, raw)
    judges, patterns = _load_judge_registry(codebook, load_yaml(judges_yaml, RulesError), judges_yaml.name)
    return Rules(raw=raw, sha256=hashlib.sha256(data).hexdigest(), judges=judges,
                 self_identification_patterns=patterns, none_token=none_token, load_warnings=tuple(load_warnings))


def _load_judge_registry(codebook, registry, source):
    """judges.yaml -> ({judge_id: JudgeEntry}, 눈가림 패턴 튜플). 키·종류·judge_type을 검사해 TypeError 대신 RulesError로.

    production은 bool이어야 한다: 'false'(문자열)는 참이라 모의 판정 거부가 통째로 꺼진다(judge-01).
    """
    if not isinstance(registry, dict) or not isinstance(registry.get("judges"), dict) or not registry["judges"]:
        raise RulesError(f"{source}: judges 매핑(비어 있지 않음)이 있어야 합니다")
    judges = {}
    for judge_id, entry in registry["judges"].items():
        judge = build_entry(JudgeEntry, "judge_id", judge_id, entry, source, RulesError)
        allowed = codebook.field("06_judgments", "judge_type").enum
        if judge.judge_type not in allowed:
            raise RulesError(f"{source} {judge_id}.judge_type={judge.judge_type!r}: 코드북 06 judge_type 허용값 {allowed}이 아님")
        for name in ("adapter", "judge_version", "judge_prompt_id", "judge_prompt_version"):
            if not isinstance(getattr(judge, name), str) or not getattr(judge, name):
                raise RulesError(f"{source} {judge_id}.{name}: 비어 있지 않은 문자열이어야 함")
        judges[judge_id] = judge
    patterns = registry.get("self_identification_patterns", ())
    if not isinstance(patterns, (list, tuple)) or not all(isinstance(p, str) for p in patterns):
        raise RulesError(f"{source} self_identification_patterns: 문자열 목록이어야 함")
    return judges, tuple(patterns)
