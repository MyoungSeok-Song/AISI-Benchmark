"""Data and relationship checks, run before any model call (inputs) and after a batch (outputs).

Issue levels
- error:   structural problem (format, key, FK, turn order, protocol mismatch). The runner refuses to start.
- missing: a codebook-required human-input field is blank. Blocks execution unless the run config
           explicitly allows incomplete metadata (development pilot); always listed in the report.
- warning: allowed but worth a look (e.g. running items that are not review-verified).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .protocol import PROTOCOLS
from .schemas import ITEM_TAGS, ITEMS, PROMPTS, RESPONSES, RUNS, RISK_CODES, SEMVER, Req, Table, check_value
from .taxonomy import TaxonomyRegistry

TAXONOMY_DIR = Path(__file__).resolve().parents[2] / "data" / "taxonomy"


@dataclass
class Issue:
    level: str  # error | missing | warning
    table: str
    key: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.table} {self.key} {self.field}: {self.message}"


# Required fields whose codebook rule explicitly allows a blank ("조건부 공란" / "아니면 공란" / no response).
BLANK_ALLOWED = {
    ("item_tags", "primary_risk"),
    ("item_tags", "companion_context"),
    ("responses", "response_text"),
}
KEY_FIELDS = {
    "items": {"item_id", "item_version", "dataset_version", "conversation_mode", "planned_round_count", "protocol_id"},
    "item_tags": {"item_id", "item_version", "tag_revision", "tag_status"},
    "prompts": {"turn_id", "item_id", "item_version", "turn_index", "message_text"},
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _key(table: Table, row: dict) -> str:
    return "+".join(row.get(k, "") for k in table.primary_key)


def check_rows(table: Table, rows: list[dict]) -> list[Issue]:
    issues: list[Issue] = []
    seen: Counter = Counter()
    uq_seen: dict[tuple, Counter] = {u: Counter() for u in table.unique}
    for row in rows:
        k = _key(table, row)
        seen[k] += 1
        for u in table.unique:
            uq_seen[u][tuple(row.get(c, "") for c in u)] += 1
        for f in table.fields:
            v = row.get(f.name, "")
            if v.strip() == "":
                if f.name in KEY_FIELDS.get(table.name, ()) or (
                        table.name in ("runs", "responses") and f.req.required
                        and (table.name, f.name) not in BLANK_ALLOWED):
                    issues.append(Issue("error", table.name, k, f.name, "필수값 공란"))
                elif f.req.required and (table.name, f.name) not in BLANK_ALLOWED:
                    issues.append(Issue("missing", table.name, k, f.name, f"필수({f.req.value}) 메타데이터 공란"))
                continue
            msg = check_value(f, v)
            if msg:
                issues.append(Issue("error", table.name, k, f.name, msg))
    for k, n in seen.items():
        if n > 1:
            issues.append(Issue("error", table.name, k, "+".join(table.primary_key), f"PK 중복 {n}건"))
    for u, cnt in uq_seen.items():
        for vals, n in cnt.items():
            if n > 1:
                issues.append(Issue("error", table.name, "+".join(vals), "+".join(u), f"UQ 중복 {n}건"))
    return issues


def validate_inputs(items: list[dict], prompts: list[dict], item_tags: list[dict] | None,
                    dataset_version: str, allow_legacy_protocols: bool = False) -> list[Issue]:
    """allow_legacy_protocols: only for re-validating data built before the protocol_id change (v0.2 ids)."""
    issues = check_rows(ITEMS, items) + check_rows(PROMPTS, prompts)
    item_keys = {(r["item_id"], r["item_version"]) for r in items}
    item_ids = {r["item_id"] for r in items}

    by_item: dict[tuple, list[dict]] = defaultdict(list)
    for p in prompts:
        ik = (p["item_id"], p["item_version"])
        if ik not in item_keys:
            issues.append(Issue("error", "prompts", p["turn_id"], "item_id+item_version",
                                f"존재하지 않는 문항 참조 {ik}"))
        by_item[ik].append(p)

    for it in items:
        ik = (it["item_id"], it["item_version"])
        key = "+".join(ik)
        if it["dataset_version"] != dataset_version:
            issues.append(Issue("error", "items", key, "dataset_version",
                                f"실행 설정 dataset_version {dataset_version}과 다름: {it['dataset_version']}"))
        if it.get("parent_item_id") and it["parent_item_id"] not in item_ids:
            issues.append(Issue("warning", "items", key, "parent_item_id",
                                "상위 문항이 이 배포본에 없음 (이전 배포본 문항이면 정상)"))
        try:
            planned = int(it["planned_round_count"])
        except ValueError:
            continue  # already reported by check_rows
        # codebook v0.3 (agreed 2026-09-30): original_text is required only for items from an external source
        if it.get("source_benchmark") and it["source_benchmark"] != "NEW" and not it.get("original_text", "").strip():
            issues.append(Issue("missing", "items", key, "original_text",
                                "외부 원천 문항(source_benchmark ≠ NEW)은 원문 필수"))
        mode, proto = it["conversation_mode"], it["protocol_id"]
        if mode == "single" and planned != 1:
            issues.append(Issue("error", "items", key, "planned_round_count", f"single인데 {planned}턴"))
        if mode == "multi" and planned < 2:
            issues.append(Issue("error", "items", key, "planned_round_count", f"multi인데 {planned}턴"))
        spec = PROTOCOLS.get(proto)
        if spec and spec.legacy and not allow_legacy_protocols:
            issues.append(Issue("error", "items", key, "protocol_id",
                                f"구 표기 {proto} — 새 데이터에는 SemVer 표기(예: MT3-1.0.0) 사용. 구 표기는 과거 로그 판독용"))
        if spec and (spec.turns != planned or spec.mode != mode):
            issues.append(Issue("error", "items", key, "protocol_id",
                                f"{proto}는 {spec.mode}/{spec.turns}턴인데 문항은 {mode}/{planned}턴"))
        idx = []
        for p in by_item.get(ik, []):
            try:
                idx.append(int(p["turn_index"]))
            except ValueError:
                pass
        if sorted(idx) != list(range(1, planned + 1)):
            issues.append(Issue("error", "prompts", key, "turn_index",
                                f"예정 {list(range(1, planned + 1))}, 실제 {sorted(idx)}"))

    if item_tags is not None:
        issues += check_rows(ITEM_TAGS, item_tags)
        case_type = {(it["item_id"], it["item_version"]): it.get("case_type", "") for it in items}
        current: Counter = Counter()
        for t in item_tags:
            tk = (t["item_id"], t["item_version"])
            if tk not in item_keys:
                issues.append(Issue("error", "item_tags", _key(ITEM_TAGS, t), "item_id+item_version",
                                    f"존재하지 않는 문항 참조 {tk}"))
            if t["tag_status"] == "current":
                current[tk] += 1
            # codebook v0.3: primary_risk required only once risk tagging is confirmed; safe controls stay blank
            status, primary = t.get("risk_review_status", ""), t.get("primary_risk", "").strip()
            if status == "mapped" and not primary:
                issues.append(Issue("missing", "item_tags", _key(ITEM_TAGS, t), "primary_risk",
                                    "risk_review_status=mapped인데 primary_risk 공란"))
            if status == "not_applicable" and primary:
                issues.append(Issue("error", "item_tags", _key(ITEM_TAGS, t), "primary_risk",
                                    "risk_review_status=not_applicable(안전 대조군)인데 primary_risk 값이 있음"))
            issues += check_tag_codes(t, case_type.get(tk, ""))
        for ik in item_keys:
            if current[ik] != 1:
                issues.append(Issue("error", "item_tags", "+".join(ik), "tag_status",
                                    f"current 태그 {current[ik]}개 (정확히 1개여야 함)"))
    return issues


@lru_cache(maxsize=1)
def _taxonomies() -> TaxonomyRegistry:
    return TaxonomyRegistry.from_dir(TAXONOMY_DIR)


def _json_list(v: str) -> list:
    try:
        x = json.loads(v) if v.strip() else []
    except json.JSONDecodeError:
        return []  # format error is reported by check_rows
    return x if isinstance(x, list) else []


def check_tag_codes(tag: dict, case_type: str) -> list[Issue]:
    """Risk codes by taxonomy_version (codebook v0.3, same rule as the team runner): MAJOR 0 = legacy R1~R5,
    MAJOR ≥ 1 = the A1~A10 table of that version in data/taxonomy/. Checks primary_risk, secondary_risks,
    sub_risk_codes (each a sub of primary_risk) and, when the column is present, control_target_risk
    (10/05 reply ②: which risk group a safe-control item controls; only on safe_control items)."""
    v = tag.get("taxonomy_version", "").strip()
    if not re.fullmatch(SEMVER, v):
        return []  # reported by check_rows
    key, out = _key(ITEM_TAGS, tag), []

    def err(field, msg, level="error"):
        out.append(Issue(level, "item_tags", key, field, msg))

    primary = tag.get("primary_risk", "").strip()
    secondary = _json_list(tag.get("secondary_risks", ""))
    subs = _json_list(tag.get("sub_risk_codes", ""))
    control = (tag.get("control_target_risk") or "").strip()
    if int(v.split(".")[0]) == 0:
        majors, tx = set(RISK_CODES), None
    else:
        try:
            tx = _taxonomies().get(v)
        except KeyError as e:
            err("taxonomy_version", f"{e.args[0]} — data/taxonomy/에 이 판본 기준표가 필요")
            return out
        majors = {c.code for c in tx.majors()}
    scheme = f"taxonomy_version {v}"
    if primary and primary not in majors:
        err("primary_risk", f"{primary!r}는 {scheme}의 대분류가 아님 (허용: {sorted(majors)})")
    bad = [c for c in secondary if c not in majors]
    if bad:
        err("secondary_risks", f"{bad}는 {scheme}의 대분류가 아님")
    if primary and primary in secondary:
        err("secondary_risks", f"primary_risk {primary}가 secondary_risks에도 있음", "warning")
    if tx is not None:
        for sub in subs:
            if not tx.has(sub, "sub"):
                err("sub_risk_codes", f"{sub!r}는 {scheme}의 소분류가 아님")
            elif not primary:
                err("sub_risk_codes", f"primary_risk 없이 소분류 {sub}만 있음")
            else:
                msg = tx.check_pair(primary, sub)
                if msg:
                    err("sub_risk_codes", msg)
    if "control_target_risk" in tag:
        if control and control not in majors:
            err("control_target_risk", f"{control!r}는 {scheme}의 대분류가 아님")
        if control and case_type != "safe_control":
            err("control_target_risk", f"대조 문항(case_type=safe_control)이 아닌데 값이 있음 (case_type={case_type!r})")
        if not control and case_type == "safe_control" and tag.get("tag_status") == "current":
            err("control_target_risk", "대조 문항인데 어느 위험군의 대조인지 비어 있음", "warning")
    return out


def validate_outputs(items: list[dict], prompts: list[dict], runs: list[dict],
                     responses: list[dict]) -> list[Issue]:
    issues = check_rows(RUNS, runs) + check_rows(RESPONSES, responses)
    items_by_key = {(r["item_id"], r["item_version"]): r for r in items}
    prompts_by_id = {p["turn_id"]: p for p in prompts}
    runs_by_id = {r["run_id"]: r for r in runs}
    resp_by_run: dict[str, list[tuple[int, dict]]] = defaultdict(list)

    for r in responses:
        run = runs_by_id.get(r["run_id"])
        turn = prompts_by_id.get(r["turn_id"])
        if run is None:
            issues.append(Issue("error", "responses", r["response_id"], "run_id", "runs에 없는 run_id"))
        if turn is None:
            issues.append(Issue("error", "responses", r["response_id"], "turn_id", "prompts에 없는 turn_id"))
        if run is None or turn is None:
            continue
        if (turn["item_id"], turn["item_version"]) != (run["item_id"], run["item_version"]):
            issues.append(Issue("error", "responses", r["response_id"], "turn_id",
                                "run의 문항·버전과 turn의 문항·버전이 다름"))
        resp_by_run[r["run_id"]].append((int(turn["turn_index"]), r))
        if r["response_status"] == "success" and not r["response_text"].strip():
            issues.append(Issue("error", "responses", r["response_id"], "response_text", "success인데 응답 공란"))
        if r["response_status"] == "blocked" and not r["block_source"]:
            issues.append(Issue("error", "responses", r["response_id"], "block_source", "blocked인데 block_source 공란"))
        try:
            msgs = json.loads(r["request_messages_json"])
            if not all(isinstance(m, dict) and "role" in m and "content" in m for m in msgs):
                raise ValueError("role/content 없는 원소")
            if msgs[-1]["role"] != "user":
                raise ValueError("마지막 메시지가 user가 아님")
            expected_len = 2 * int(turn["turn_index"]) + (1 if msgs[0]["role"] == "system" else 0) - 1
            if len(msgs) != expected_len:
                raise ValueError(f"turn {turn['turn_index']}의 메시지 수 {len(msgs)} ≠ {expected_len}")
        except (ValueError, KeyError, IndexError, json.JSONDecodeError) as e:
            issues.append(Issue("error", "responses", r["response_id"], "request_messages_json", str(e)))

    for run in runs:
        rid = run["run_id"]
        item = items_by_key.get((run["item_id"], run["item_version"]))
        if item is None:
            issues.append(Issue("error", "runs", rid, "item_id+item_version", "items에 없는 문항"))
            continue
        if run["dataset_version"] != item["dataset_version"]:
            issues.append(Issue("error", "runs", rid, "dataset_version", "문항의 dataset_version과 다름"))
        if run["protocol_id"] != item["protocol_id"]:
            issues.append(Issue("error", "runs", rid, "protocol_id", "문항의 protocol_id와 다름"))
        if run["system_prompt_hash"] != sha256_text(run["system_prompt_text"]):
            issues.append(Issue("error", "runs", rid, "system_prompt_hash", "system_prompt_text의 SHA-256과 다름"))
        got = sorted(resp_by_run.get(rid, []), key=lambda x: x[0])
        if [t for t, _ in got] != list(range(1, len(got) + 1)):
            issues.append(Issue("error", "runs", rid, "responses", f"응답 턴이 1부터 연속되지 않음 {[t for t, _ in got]}"))
        n_success = sum(1 for _, r in got if r["response_status"] == "success")
        if run["actual_turn_count"] and int(run["actual_turn_count"]) != n_success:
            issues.append(Issue("error", "runs", rid, "actual_turn_count",
                                f"{run['actual_turn_count']} ≠ success 응답 {n_success}"))
        planned = int(item["planned_round_count"])
        if run["run_status"] == "completed" and n_success != planned:
            issues.append(Issue("error", "runs", rid, "run_status", f"completed인데 success {n_success}/{planned}"))
        if run["run_status"] == "completed" and run["stop_reason"] != "planned_end":
            issues.append(Issue("error", "runs", rid, "stop_reason", "completed인데 planned_end가 아님"))
    return issues


def summarize(issues: list[Issue]) -> dict[str, int]:
    return dict(Counter(i.level for i in issues))


def missing_field_counts(issues: list[Issue]) -> dict[str, int]:
    return dict(Counter(f"{i.table}.{i.field}" for i in issues if i.level == "missing"))


__all__ = ["Issue", "validate_inputs", "validate_outputs", "check_rows", "sha256_text", "summarize",
           "missing_field_counts", "Req"]
