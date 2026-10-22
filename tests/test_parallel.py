"""Parallel runner: same evaluation as sequential, safe IDs / files, resume without duplicates."""
import json
import threading

import yaml
from conftest import ROOT

import kyab.run_benchmark as rb
from kyab.ids import IdRegistry
from kyab.run_benchmark import main
from kyab.schemas import PROMPTS, RESPONSES, RUNS
from kyab.storage import TableWriter, read_table

FAST_RETRY = {"max_retries": 1, "backoff_s": 0.0, "max_backoff_s": 0.0}


def cfg(tmp_path, name="cfg", **over):
    c = yaml.safe_load((ROOT / "config" / "pilot_persona_qwen.yaml").read_text(encoding="utf-8"))
    c.update(output_dir=str(tmp_path / f"out_{name}"), id_registry=str(tmp_path / "ids.json"), rollouts=2,
             retry=FAST_RETRY)
    c.update(over)
    p = tmp_path / f"{name}.yaml"
    p.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    return str(p)


def outputs(tmp_path, name="cfg"):
    d = tmp_path / f"out_{name}"
    return (read_table(d / "runs.csv", RUNS, allow_extra=False),
            read_table(d / "responses.csv", RESPONSES, allow_extra=False))


def semantic(tmp_path, name):
    """Everything that defines the evaluation, without ids, timestamps or row order."""
    runs, resps = outputs(tmp_path, name)
    by_run = {r["run_id"]: r for r in runs}
    run_set = {(r["item_id"], r["persona_id"], r["rollout_no"], r["run_status"], r["actual_turn_count"],
                r["stop_reason"]) for r in runs}
    resp_set = {(by_run[x["run_id"]]["item_id"], by_run[x["run_id"]]["persona_id"], by_run[x["run_id"]]["rollout_no"],
                 x["turn_id"], x["response_status"], x["response_text"], x["request_messages_json"],
                 x["error_code"]) for x in resps}
    assert len(run_set) == len(runs) and len(resp_set) == len(resps)  # no duplicate (item, persona, rollout[, turn])
    return run_set, resp_set


def test_concurrency_does_not_change_the_evaluation(tmp_path):
    for model in ("mock", "mock_faults"):  # mock_faults: KYAB-900012 turn 2 times out → partial runs
        assert main(["--config", cfg(tmp_path, f"seq_{model}", concurrency=1), "--model", model]) in (0, 1)
        assert main(["--config", cfg(tmp_path, f"par_{model}"), "--model", model, "--concurrency", "8"]) in (0, 1)
        seq, par = semantic(tmp_path, f"seq_{model}"), semantic(tmp_path, f"par_{model}")
        assert seq == par
        assert len(seq[0]) == 12 * 6 * 2
    statuses = {s for (_, _, _, s, _, _) in semantic(tmp_path, "par_mock_faults")[0]}
    assert statuses == {"completed", "partial"}


def test_parallel_ids_and_rows_are_unique_and_intact(tmp_path):
    assert main(["--config", cfg(tmp_path), "--model", "mock", "--concurrency", "8"]) == 0
    runs, resps = outputs(tmp_path)
    assert len({r["run_id"] for r in runs}) == len(runs) == 144
    assert len({x["response_id"] for x in resps}) == len(resps) == 16 * 6 * 2
    turn_index = {p["turn_id"]: int(p["turn_index"])
                  for p in read_table(ROOT / "data" / "pilot_0.0.2" / "prompts.csv", PROMPTS)}
    for x in resps:  # each request carries exactly its own run's history (no cross-run mixing)
        msgs = json.loads(x["request_messages_json"])
        assert len(msgs) == 2 * turn_index[x["turn_id"]]


def test_id_registry_and_table_writer_are_thread_safe(tmp_path):
    reg = IdRegistry(tmp_path / "ids.json")
    w = TableWriter(tmp_path / "responses.csv", RESPONSES)
    got: list[str] = []

    def worker(k):
        for i in range(60):
            rid = reg.new_response_id()
            got.append(rid)
            w.append({"response_id": rid, "run_id": f"RUN-20261021-{k:06d}", "turn_id": "TURN-00000001",
                      "request_messages_json": "[]", "response_text": f"line1\nline2 \"{k}-{i}\", ok" * 20,
                      "raw_response_json": "{}", "response_status": "success"})

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    rows = read_table(tmp_path / "responses.csv", RESPONSES, allow_extra=False)
    assert len(got) == len(set(got)) == len(rows) == 480
    assert all(r["response_text"].count("line1\nline2") == 20 for r in rows)


def test_rerun_skips_completed_runs(tmp_path):
    c = cfg(tmp_path)
    assert main(["--config", c, "--model", "mock", "--concurrency", "4"]) == 0
    assert main(["--config", c, "--model", "mock", "--concurrency", "4"]) == 0
    runs, _ = outputs(tmp_path)
    assert len(runs) == 144  # nothing created twice
    assert main(["--config", c, "--model", "mock", "--rollouts", "1", "--rollout-start", "3"]) == 0
    assert len(outputs(tmp_path)[0]) == 144 + 72  # only the new rollout ran


def test_unfinished_runs_resume_only_with_retry_failed(tmp_path):
    c = cfg(tmp_path)
    assert main(["--config", c, "--model", "mock_faults", "--concurrency", "4"]) == 0
    runs, _ = outputs(tmp_path)
    partial = [r for r in runs if r["run_status"] != "completed"]
    assert len(partial) == 12 and {r["item_id"] for r in partial} == {"KYAB-900012"}
    assert main(["--config", c, "--model", "mock_faults"]) == 0
    assert len(outputs(tmp_path)[0]) == 144  # skipped, not duplicated
    assert main(["--config", c, "--model", "mock_faults", "--retry-failed", "--concurrency", "4"]) == 0
    runs2, _ = outputs(tmp_path)
    assert len(runs2) == 144 + 12  # only the unfinished keys ran again; the old rows stay as audit records
    assert {r["run_id"] for r in partial} <= {r["run_id"] for r in runs2}


def test_changed_generation_settings_are_refused_in_the_same_output(tmp_path):
    assert main(["--config", cfg(tmp_path), "--model", "mock"]) == 0
    assert main(["--config", cfg(tmp_path, temperature=0.7), "--model", "mock"]) == 2
    assert len(outputs(tmp_path)[0]) == 144


def test_one_broken_worker_does_not_stop_the_others(tmp_path, monkeypatch):
    real = rb.run_item

    def flaky(item, *a, **k):
        if item.item_id == "KYAB-900003" and item.persona_id == "PERSONA-MID-F":
            raise RuntimeError("disk full (simulated)")
        return real(item, *a, **k)

    monkeypatch.setattr(rb, "run_item", flaky)
    c = cfg(tmp_path)
    assert main(["--config", c, "--model", "mock", "--concurrency", "4"]) == 1
    assert len(outputs(tmp_path)[0]) == 144 - 2
    monkeypatch.setattr(rb, "run_item", real)
    assert main(["--config", c, "--model", "mock", "--concurrency", "4"]) == 0
    runs, _ = outputs(tmp_path)
    assert len(runs) == 144 and {r["run_status"] for r in runs} == {"completed"}
