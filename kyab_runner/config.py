"""설정 파일(config/runner.yaml, config/models.yaml) 로드.

로드할 때 모델 옵션의 extra_body·extra_generation_config에 호출 파라미터 키가 들어 있으면 거부한다.
그 키는 러너가 run_params(코드북 04 고정값)에서 보내는 것이라, 옵션에 두면 공급자에 보내는 값이 04 기록과 어긋난다.
"""
import hashlib
from dataclasses import MISSING, dataclass, field, fields
from zoneinfo import ZoneInfo

from . import paths
from .errors import SetupError, load_yaml


class ConfigError(SetupError):
    """설정 파일이 러너 규칙에 어긋날 때."""


# 구현된 context_text 위치. runner.yaml context_position은 이 중 하나여야 한다(여기서 검사, messages가 적용).
# 공용 기반인 config가 실행 계층의 messages를 import하지 않도록 상수는 여기 둔다(R15).
CONTEXT_POSITIONS = ("user_prefix",)


# 모델 옵션(extra_body·extra_generation_config)에 둘 수 없는 키: 러너가 run_params에서 보내는 호출 파라미터와
# 그 밖의 샘플링·길이 설정(코드북 04에 칸이 없어 기록되지 않는 조건을 만들지 않기 위해),
# 그리고 Gemini 요청의 generationConfig 블록 자체(통째로 넣으면 러너가 만든 블록을 덮어쓴다 — 추가 설정은 extra_generation_config로만)
# 추론·사고 설정(reasoning_effort·thinking·thinkingConfig)도 금지한다: 04에 기록되지 않는 실행 조건이 생기기 때문이다.
# C7(추론 설정의 결정과 기록 경로)이 정해진 뒤 기록 경로와 함께 허용한다.
PARAM_KEYS_FORBIDDEN_IN_OPTIONS = ("max_tokens", "max_completion_tokens", "max_output_tokens", "maxOutputTokens", "max_new_tokens",
                                   "temperature", "top_p", "topP", "top_k", "topK", "min_p", "repetition_penalty",
                                   "presence_penalty", "presencePenalty", "frequency_penalty", "frequencyPenalty", "seed",
                                   "min_tokens", "candidateCount", "stop", "stopSequences", "stop_sequences", "ignore_eos",
                                   "stop_token_ids", "logit_bias", "sampling_params",
                                   "generationConfig", "generation_config",
                                   "reasoning_effort", "thinking", "thinkingConfig")
# 요청 구조 키: 러너가 직접 만드는 부분(모델·메시지·시스템 프롬프트·도구·저장·스트리밍·후보 수·안전 설정).
# 어댑터는 옵션 블록을 요청 본문에 마지막으로 합치므로, 여기 있는 키를 옵션에 두면 러너가 만든 값을 덮어쓴다 —
# 그러면 04 safety_profile·tool_profile, 05 request_messages_json, describe()의 conversation_storage(off)가 사실과 달라진다.
REQUEST_KEYS_FORBIDDEN_IN_OPTIONS = ("model", "messages", "contents", "system", "systemInstruction", "store", "stream", "stream_options",
                                     "n", "safetySettings", "tools", "tool_choice", "toolConfig", "functions", "function_call")
_OPTION_BLOCKS = ("extra_body", "extra_generation_config")


def _check_options(model_id, options):
    """어댑터 추가 설정이 호출 파라미터·요청 구조를 덮어쓰지 않는지. omit_params로 출력 한도를 빼는 것도 막는다."""
    for block in _OPTION_BLOCKS:
        keys = set(options.get(block) or {})
        forbidden = sorted(keys & set(PARAM_KEYS_FORBIDDEN_IN_OPTIONS))
        if forbidden:
            raise ConfigError(f"models.yaml {model_id}.options.{block}에 호출 파라미터 키 {forbidden}가 있습니다. "
                              "temperature·top_p·출력 한도는 runner.yaml run_params에서만 정합니다(04_runs 기록과 일치해야 함)")
        structural = sorted(keys & set(REQUEST_KEYS_FORBIDDEN_IN_OPTIONS))
        if structural:
            raise ConfigError(f"models.yaml {model_id}.options.{block}에 요청 구조 키 {structural}가 있습니다. "
                              "모델·메시지·도구·저장(store)·안전 설정은 러너가 만들며 옵션으로 덮어쓸 수 없습니다"
                              "(04 safety_profile·tool_profile, 05 request_messages_json과 일치해야 함)")
    if "max_output_tokens" in (options.get("omit_params") or []):
        raise ConfigError(f"models.yaml {model_id}.options.omit_params에 max_output_tokens를 넣을 수 없습니다. "
                          "출력 한도는 항상 보냅니다(빼면 한도 없이 전송되거나 요청이 깨짐)")


# runner.yaml에서 러너가 읽는 키와 값 종류. 실행 뒤(모델 호출 뒤)에야 KeyError로 드러나지 않도록 로드 때 한 번에 검사한다.
# run_params는 cli.check_run_params가 코드북 04와 대조하므로 여기 두지 않는다. vllm_venv·hf_home·vllm_env는 도구만 읽는 선택 키.
_RUNNER_KEYS = {
    # 기록 환경
    "timezone": str, "system_prompt_file": str,
    # 04에 적는 등록 설정명과 그 등록 목록
    "safety_profile": str, "tool_profile": str, "registered_safety_profiles": list, "registered_tool_profiles": list,
    # 실행 방식
    "protocols": dict, "default_rollouts": int, "max_attempts_per_turn": int, "context_reserve_tokens": int,
    "context_position": str, "context_separator": str,
    # 입력 선정·검증
    "eligible_item_review_status": list, "eligible_lifecycle_status": list, "registered_rubric_ids": list,
    # 05 finish_reason 정규화 어휘
    "finish_reasons": list,
}


def _check_runner(raw):
    """runner.yaml의 필수 키·값 종류·범위. 어긋나면 ConfigError(키 이름 포함)."""
    if not isinstance(raw, dict):
        raise ConfigError("runner.yaml: 매핑(키: 값)이어야 합니다")
    for key, kind in _RUNNER_KEYS.items():
        if key not in raw:
            raise ConfigError(f"runner.yaml: 필수 키 {key!r}가 없습니다")
        value = raw[key]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise ConfigError(f"runner.yaml {key}: {kind.__name__}이어야 합니다 (현재 {value!r})")
    for key in ("default_rollouts", "max_attempts_per_turn"):
        if raw[key] < 1:
            raise ConfigError(f"runner.yaml {key}: 1 이상이어야 합니다 (현재 {raw[key]})")
    if raw["context_position"] not in CONTEXT_POSITIONS:
        raise ConfigError(f"runner.yaml context_position={raw['context_position']!r}: 지원 {CONTEXT_POSITIONS}")
    for key, registry in (("safety_profile", "registered_safety_profiles"), ("tool_profile", "registered_tool_profiles")):
        if raw[key] not in raw[registry]:
            raise ConfigError(f"runner.yaml {key}={raw[key]!r}가 {registry}에 없습니다")
    try:
        ZoneInfo(raw["timezone"])
    except Exception as exc:                          # ZoneInfoNotFoundError·ValueError 등
        raise ConfigError(f"runner.yaml timezone={raw['timezone']!r}: 시간대를 찾을 수 없습니다 ({exc})") from exc
    if not isinstance(raw.get("control_link_required", False), bool):
        raise ConfigError("runner.yaml control_link_required는 true/false여야 합니다")


def build_entry(cls, key_name, key, entry, source, error_cls):
    """등록부(models.yaml·judges.yaml) 항목 1개 -> dataclass. 키가 빠지거나 모르면 TypeError 대신 error_cls(항목 이름 포함).

    bool 필드에 문자열 'false' 같은 값이 오면 참으로 취급되는 사고를 막기 위해 bool 필드의 종류도 본다.
    """
    if not isinstance(entry, dict):
        raise error_cls(f"{source} {key}: 매핑(키: 값)이어야 합니다 (현재 {entry!r})")
    spec = {f.name: f for f in fields(cls) if f.name != key_name}
    unknown = sorted(set(entry) - set(spec))
    missing = sorted(name for name, f in spec.items() if name not in entry and f.default is MISSING and f.default_factory is MISSING)
    if unknown or missing:
        raise error_cls(f"{source} {key}: 모르는 키 {unknown} / 빠진 키 {missing}")
    for name, f in spec.items():
        if f.type is bool and name in entry and not isinstance(entry[name], bool):
            raise error_cls(f"{source} {key}.{name}: true/false여야 합니다 (현재 {entry[name]!r} — 따옴표를 빼세요)")
    return cls(**{key_name: key}, **entry)


@dataclass(frozen=True)
class ModelEntry:
    """models.yaml의 모델 1개. 04_runs의 모델 관련 5개 필드의 원천."""
    model_id: str
    adapter: str
    provider: str
    model_version: str
    model_snapshot_date: str
    api_version: str
    enabled: bool
    options: dict = field(default_factory=dict)   # 어댑터별 설정 (접속 주소, 추가 요청 본문 등)


@dataclass(frozen=True)
class RunnerConfig:
    """runner.yaml 전체 + 읽어 둔 시스템 프롬프트."""
    raw: dict                   # runner.yaml 원본 dict
    system_prompt_text: str     # 모든 실행에 그대로 보내는 문자열
    system_prompt_hash: str     # 위 문자열의 SHA-256 (소문자 64자)
    models: dict                # {model_id: ModelEntry}

    def __getitem__(self, key):
        return self.raw[key]

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def protocol(self, protocol_id):
        """프로토콜 정의 {conversation_mode, planned_round_count}. 없으면 KeyError."""
        return self.raw["protocols"][protocol_id]


def load_config(config_dir=paths.CONFIG_DIR):
    """runner.yaml·models.yaml·시스템 프롬프트 -> RunnerConfig. config_dir는 시험용 치환 자리다."""
    raw = load_yaml(config_dir / paths.RUNNER_YAML.name, ConfigError)
    _check_runner(raw)
    models_raw = load_yaml(config_dir / paths.MODELS_YAML.name, ConfigError)
    if not isinstance(models_raw, dict) or not isinstance(models_raw.get("models"), dict):
        raise ConfigError("models.yaml: 최상위 models 매핑이 없습니다")

    # 파일 끝 줄바꿈만 떼고 나머지는 그대로 쓴다. 해시는 실제로 보낸 문자열 기준.
    try:
        prompt = (config_dir / raw["system_prompt_file"]).read_text(encoding="utf-8").rstrip("\n")
    except FileNotFoundError as exc:
        raise ConfigError(f"runner.yaml system_prompt_file: 파일이 없습니다 ({exc.filename})") from exc
    models = {model_id: build_entry(ModelEntry, "model_id", model_id, entry, "models.yaml", ConfigError)
              for model_id, entry in models_raw["models"].items()}
    for model_id, entry in models.items():
        _check_options(model_id, entry.options)
    return RunnerConfig(
        raw=raw,
        system_prompt_text=prompt,
        system_prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        models=models,
    )
