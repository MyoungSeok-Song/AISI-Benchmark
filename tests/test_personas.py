"""Persona axis × repeated rollouts through the CLI (mock model)."""
import json

import yaml
from conftest import ROOT

from kyab.run_benchmark import main
from kyab.schemas import PERSONA_PROMPTS, PERSONAS, PROMPTS, RESPONSES, RUNS
from kyab.storage import read_table, write_table

PERSONA_DIR = ROOT / "data" / "personas" / "placeholder_0.0.1"
N_ITEMS, N_TURNS = 12, 10 * 1 + 2 * 3  # pilot_0.0.2


def cfg(tmp_path, **over):
    c = yaml.safe_load((ROOT / "config" / "pilot_persona_qwen.yaml").read_text(encoding="utf-8"))
    c.update(output_dir=str(tmp_path / "out"), id_registry=str(tmp_path / "ids.json"))
    c.update(over)
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    return str(p)


def persona_dir_with_rewrites(tmp_path, drop: int = 0, style_note: str | None = None):
    d = tmp_path / "personas"
    personas = read_table(PERSONA_DIR / "personas.csv", PERSONAS)
    if style_note:
        for p in personas:
            p["style_note"] = style_note
    write_table(d / "personas.csv", PERSONAS, personas)
    turns = read_table(ROOT / "data" / "pilot_0.0.2" / "prompts.csv", PROMPTS)
    rows = [{"persona_id": p["persona_id"], "turn_id": t["turn_id"],
             "message_text": f"[{p['persona_id']}] {t['message_text']}"} for p in personas for t in turns]
    write_table(d / "persona_prompts.csv", PERSONA_PROMPTS, rows[drop:])
    return d


def out_rows(tmp_path):
    return (read_table(tmp_path / "out" / "runs.csv", RUNS, allow_extra=False),
            read_table(tmp_path / "out" / "responses.csv", RESPONSES, allow_extra=False))


def test_items_x_personas_x_rollouts_with_base_fallback(tmp_path):
    assert main(["--config", cfg(tmp_path, rollouts=2), "--model", "mock"]) == 0
    runs, resps = out_rows(tmp_path)
    assert len(runs) == N_ITEMS * 6 * 2 and len(resps) == N_TURNS * 6 * 2
    assert {r["temperature"] for r in runs} == {"1.0"}
    assert {r["persona_id"] for r in runs} == {p["persona_id"] for p in read_table(PERSONA_DIR / "personas.csv",
                                                                                     PERSONAS)}
    assert {r["rollout_no"] for r in runs} == {"1", "2"}
    batch = yaml.safe_load(next((tmp_path / "out" / "batches").glob("*.yaml")).read_text(encoding="utf-8"))
    assert batch["personas"]["prompt_source"].startswith("base_fallback")
    assert batch["personas"]["persona_status"] == ["placeholder"]


def test_persona_rewrite_is_what_the_model_receives(tmp_path):
    d = persona_dir_with_rewrites(tmp_path, style_note="SENTINEL_STYLE_NOTE")
    assert main(["--config", cfg(tmp_path, rollouts=1, persona_dir=str(d), persona_prompt_fallback="none"),
                 "--model", "mock", "--personas", "PERSONA-MID-F"]) == 0
    runs, resps = out_rows(tmp_path)
    assert {r["persona_id"] for r in runs} == {"PERSONA-MID-F"}
    for r in resps:
        users = [m["content"] for m in json.loads(r["request_messages_json"]) if m["role"] == "user"]
        assert users and all(u.startswith("[PERSONA-MID-F] ") for u in users)
        assert "SENTINEL_STYLE_NOTE" not in r["request_messages_json"]
    batch = yaml.safe_load(next((tmp_path / "out" / "batches").glob("*.yaml")).read_text(encoding="utf-8"))
    assert batch["personas"]["prompt_source"] == "persona_prompts"


def test_missing_rewrites_block_unless_fallback(tmp_path):
    assert main(["--config", cfg(tmp_path, persona_prompt_fallback="none"), "--model", "mock"]) == 2
    d = persona_dir_with_rewrites(tmp_path, drop=1)
    assert main(["--config", cfg(tmp_path, persona_dir=str(d), persona_prompt_fallback="none"), "--model",
                 "mock"]) == 2
    assert not (tmp_path / "out").exists()


def test_rollout_start_appends_more_rollouts(tmp_path):
    c = cfg(tmp_path, rollouts=1)
    assert main(["--config", c, "--model", "mock", "--personas", "PERSONA-EARLY-M", "--items", "KYAB-900001"]) == 0
    assert main(["--config", c, "--model", "mock", "--personas", "PERSONA-EARLY-M", "--items", "KYAB-900001",
                 "--rollouts", "2", "--rollout-start", "2"]) == 0
    runs, _ = out_rows(tmp_path)
    assert sorted(int(r["rollout_no"]) for r in runs) == [1, 2, 3]


def test_no_persona_dir_records_none(tmp_path):
    c = yaml.safe_load(open(cfg(tmp_path), encoding="utf-8"))
    c.pop("persona_dir")
    p = tmp_path / "np.yaml"
    p.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    assert main(["--config", str(p), "--model", "mock", "--rollouts", "1"]) == 0
    runs, _ = out_rows(tmp_path)
    assert {r["persona_id"] for r in runs} == {"none"} and len(runs) == N_ITEMS


def test_incomplete_persona_grid_blocks(tmp_path):
    d = tmp_path / "five"
    rows = read_table(PERSONA_DIR / "personas.csv", PERSONAS)
    write_table(d / "personas.csv", PERSONAS, [r for r in rows if r["persona_id"] != "PERSONA-LATE-F"])
    assert main(["--config", cfg(tmp_path, persona_dir=str(d)), "--model", "mock"]) == 2
    assert not (tmp_path / "out").exists()
