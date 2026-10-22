import json

from kyab.ids import IdRegistry
from kyab.schemas import RESPONSES, RUNS
from kyab.storage import TableWriter, read_table


def test_roundtrip_preserves_json_multiline_and_korean(tmp_path):
    w = TableWriter(tmp_path / "responses.csv", RESPONSES)
    msgs = [{"role": "user", "content": "요즘 친구들이랑 연락 끊고 싶어...\n\"진짜로\", 쉼표도"}]
    row = {"response_id": "RESP-00000001", "run_id": "RUN-20260930-000001", "turn_id": "TURN-00000001",
           "request_messages_json": json.dumps(msgs, ensure_ascii=False), "response_text": "줄1\n줄2, \"인용\"",
           "raw_response_json": "{}", "response_status": "success"}
    w.append(row)
    back = read_table(tmp_path / "responses.csv", RESPONSES, allow_extra=False)[0]
    assert json.loads(back["request_messages_json"]) == msgs
    assert back["response_text"] == row["response_text"]
    assert back["block_source"] == ""


def test_header_is_exact_codebook_order(tmp_path):
    TableWriter(tmp_path / "runs.csv", RUNS)
    assert (tmp_path / "runs.csv").read_text(encoding="utf-8").strip() == ",".join(RUNS.columns)


def test_upsert_replaces_by_primary_key(tmp_path):
    w = TableWriter(tmp_path / "runs.csv", RUNS)
    w.upsert({"run_id": "RUN-20260930-000001", "run_status": "running"})
    w.upsert({"run_id": "RUN-20260930-000002", "run_status": "running"})
    w.upsert({"run_id": "RUN-20260930-000001", "run_status": "completed"})
    rows = read_table(tmp_path / "runs.csv", RUNS)
    assert [(r["run_id"], r["run_status"]) for r in rows] == [
        ("RUN-20260930-000001", "completed"), ("RUN-20260930-000002", "running")]


def test_writer_refuses_file_with_different_header(tmp_path):
    p = tmp_path / "runs.csv"
    p.write_text("run_id,something_else\n", encoding="utf-8")
    try:
        TableWriter(p, RUNS)
    except ValueError as e:
        assert "헤더" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_ids_are_unique_across_restarts(tmp_path):
    a = IdRegistry(tmp_path / "ids.json")
    first = [a.new_run_id() for _ in range(3)] + [a.new_response_id() for _ in range(3)]
    b = IdRegistry(tmp_path / "ids.json")  # new process / restart
    second = [b.new_run_id() for _ in range(3)] + [b.new_response_id() for _ in range(3)]
    assert len(set(first + second)) == 12
    assert second[0].endswith("-000004") and second[3] == "RESP-00000004"
    batch = b.new_run_batch_id()
    assert batch.startswith("RBATCH-") and batch.endswith("-001")
