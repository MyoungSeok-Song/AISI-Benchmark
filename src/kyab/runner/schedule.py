"""Run planning for resume and duplicate prevention.

A run is identified by its *run key*: model × dataset × item × protocol × persona × rollout. Before calling a
model the runner reads the output directory's runs.csv and

- skips every key that already has a ``completed`` run (never creates a second one),
- skips keys whose latest run did not complete (failed / partial / still ``running`` after a crash) unless
  ``retry_failed`` is set; a retry gets a new run_id and the old row stays as an audit record,
- refuses to mix generation settings: an existing run with the same key but a different temperature, top_p,
  max_output_tokens, system prompt or model_version is a conflict (use another output directory).
"""
from __future__ import annotations

from dataclasses import dataclass, field

RunKey = tuple[str, str, str, str, str, str, str]
SETTINGS = ("model_version", "temperature", "top_p", "max_output_tokens", "system_prompt_hash")


def run_key(model_id: str, dataset_version: str, item_id: str, item_version: str, protocol_id: str,
            persona_id: str, rollout_no: int | str) -> RunKey:
    return (model_id, dataset_version, item_id, item_version, protocol_id, persona_id, str(rollout_no))


def row_key(r: dict) -> RunKey:
    return run_key(r["model_id"], r["dataset_version"], r["item_id"], r["item_version"], r["protocol_id"],
                   r.get("persona_id") or "none", r["rollout_no"])


@dataclass
class Plan:
    to_run: list = field(default_factory=list)          # jobs to execute now
    done: list = field(default_factory=list)            # (job, run_id) already completed
    unfinished: list = field(default_factory=list)      # (job, run_id, status) skipped, needs --retry-failed
    retried: list = field(default_factory=list)         # (job, previous run_id, status) re-run now
    conflicts: list = field(default_factory=list)       # (job, run_id, {setting: (old, new)})


def plan_runs(jobs: list, key_of, existing_runs: list[dict], settings: dict[str, str],
              retry_failed: bool) -> Plan:
    """jobs: opaque objects; key_of(job) → RunKey. settings: current values of SETTINGS (as CSV strings)."""
    by_key: dict[RunKey, list[dict]] = {}
    for r in existing_runs:
        by_key.setdefault(row_key(r), []).append(r)
    plan = Plan()
    for job in jobs:
        prev = by_key.get(key_of(job), [])
        diff = {}
        for r in prev:
            diff = {s: (r[s], settings[s]) for s in SETTINGS if _norm(r.get(s, "")) != _norm(settings[s])}
            if diff:
                plan.conflicts.append((job, r["run_id"], diff))
                break
        if diff:
            continue
        completed = [r for r in prev if r["run_status"] == "completed"]
        if completed:
            plan.done.append((job, completed[0]["run_id"]))
        elif prev:
            last = max(prev, key=lambda r: (r["started_at"], r["run_id"]))
            if retry_failed:
                plan.retried.append((job, last["run_id"], last["run_status"]))
                plan.to_run.append(job)
            else:
                plan.unfinished.append((job, last["run_id"], last["run_status"]))
        else:
            plan.to_run.append(job)
    return plan


def _norm(v) -> str:
    """'1' and '1.0' are the same temperature."""
    s = str(v).strip()
    try:
        return repr(float(s))
    except ValueError:
        return s
