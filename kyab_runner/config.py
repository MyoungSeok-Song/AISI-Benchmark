"""설정 파일(config/runner.yaml, config/models.yaml) 로드."""
import hashlib
from dataclasses import dataclass, field

import yaml

from . import paths


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
    with open(config_dir / "runner.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    with open(config_dir / "models.yaml", encoding="utf-8") as f:
        models_raw = yaml.safe_load(f)["models"]

    # 파일 끝 줄바꿈만 떼고 나머지는 그대로 쓴다. 해시는 실제로 보낸 문자열 기준.
    prompt = (config_dir / raw["system_prompt_file"]).read_text(encoding="utf-8").rstrip("\n")
    models = {model_id: ModelEntry(model_id=model_id, **entry)
              for model_id, entry in models_raw.items()}
    return RunnerConfig(
        raw=raw,
        system_prompt_text=prompt,
        system_prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        models=models,
    )
