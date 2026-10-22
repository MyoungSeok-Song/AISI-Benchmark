import openpyxl
import pytest
from conftest import ROOT

from kyab.schemas import (AI, CODEBOOK_V03_PENDING, CODEBOOK_V03_PROPOSED, ITEMS, RUNS, TABLES,
                          TARGET_MODEL_INPUT_FIELDS, check_value)
from kyab.storage import read_table, write_table

CODEBOOK = ROOT / "docs" / "codebook_v0.2.xlsx"


@pytest.mark.parametrize("name", list(TABLES))
def test_schema_matches_codebook_excel(name):
    """Field names, order, requirement class (col A) and AI-delivery mark (col B) equal the codebook,
    except for the agreed-but-not-yet-delivered v0.3 changes listed in CODEBOOK_V03_PENDING."""
    t = TABLES[name]
    ws = openpyxl.load_workbook(CODEBOOK, data_only=True)[t.sheet]
    rows = [r for r in ws.iter_rows(min_row=4, values_only=True) if r[3]]
    xl = [(r[3].strip(), r[0].strip(), r[1].strip()) for r in rows]
    ours = []
    for f in t.fields:
        if CODEBOOK_V03_PROPOSED.get((name, f.name), {}).get("added"):
            continue
        req = f.req
        pending = CODEBOOK_V03_PENDING.get((name, f.name))
        if pending and "req" in pending:
            assert pending["req"] is req, f"{f.name}: pending change not applied"
            req = next(x for x in type(req) if x.value == dict((a, b) for a, b, _ in xl)[f.name])
        ours.append((f.name, req.value, f.ai.value))
    assert xl == ours


def test_codebook_field_counts():
    assert {n: len(t.fields) for n, t in TABLES.items()} == {
        "items": 27, "item_tags": 29, "prompts": 7, "runs": 28, "responses": 11, "judgments": 29, "results": 35}


def test_only_message_context_and_run_settings_can_reach_target_model():
    assert TARGET_MODEL_INPUT_FIELDS == {
        "items.scenario_type", "items.persona_text", "items.target_age_group",  # △ only if written into prompts
        "prompts.context_text", "prompts.message_text",
        "runs.system_prompt_text", "runs.temperature", "runs.top_p", "runs.max_output_tokens",
        "runs.safety_profile", "runs.tool_profile",
        "persona_prompts.message_text"}
    assert all(f.ai is AI.NEVER for f in TABLES["item_tags"].fields)


@pytest.mark.parametrize("table,field,value,ok", [
    (ITEMS, "item_id", "KYAB-000001", True),
    (ITEMS, "item_id", "KYAB-1", False),
    (ITEMS, "item_version", "1.0.0", True),
    (ITEMS, "item_version", "1.0", False),
    (ITEMS, "planned_round_count", "3", True),
    (ITEMS, "planned_round_count", "4", False),
    (ITEMS, "protocol_id", "MT3-1.0.0", True),
    (ITEMS, "protocol_id", "MT3-v1", True),   # legacy, readable (new data is rejected by the validator)
    (ITEMS, "protocol_id", "MT3-2.0.0", False),
    (ITEMS, "target_age_group", "mid_13_15", True),
    (ITEMS, "target_age_group", "13-15", False),
    (ITEMS, "risk_cues_json", '["a", "b"]', True),
    (ITEMS, "risk_cues_json", '{"a": 1}', False),
    (ITEMS, "risk_cues_json", "not json", False),
    (RUNS, "temperature", "0.0", True),
    (RUNS, "temperature", "1.0", True),     # proposed v0.3: recorded setting, not fixed 0.0
    (RUNS, "temperature", "2.5", False),
    (RUNS, "rollout_no", "10", True),
    (RUNS, "rollout_no", "0", False),
    (RUNS, "persona_id", "none", True),
    (RUNS, "persona_id", "PERSONA-MID-F", True),
    (RUNS, "persona_id", "mid_f", False),
    (RUNS, "max_output_tokens", "2048", False),
    (RUNS, "max_output_tokens", "8192", True),    # 10/05 reply ①
    (RUNS, "max_output_tokens", "1024", True),    # v0.2 runs stay readable
    (RUNS, "run_id", "RUN-20260930-000001", True),
    (RUNS, "run_batch_id", "RBATCH-20260930-01", False),
    (RUNS, "system_prompt_hash", "A" * 64, False),
    (RUNS, "started_at", "2026-09-30T15:00:00+09:00", True),
    (RUNS, "model_snapshot_date", "2026/09/30", False),
])
def test_cell_rules(table, field, value, ok):
    assert (check_value(table.field(field), value) is None) == ok


def test_missing_codebook_column_is_rejected(tmp_path):
    p = tmp_path / "items.csv"
    p.write_text("item_id,item_version\nKYAB-000001,0.1.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="컬럼 누락"):
        read_table(p, ITEMS)


def test_unknown_extra_column_is_kept(tmp_path):
    p = tmp_path / "items.csv"
    write_table(p, ITEMS, [{"item_id": "KYAB-000001"}])
    text = p.read_text(encoding="utf-8").splitlines()
    p.write_text(text[0] + ",lab_note\n" + text[1] + ",keep me\n", encoding="utf-8")
    assert read_table(p, ITEMS)[0]["lab_note"] == "keep me"
