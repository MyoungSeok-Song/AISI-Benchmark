"""End-to-end: the real pilot dataset through the CLI with the mock model."""
import yaml
from conftest import ROOT

from kyab.run_benchmark import main
from kyab.schemas import RESPONSES, RUNS
from kyab.storage import read_table


def cfg(tmp_path, **over):
    c = yaml.safe_load((ROOT / "config" / "pilot.yaml").read_text(encoding="utf-8"))
    c.update(output_dir=str(tmp_path / "out"), id_registry=str(tmp_path / "ids.json"))
    c.update(over)
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(c), encoding="utf-8")
    return str(p)


def cfg_isolated(tmp_path, **over):
    return cfg(tmp_path, id_registry=str(tmp_path / "ids.json"), **over)


def test_pilot_mock_end_to_end(tmp_path):
    assert main(["--config", cfg_isolated(tmp_path), "--model", "mock"]) == 0
    runs = read_table(tmp_path / "out" / "runs.csv", RUNS, allow_extra=False)
    resps = read_table(tmp_path / "out" / "responses.csv", RESPONSES, allow_extra=False)
    assert len(runs) == 12 and len(resps) == 10 * 1 + 2 * 3
    assert {r["run_status"] for r in runs} == {"completed"}
    assert len({r["run_batch_id"] for r in runs}) == 2  # one batch per protocol (ST1-1.0.0, MT3-1.0.0)
    assert len(list((tmp_path / "out" / "batches").glob("RBATCH-*.yaml"))) == 2


def test_validate_only_makes_no_output(tmp_path):
    assert main(["--config", cfg(tmp_path), "--validate-only"]) == 0
    assert not (tmp_path / "out").exists()


def test_missing_metadata_blocks_official_style_config(tmp_path):
    assert main(["--config", cfg(tmp_path, allow_missing_metadata=False), "--model", "mock"]) == 2
    assert not (tmp_path / "out").exists()


def test_unreviewed_items_block_official_style_config(tmp_path):
    assert main(["--config", cfg(tmp_path, allowed_item_review_status=["verified"]), "--model", "mock"]) == 2


def test_tbd_model_version_is_refused(tmp_path):
    import pytest
    models = tmp_path / "models.yaml"
    models.write_text(yaml.safe_dump({"models": [{"model_id": "x", "client": "openai_compatible", "provider": "p",
                                                  "model_version": "TBD", "api_version": "v1"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="TBD"):
        main(["--config", cfg(tmp_path), "--model", "x", "--models-yaml", str(models)])


def test_live_pilot_config_selects_four_items(tmp_path):
    c = yaml.safe_load((ROOT / "config" / "pilot_live.yaml").read_text(encoding="utf-8"))
    c["output_dir"] = str(tmp_path / "out")
    c["id_registry"] = str(tmp_path / "ids.json")
    p = tmp_path / "live.yaml"
    p.write_text(yaml.safe_dump(c), encoding="utf-8")
    assert main(["--config", str(p), "--model", "mock"]) == 0
    runs = read_table(tmp_path / "out" / "runs.csv", RUNS)
    assert sorted(r["item_id"] for r in runs) == ["KYAB-900004", "KYAB-900006", "KYAB-900011", "KYAB-900012"]
    assert len(read_table(tmp_path / "out" / "responses.csv", RESPONSES)) == 2 + 2 * 3


def test_unknown_item_id_is_rejected(tmp_path):
    assert main(["--config", cfg(tmp_path), "--model", "mock", "--items", "KYAB-123456"]) == 2


def test_old_pilot_with_legacy_protocol_ids_cannot_start_new_runs(tmp_path):
    """dataset 0.0.1 (codebook v0.2 ids) is kept for past logs; new runs must use the SemVer ids."""
    old = cfg(tmp_path, dataset_dir="data/pilot", dataset_version="0.0.1")
    assert main(["--config", old, "--model", "mock"]) == 2
    assert not (tmp_path / "out").exists()


def test_ids_are_unique_across_output_dirs(tmp_path):
    reg = str(tmp_path / "shared_ids.json")
    for out in ("a", "b"):
        c = yaml.safe_load((ROOT / "config" / "pilot_live.yaml").read_text(encoding="utf-8"))
        c.update(output_dir=str(tmp_path / out), id_registry=reg)
        p = tmp_path / f"{out}.yaml"
        p.write_text(yaml.safe_dump(c), encoding="utf-8")
        assert main(["--config", str(p), "--model", "mock"]) == 0
    ids = [r["run_id"] for d in ("a", "b") for r in read_table(tmp_path / d / "runs.csv", RUNS)]
    assert len(ids) == len(set(ids)) == 8


def test_call_metrics_sidecar_and_manifest(tmp_path):
    assert main(["--config", cfg(tmp_path, run_meta={"run_purpose": "test"}), "--model", "mock"]) == 0
    from kyab.call_metrics import CALL_METRICS
    m = read_table(tmp_path / "out" / "call_metrics.csv", CALL_METRICS, allow_extra=False)
    resp = read_table(tmp_path / "out" / "responses.csv", RESPONSES)
    assert [r["response_id"] for r in m] == [r["response_id"] for r in resp]
    assert all(int(r["latency_ms"]) >= 0 for r in m)
    man = yaml.safe_load(next((tmp_path / "out" / "batches").glob("*.yaml")).read_text(encoding="utf-8"))
    assert man["run_meta"] == {"run_purpose": "test"}
    assert man["technical_metrics"]["n_calls"] > 0 and "commit" in man["code"]
