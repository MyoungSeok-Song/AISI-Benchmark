import copy

from conftest import DV, make_item, make_prompts, make_tag, mock_info

from kyab.models.mock import MockModelClient
from kyab.runner.conversation import run_item
from kyab.storage import read_table
from kyab.schemas import RESPONSES, RUNS
from kyab.validator import validate_inputs, validate_outputs


def errors(issues):
    return [(i.table, i.field, i.message) for i in issues if i.level == "error"]


def check(ds, **mut):
    items, prompts, tags = copy.deepcopy(ds.items), copy.deepcopy(ds.prompts), copy.deepcopy(ds.item_tags)
    for fn in mut.values():
        fn(items, prompts, tags)
    return errors(validate_inputs(items, prompts, tags, DV))


def test_clean_dataset_has_no_errors(small_dataset):
    assert check(small_dataset) == []


def test_duplicate_item_pk(small_dataset):
    e = check(small_dataset, f=lambda i, p, t: i.append(dict(i[0])))
    assert any("PK 중복" in m for _, _, m in e)


def test_duplicate_turn_id(small_dataset):
    def f(i, p, t):
        p[2]["turn_id"] = p[1]["turn_id"]
    assert any(tb == "prompts" and "PK 중복" in m for tb, _, m in check(small_dataset, f=f))


def test_prompt_references_missing_item(small_dataset):
    def f(i, p, t):
        p += make_prompts("KYAB-999999", ["orphan"], 50)
    assert any("존재하지 않는 문항" in m for _, _, m in check(small_dataset, f=f))


def test_turn_gap_is_rejected(small_dataset):
    def f(i, p, t):
        p[2]["turn_index"] = "4"  # 1, 4, 3 → not 1..3
    assert any(fl == "turn_index" for _, fl, _ in check(small_dataset, f=f))


def test_planned_count_mismatch(small_dataset):
    def f(i, p, t):
        del p[3]  # 3-turn item now has 2 prompts
    assert any("예정 [1, 2, 3], 실제 [1, 2]" in m for _, _, m in check(small_dataset, f=f))


def test_single_mode_with_three_turns(small_dataset):
    def f(i, p, t):
        i[1]["conversation_mode"] = "single"
    e = check(small_dataset, f=f)
    assert any("single인데 3턴" in m for _, _, m in e)
    assert any(fl == "protocol_id" for _, fl, _ in e)


def test_protocol_turncount_mismatch(small_dataset):
    def f(i, p, t):
        i[1]["protocol_id"] = "MT7-1.0.0"
    assert any(fl == "protocol_id" for _, fl, _ in check(small_dataset, f=f))


def test_exactly_one_current_tag(small_dataset):
    def two(i, p, t):
        t.append(make_tag("KYAB-000001", rev=2, status="current"))
    assert any("current 태그 2개" in m for _, _, m in check(small_dataset, f=two))

    def zero(i, p, t):
        t[0]["tag_status"] = "superseded"
    assert any("current 태그 0개" in m for _, _, m in check(small_dataset, f=zero))

    def history_ok(i, p, t):
        t[0]["tag_status"] = "superseded"
        t.append(make_tag("KYAB-000001", rev=2, status="current"))
    assert check(small_dataset, f=history_ok) == []


def test_dataset_version_mismatch(small_dataset):
    e = errors(validate_inputs(small_dataset.items, small_dataset.prompts, small_dataset.item_tags, "9.9.9"))
    assert any(fl == "dataset_version" for _, fl, _ in e)


def test_blank_required_metadata_is_missing_not_error(small_dataset):
    items = copy.deepcopy(small_dataset.items)
    items[0]["target_age_group"] = ""
    issues = validate_inputs(items, small_dataset.prompts, small_dataset.item_tags, DV)
    assert errors(issues) == []
    assert [(i.level, i.field) for i in issues if i.table == "items"] == [("missing", "target_age_group")]


def test_outputs_fk_and_counts(small_dataset, out, ctx):
    client = MockModelClient(mock_info())
    for it in small_dataset.executable_items():
        run_item(it, 1, client, ctx, out["ids"], out["runs"], out["responses"])
    runs = read_table(out["dir"] / "runs.csv", RUNS)
    resps = read_table(out["dir"] / "responses.csv", RESPONSES)
    assert validate_outputs(small_dataset.items, small_dataset.prompts, runs, resps) == []

    bad = copy.deepcopy(resps)
    bad[0]["run_id"] = "RUN-20990101-000001"
    bad[1]["turn_id"] = "TURN-99999999"
    e = errors(validate_outputs(small_dataset.items, small_dataset.prompts, runs, bad))
    assert ("responses", "run_id", "runs에 없는 run_id") in e
    assert ("responses", "turn_id", "prompts에 없는 turn_id") in e

    wrong_count = copy.deepcopy(runs)
    wrong_count[1]["actual_turn_count"] = "2"
    assert any(fl == "actual_turn_count" for _, fl, _ in errors(
        validate_outputs(small_dataset.items, small_dataset.prompts, wrong_count, resps)))

    tampered = copy.deepcopy(runs)
    tampered[0]["system_prompt_text"] = "changed"
    assert any(fl == "system_prompt_hash" for _, fl, _ in errors(
        validate_outputs(small_dataset.items, small_dataset.prompts, tampered, resps)))


# ---------------------------------------------------------------- codebook v0.3 agreed rules (2026-09-30)

def test_legacy_protocol_id_rejected_for_new_data_but_readable_for_old(small_dataset):
    def legacy(i, p, t):
        i[1]["protocol_id"] = "MT3-v1"
    assert any("구 표기" in m for _, _, m in check(small_dataset, f=legacy))
    items = copy.deepcopy(small_dataset.items)
    items[1]["protocol_id"] = "MT3-v1"
    assert errors(validate_inputs(items, small_dataset.prompts, small_dataset.item_tags, DV,
                                  allow_legacy_protocols=True)) == []


def test_original_text_required_only_for_external_items(small_dataset):
    items = copy.deepcopy(small_dataset.items)
    items[0]["original_text"] = ""  # source_benchmark=NEW → allowed
    assert not [i for i in validate_inputs(items, small_dataset.prompts, small_dataset.item_tags, DV)
                if i.field == "original_text"]
    items[0]["source_benchmark"] = "CAREBench"
    got = [(i.level, i.field) for i in validate_inputs(items, small_dataset.prompts, small_dataset.item_tags, DV)
           if i.field == "original_text"]
    assert got == [("missing", "original_text")]


def test_intent_tag_is_optional(small_dataset):
    assert not [i for i in validate_inputs(small_dataset.items, small_dataset.prompts, small_dataset.item_tags, DV)
                if i.field == "intent_tag"]


def test_primary_risk_rules_by_review_status(small_dataset):
    def run(status, primary):
        tags = copy.deepcopy(small_dataset.item_tags)
        tags[0].update(risk_review_status=status, primary_risk=primary)
        return [(i.level, i.message) for i in
                validate_inputs(small_dataset.items, small_dataset.prompts, tags, DV) if i.field == "primary_risk"]
    assert run("mapped", "R5") == []
    assert run("mapped", "")[0][0] == "missing"
    for status in ("unreviewed", "hold", "not_applicable"):
        assert run(status, "") == []
    assert run("not_applicable", "R5")[0][0] == "error"


def test_tag_codes_follow_taxonomy_version(small_dataset):
    """MAJOR 0 = legacy R1~R5; MAJOR ≥ 1 = A1~A10 table of that version (data/taxonomy/)."""
    def run(**over):
        tags = [dict(t) for t in small_dataset.item_tags]
        tags[0].update(risk_review_status="mapped", **over)
        return sorted((i.level, i.field) for i in validate_inputs(small_dataset.items, small_dataset.prompts, tags, DV)
                      if i.table == "item_tags" and i.level != "missing")
    assert run(taxonomy_version="0.7.0", primary_risk="R5") == []
    assert run(taxonomy_version="0.7.0", primary_risk="A5") == [("error", "primary_risk")]
    assert run(taxonomy_version="1.0.0", primary_risk="A5", sub_risk_codes='["A5.01"]') == []
    assert run(taxonomy_version="1.0.0", primary_risk="R5") == [("error", "primary_risk")]
    assert run(taxonomy_version="1.0.0", primary_risk="A5", sub_risk_codes='["A1.01"]') == [("error", "sub_risk_codes")]
    assert run(taxonomy_version="1.0.0", primary_risk="A5", secondary_risks='["A8", "X9"]') == \
        [("error", "secondary_risks")]
    assert run(taxonomy_version="2.0.0", primary_risk="A5") == [("error", "taxonomy_version")]


def test_control_target_risk_only_on_safe_controls(small_dataset):
    items = [dict(i) for i in small_dataset.items]
    items[1]["case_type"] = "safe_control"

    def run(idx, value):
        tags = [dict(t, taxonomy_version="1.0.0", primary_risk="", risk_review_status="not_applicable",
                     control_target_risk="") for t in small_dataset.item_tags]
        tags[idx]["control_target_risk"] = value
        return sorted((i.level, i.item if hasattr(i, "item") else i.key.split("+")[0])
                      for i in validate_inputs(items, small_dataset.prompts, tags, DV)
                      if i.field == "control_target_risk")
    assert run(1, "A5") == []
    assert run(0, "A5") == [("error", "KYAB-000001"), ("warning", "KYAB-000002")]  # not a control; control left blank
    assert run(1, "A11") == [("error", "KYAB-000002")]
