"""Build a ModelClient from config/models.yaml. Adding a model means adding YAML, not runner code."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from .base import ModelClient, ModelInfo
from .mock import MockModelClient
from .openai_compat import OpenAICompatibleClient


def load_models(path: str | Path) -> dict[str, dict]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {m["model_id"]: m for m in doc.get("models", [])}


def build_client(model_id: str, models_yaml: str | Path) -> ModelClient:
    models = load_models(models_yaml)
    if model_id not in models:
        raise KeyError(f"{model_id}가 {models_yaml}에 없음. 등록된 모델: {sorted(models)}")
    m = models[model_id]
    missing = [k for k in ("provider", "model_version", "api_version", "client") if not m.get(k)]
    if missing:
        raise ValueError(f"{model_id}: models.yaml 필수 항목 누락 {missing}")
    if str(m["model_version"]).upper() == "TBD":
        raise ValueError(f"{model_id}: model_version이 TBD — 실제 호출 전 공식 버전·체크포인트를 기록해야 함")
    snapshot = str(m.get("model_snapshot_date") or date.today().isoformat())
    info = ModelInfo(model_id=model_id, provider=m["provider"], model_version=str(m["model_version"]),
                     model_snapshot_date=snapshot, api_version=str(m["api_version"]),
                     extra={"allow_reasoning": bool(m.get("allow_reasoning", False))})
    if m["client"] == "mock":
        return MockModelClient(info, faults=m.get("faults"), only_items=m.get("fault_items"),
                               fault_repeats=m.get("fault_repeats"))
    if m["client"] == "openai_compatible":
        return OpenAICompatibleClient(info, base_url=m["base_url"], request_model=m["request_model"],
                                      api_key_env=m.get("api_key_env"), timeout_s=float(m.get("timeout_s", 120)),
                                      max_tokens_param=m.get("max_tokens_param", "max_tokens"),
                                      extra_body=m.get("extra_body"), omit_params=m.get("omit_params"))
    raise ValueError(f"{model_id}: 알 수 없는 client {m['client']!r}")
