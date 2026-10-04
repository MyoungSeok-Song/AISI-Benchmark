"""판정·집계 규칙(config/aggregation_rules.yaml)과 판정기 등록부(config/judges.yaml) 로드.

코드북이 정하지 않은 판정·집계 결정은 모두 aggregation_rules.yaml에 있다. 코드는 이 모듈을
거쳐서만 그 값을 읽는다. 로드할 때 규칙 파일이 가리키는 필드·허용값이 코드북에 실제로
있는지 확인해, 코드북이 바뀌었는데 규칙 파일이 옛 이름을 쓰는 일을 막는다.
"""
import hashlib
from dataclasses import dataclass

import yaml

from . import paths

PROVIDER_BLOCK_POLICIES = ("count_as_refusal", "exclude")
DIMENSION_SOURCES = ("conversation_then_turn_mean", "conversation_only")
# 평가 단위는 이 조합만 구현돼 있다: 단일턴은 turn 행, 다중턴은 conversation 행.
SUPPORTED_EVALUATION_UNIT = {"single": "turn", "multi": "conversation"}
SUPPORTED_CI_METHODS = ("wilson_95",)


class RulesError(Exception):
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
        """이 판정 행에 치명적 실패가 있는가. NONE(none_token)과 빈칸은 '없음'이다.

        '치명적 실패가 있는가'를 판단하는 곳은 모두 이 함수를 거친다(판정 검증, first_cfc_turn, CFR, 모의 판정기).
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
    if spec.enum:
        missing = [v for v in [*raw["judgment"]["registered_cfc_codes"], spec.none_token] if v not in spec.enum]
        if missing:
            raise RulesError(f"코드북 06 critical_failure_code 허용값에 없는 값 {missing} (등록 CFC 코드·NONE은 허용값에 있어야 함)")
    return spec.none_token


def _check_against_codebook(codebook, raw):
    """규칙 파일이 쓰는 필드 이름·허용값이 코드북에 있는지 확인한다."""
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


def load_rules(codebook, rules_yaml=paths.AGGREGATION_RULES_YAML, judges_yaml=paths.JUDGES_YAML):
    data = rules_yaml.read_bytes()
    raw = yaml.safe_load(data)
    _check_against_codebook(codebook, raw)
    none_token = _check_cfc_tokens(codebook, raw)
    with open(judges_yaml, encoding="utf-8") as f:
        registry = yaml.safe_load(f)
    judges = {judge_id: JudgeEntry(judge_id=judge_id, **entry) for judge_id, entry in registry["judges"].items()}
    return Rules(raw=raw, sha256=hashlib.sha256(data).hexdigest(), judges=judges,
                 self_identification_patterns=tuple(registry.get("self_identification_patterns", ())),
                 none_token=none_token)
