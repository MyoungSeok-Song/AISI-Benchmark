"""scripts/estimate_cost.py scales measured tokens per call position to the full plan."""
import importlib.util

from conftest import ROOT

from kyab.call_metrics import CALL_METRICS
from kyab.schemas import RESPONSES, RUNS
from kyab.storage import write_table


def test_estimate_scales_measured_tokens(tmp_path, capsys):
    d = tmp_path / "smoke"
    runs = [{"run_id": "RUN-20261022-000001", "protocol_id": "ST1-1.0.0"},
            {"run_id": "RUN-20261022-000002", "protocol_id": "MT3-1.0.0"}]
    resp, met = [], []
    for n, (rid, tid, tin, tout, tr) in enumerate([
            ("RUN-20261022-000001", "TURN-00000001", 100, 400, 200),
            ("RUN-20261022-000002", "TURN-00000002", 100, 300, 0),
            ("RUN-20261022-000002", "TURN-00000003", 500, 300, 0),
            ("RUN-20261022-000002", "TURN-00000004", 900, 300, 0)], 1):
        rid_ = f"RESP-{n:08d}"
        resp.append({"response_id": rid_, "run_id": rid, "turn_id": tid})
        met.append({"response_id": rid_, "run_id": rid, "turn_id": tid, "model_id": "gpt_5_mini",
                    "input_tokens": tin, "output_tokens": tout, "reasoning_tokens": tr, "response_status": "success"})
    write_table(d / "runs.csv", RUNS, runs)
    write_table(d / "responses.csv", RESPONSES, resp)
    write_table(d / "call_metrics.csv", CALL_METRICS, met)
    spec = importlib.util.spec_from_file_location("ec", ROOT / "scripts" / "estimate_cost.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--smoke", str(d), "--single", "10", "--multi", "10", "--personas", "1", "--rollouts", "1",
                     "--full-runs", "1", "--retry", "0"]) == 0
    out = capsys.readouterr().out
    # input: 10×100 + 10×(100+500+900) = 16,000; output: 10×400 + 10×900 = 13,000 → $0.004 + $0.026 = $0.03
    assert "| gpt_5_mini | 4 | 0.02M | 0.01M |" in out and "$0.0" in out
