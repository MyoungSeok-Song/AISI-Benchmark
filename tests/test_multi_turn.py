import copy
import json

from conftest import SENTINELS, make_item, make_prompts, make_tag, mock_info

from kyab.loader import Dataset
from kyab.models.base import SUCCESS, GenerationResult
from kyab.models.mock import MockModelClient
from kyab.runner.conversation import run_item
from kyab.schemas import RESPONSES, RUNS
from kyab.storage import read_table


class ScriptedClient:
    """Replies with unusual text to prove the next user turn does not depend on the reply."""

    def __init__(self, replies):
        self.info = mock_info("scripted")
        self.replies = list(replies)
        self.calls = []

    def generate(self, messages, config):
        self.calls.append(copy.deepcopy(messages))
        text = self.replies.pop(0)
        return GenerationResult(SUCCESS, text, {"reply": text}, copy.deepcopy(messages), finish_reason="stop")


def test_three_turn_history_accumulates(small_dataset, out, ctx):
    item = small_dataset.executable_items()[1]
    client = MockModelClient(mock_info())
    run = run_item(item, 1, client, ctx, out["ids"], out["runs"], out["responses"])

    roles = [[m["role"] for m in c] for c in client.calls]
    assert roles == [["system", "user"],
                     ["system", "user", "assistant", "user"],
                     ["system", "user", "assistant", "user", "assistant", "user"]]
    resps = read_table(out["dir"] / "responses.csv", RESPONSES)
    assert [r["turn_id"] for r in resps] == ["TURN-00000002", "TURN-00000003", "TURN-00000004"]
    assert [len(json.loads(r["request_messages_json"])) for r in resps] == [2, 4, 6]
    # the assistant message in turn t+1's request is exactly the stored response of turn t
    turn3 = json.loads(resps[2]["request_messages_json"])
    assert turn3[2]["content"] == resps[0]["response_text"]
    assert turn3[4]["content"] == resps[1]["response_text"]
    assert run["actual_turn_count"] == 3 and run["run_status"] == "completed"
    assert len(read_table(out["dir"] / "runs.csv", RUNS)) == 1  # one run row for the whole conversation


def test_next_user_turn_is_fixed_whatever_the_model_says(small_dataset, out, ctx):
    item = small_dataset.executable_items()[1]
    client = ScriptedClient(["Let's change the topic entirely.", "I refuse to continue.", "ok"])
    run_item(item, 1, client, ctx, out["ids"], out["runs"], out["responses"])
    assert [c[-1]["content"] for c in client.calls] == ["turn one", "turn two", "turn three"]
    assert client.calls[2][2]["content"] == "Let's change the topic entirely."
    assert client.calls[2][4]["content"] == "I refuse to continue."


def test_turn_count_is_not_hardcoded(tmp_path, out, ctx):
    texts = [f"turn {i}" for i in range(1, 8)]
    ds = Dataset(tmp_path, {}, [make_item("KYAB-000007", 7)], make_prompts("KYAB-000007", texts, 100),
                 [make_tag("KYAB-000007")])
    client = MockModelClient(mock_info())
    run = run_item(ds.executable_items()[0], 1, client, ctx, out["ids"], out["runs"], out["responses"])
    assert run["actual_turn_count"] == 7
    assert [len(c) for c in client.calls] == [2, 4, 6, 8, 10, 12, 14]


def test_turns_run_in_turn_index_order_not_file_order(tmp_path, out, ctx):
    prompts = make_prompts("KYAB-000003", ["first", "second", "third"], 10)
    ds = Dataset(tmp_path, {}, [make_item("KYAB-000003", 3)], list(reversed(prompts)), None)
    client = MockModelClient(mock_info())
    run_item(ds.executable_items()[0], 1, client, ctx, out["ids"], out["runs"], out["responses"])
    assert [c[-1]["content"] for c in client.calls] == ["first", "second", "third"]


def test_evaluation_metadata_never_reaches_the_model(small_dataset, out, ctx):
    client = MockModelClient(mock_info())
    for it in small_dataset.executable_items():
        run_item(it, 1, client, ctx, out["ids"], out["runs"], out["responses"])
    sent = json.dumps(client.calls)
    for s in list(SENTINELS.values()) + ["SENTINEL_TURN_EXPECTED", "SENTINEL_EXPECTED_TAG", "R5", "high"]:
        assert s.strip('[]"') not in sent, s
