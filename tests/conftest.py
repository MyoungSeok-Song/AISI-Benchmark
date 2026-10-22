import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kyab.adapters.common import blank_row, item_row, unreviewed_tag_row  # noqa: E402
from kyab.ids import IdRegistry  # noqa: E402
from kyab.loader import Dataset  # noqa: E402
from kyab.models.base import GenerationConfig, ModelInfo  # noqa: E402
from kyab.runner.conversation import RetryPolicy, RunContext  # noqa: E402
from kyab.schemas import PROMPTS, RESPONSES, RUNS  # noqa: E402
from kyab.storage import TableWriter  # noqa: E402

DV = "0.0.1"
GEN = GenerationConfig(0.0, 1.0, 1024)
# Evaluation metadata that must never reach the target model.
SENTINELS = {
    "risk_cues_json": '["SENTINEL_RISK_CUE"]',
    "prohibited_response_json": '["SENTINEL_PROHIBITED"]',
    "rubric_id": "SENTINEL_RUBRIC",
    "source_category": "SENTINEL_SOURCE_CATEGORY",
    "original_text": "SENTINEL_ORIGINAL_TEXT",
}


def make_item(item_id: str, turns: int, protocol: str | None = None, **over) -> dict:
    protocol = protocol or ("ST1-1.0.0" if turns == 1 else f"MT{turns}-1.0.0")
    base = dict(item_id=item_id, item_version="0.1.0", dataset_version=DV, scenario_type="not_specified",
                persona_text="not_specified", target_age_group="mid_13_15", user_gender="unspecified",
                conversation_mode="single" if turns == 1 else "multi", planned_round_count=turns,
                protocol_id=protocol, source_benchmark="NEW", source_language="en", localization_type="new",
                source_license="LicenseRef-test", item_review_status="pending", **SENTINELS)
    base.update(over)
    return item_row(**base)


def make_prompts(item_id: str, texts: list[str], start: int, contexts: list[str] | None = None) -> list[dict]:
    rows = []
    for i, t in enumerate(texts, 1):
        r = blank_row(PROMPTS)
        r.update(turn_id=f"TURN-{start + i - 1:08d}", item_id=item_id, item_version="0.1.0", turn_index=str(i),
                 context_text=(contexts or [""] * len(texts))[i - 1], message_text=t,
                 turn_expected_safe_response_json='["SENTINEL_TURN_EXPECTED"]')
        rows.append(r)
    return rows


def make_tag(item_id: str, rev: int = 1, status: str = "current") -> dict:
    t = unreviewed_tag_row(item_id, "0.1.0", "0.7.0", "2026-09-30T00:00:00+09:00")
    t.update(tag_revision=str(rev), tag_status=status, severity_tag="high", primary_risk="R5",
             expected_response_tags='["SENTINEL_EXPECTED_TAG"]')
    return t


@pytest.fixture
def small_dataset(tmp_path) -> Dataset:
    items = [make_item("KYAB-000001", 1), make_item("KYAB-000002", 3)]
    prompts = make_prompts("KYAB-000001", ["single question"], 1) + \
        make_prompts("KYAB-000002", ["turn one", "turn two", "turn three"], 2)
    tags = [make_tag("KYAB-000001"), make_tag("KYAB-000002")]
    return Dataset(tmp_path, {"dataset_version": DV}, items, prompts, tags)


@pytest.fixture
def out(tmp_path):
    d = tmp_path / "out"
    return dict(ids=IdRegistry(d / "id_registry.json"), runs=TableWriter(d / "runs.csv", RUNS),
                responses=TableWriter(d / "responses.csv", RESPONSES), dir=d)


@pytest.fixture
def ctx() -> RunContext:
    return RunContext("RBATCH-20260930-001", DV, "SYSTEM PROMPT", GEN, "service_default", "none", "test",
                      RetryPolicy(sleep=lambda s: None))


def mock_info(model_id="mock") -> ModelInfo:
    return ModelInfo(model_id, "mock", "mock-0.1", "2026-09-30", "none")
