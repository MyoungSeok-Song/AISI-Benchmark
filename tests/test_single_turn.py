import json

from conftest import mock_info

from kyab.models.mock import MockModelClient
from kyab.runner.conversation import run_item
from kyab.schemas import RESPONSES, RUNS
from kyab.storage import read_table
from kyab.validator import sha256_text


def test_single_turn_sends_one_user_message_and_records_one_response(small_dataset, out, ctx):
    item = small_dataset.executable_items()[0]
    client = MockModelClient(mock_info())
    run = run_item(item, 1, client, ctx, out["ids"], out["runs"], out["responses"])

    assert len(client.calls) == 1
    assert [m["role"] for m in client.calls[0]] == ["system", "user"]
    assert client.calls[0][1]["content"] == "single question"

    runs = read_table(out["dir"] / "runs.csv", RUNS)
    resps = read_table(out["dir"] / "responses.csv", RESPONSES)
    assert len(runs) == 1 and len(resps) == 1
    r = runs[0]
    assert (r["run_status"], r["stop_reason"], r["actual_turn_count"]) == ("completed", "planned_end", "1")
    assert (r["first_fail_turn"], r["first_cfc_turn"]) == ("", "")  # judged later, never guessed
    assert r["system_prompt_hash"] == sha256_text("SYSTEM PROMPT")
    assert (r["temperature"], r["top_p"], r["max_output_tokens"]) == ("0.0", "1.0", "1024")
    assert (r["model_id"], r["provider"], r["model_version"]) == ("mock", "mock", "mock-0.1")
    assert resps[0]["run_id"] == run["run_id"] and resps[0]["turn_id"] == "TURN-00000001"
    assert json.loads(resps[0]["request_messages_json"]) == client.calls[0]
    assert json.loads(resps[0]["raw_response_json"])["mock"] is True
    assert resps[0]["response_status"] == "success"


def test_context_text_is_rendered_into_user_message(tmp_path, out, ctx):
    from conftest import make_item, make_prompts
    from kyab.loader import Dataset

    ds = Dataset(tmp_path, {}, [make_item("KYAB-000009", 1)],
                 make_prompts("KYAB-000009", ["the question"], 1, contexts=["Setting: a middle-school student"]),
                 None)
    client = MockModelClient(mock_info())
    run_item(ds.executable_items()[0], 1, client, ctx, out["ids"], out["runs"], out["responses"])
    assert client.calls[0][1]["content"] == "Setting: a middle-school student\n\nthe question"
