import json

from conftest import make_item, make_prompts, make_tag, mock_info

from kyab.export_jsonl import build_items, build_responses, read_jsonl, write_jsonl
from kyab.models.mock import MockModelClient
from kyab.runner.conversation import run_item
from kyab.loader import Dataset
from kyab.schemas import ITEM_TAGS, ITEMS, PROMPTS
from kyab.storage import write_table


def write_dataset(d):
    items = [make_item("KYAB-000001", 1), make_item("KYAB-000002", 3)]
    prompts = make_prompts("KYAB-000002", ["t1", "t2", "t3"], 10)[::-1] + make_prompts("KYAB-000001", ["only"], 1)
    tags = [make_tag("KYAB-000001", 1, "superseded"), make_tag("KYAB-000001", 2, "current"),
            make_tag("KYAB-000002")]
    write_table(d / "items.csv", ITEMS, items)
    write_table(d / "prompts.csv", PROMPTS, prompts)
    write_table(d / "item_tags.csv", ITEM_TAGS, tags)
    return items, prompts


def test_items_jsonl_single_and_multi_share_structure(tmp_path):
    write_dataset(tmp_path)
    items = build_items(tmp_path, {"NEW": {"origin": "test"}})
    assert [i["item_id"] for i in items] == ["KYAB-000001", "KYAB-000002"]
    single, multi = items
    assert set(single) == set(multi)  # same fields regardless of conversation form
    assert [m["message_text"] for m in multi["messages"]] == ["t1", "t2", "t3"]  # turn_index order
    assert all(m["role"] == "user" and "item_id" not in m for m in multi["messages"])
    assert multi["planned_round_count"] == 3 and isinstance(multi["risk_cues_json"], list)
    assert single["parent_item_id"] is None  # blank → null
    assert single["tags"]["tag_revision"] == 2  # current tag revision only
    assert single["provenance"] == {"origin": "test"}


def test_responses_jsonl_nests_turns_and_round_trips(tmp_path, out, ctx):
    items, prompts = write_dataset(tmp_path)
    ds = Dataset(tmp_path, {}, items, prompts, None)
    for it in ds.executable_items():
        run_item(it, 1, MockModelClient(mock_info()), ctx, out["ids"], out["runs"], out["responses"])
    runs = build_responses(out["dir"])
    assert len(runs) == 2 and [len(r["turns"]) for r in runs] == [1, 3]
    t = runs[1]["turns"][2]
    assert isinstance(t["request_messages_json"], list) and len(t["request_messages_json"]) == 6
    assert isinstance(t["raw_response_json"], dict) and runs[1]["rollout_no"] == 1
    assert runs[0]["temperature"] == 0.0 and runs[0]["first_fail_turn"] is None
    path = tmp_path / "r.jsonl"
    write_jsonl(path, runs)
    assert read_jsonl(path) == json.loads(json.dumps(runs))
