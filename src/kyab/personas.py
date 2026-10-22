"""Persona axis: the same item is run once per persona, with the user turns rewritten in that persona's style.

Inputs live in a persona directory (config ``persona_dir``), separate from the 7-CSV dataset because the
persona team delivers them independently:

    personas.csv          one row per persona (schemas.PERSONAS) — age band × gender
    persona_prompts.csv   persona-specific rewrite of every prompts.csv turn (schemas.PERSONA_PROMPTS)

Only persona_prompts.message_text reaches the target model; persona labels / style notes never do.
Until the rewrites arrive, ``persona_prompt_fallback: base`` (development only) sends the unchanged base
turn for every persona — the batch manifest records that, so such runs are never mistaken for persona runs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path

from .loader import ExecutableItem
from .schemas import PERSONA_PROMPTS, PERSONAS
from .storage import read_table
from .text_qa import check_cell, check_file_encoding
from .validator import Issue, check_rows

NO_PERSONA = "none"
AGE_BANDS = ("early_10_12", "mid_13_15", "late_16_18")
GENDERS = ("male", "female")
FALLBACK_MODES = ("none", "base")


@dataclass
class PersonaSet:
    root: Path
    personas: list[dict]
    prompts: list[dict] | None  # None = persona_prompts.csv not delivered yet

    @property
    def ids(self) -> list[str]:
        return [p["persona_id"] for p in self.personas]

    def variant_map(self) -> dict[tuple[str, str], str]:
        return {(r["persona_id"], r["turn_id"]): r["message_text"] for r in self.prompts or []}


def load_personas(root: str | Path) -> PersonaSet:
    root = Path(root)
    pp = root / "persona_prompts.csv"
    for p in (root / "personas.csv", pp):
        if p.exists():
            check_file_encoding(p)
    return PersonaSet(root, read_table(root / "personas.csv", PERSONAS),
                      read_table(pp, PERSONA_PROMPTS) if pp.exists() else None)


def validate_personas(ps: PersonaSet, items: list[ExecutableItem], fallback: str) -> list[Issue]:
    """Structure + coverage. Every (persona, turn) the batch will send must have a rewrite, unless fallback=base."""
    if fallback not in FALLBACK_MODES:
        return [Issue("error", "config", "persona_prompt_fallback", "", f"{fallback!r} — 허용값 {FALLBACK_MODES}")]
    issues = check_rows(PERSONAS, ps.personas)
    grid = {(p["age_band"], p["gender"]) for p in ps.personas}
    if grid != set(product(AGE_BANDS, GENDERS)) or len(ps.personas) != len(grid):
        missing_cells = sorted(set(product(AGE_BANDS, GENDERS)) - grid)
        issues.append(Issue("error", "personas", "*", "age_band+gender",
                            f"연령대 3 × 성별 2 = 6벌이 아님: {len(ps.personas)}행, 조합 {len(grid)}개, "
                            f"빠진 조합 {missing_cells}"))
    if ps.prompts is None:
        if fallback != "base":
            issues.append(Issue("error", "persona_prompts", "*", "", "persona_prompts.csv 없음. 페르소나별 발화가 오기 전 "
                                "개발 실행이면 config에 persona_prompt_fallback: base"))
        return issues
    issues += check_rows(PERSONA_PROMPTS, ps.prompts)
    for row in ps.prompts:
        issues += check_cell(PERSONA_PROMPTS, row, "message_text", row.get("message_text", ""))
    ids, turns = set(ps.ids), {t.turn_id for it in items for t in it.turns}
    for r in ps.prompts:
        if r["persona_id"] not in ids:
            issues.append(Issue("error", "persona_prompts", r["persona_id"], "persona_id", "personas.csv에 없는 페르소나"))
        if r["turn_id"] not in turns:
            issues.append(Issue("warning", "persona_prompts", r["turn_id"], "turn_id", "이번 실행 대상 turn이 아님"))
    have = set(ps.variant_map())
    missing = [(pid, t) for pid in ps.ids for t in sorted(turns) if (pid, t) not in have]
    if missing and fallback != "base":
        issues.append(Issue("error", "persona_prompts", "*", "message_text",
                            f"페르소나별 발화 누락 {len(missing)}건 (예: {missing[:3]})"))
    elif missing:
        issues.append(Issue("warning", "persona_prompts", "*", "message_text",
                            f"페르소나별 발화 누락 {len(missing)}건 → 기본 발화로 대체 (fallback=base)"))
    return issues


def apply_persona(item: ExecutableItem, persona_id: str, variants: dict[tuple[str, str], str]) -> ExecutableItem:
    """The item as one persona sends it. Turns without a rewrite keep the base text (validated beforehand)."""
    turns = tuple(replace(t, message_text=variants.get((persona_id, t.turn_id), t.message_text)) for t in item.turns)
    return replace(item, turns=turns, persona_id=persona_id)


def prompt_source(ps: PersonaSet, items: list[ExecutableItem]) -> str:
    """Recorded in the batch manifest: where the user turns of this batch came from."""
    turns = {t.turn_id for it in items for t in it.turns}
    have = {k for k in ps.variant_map() if k[1] in turns}
    full = len(ps.ids) * len(turns)
    if not have:
        return "base_fallback (persona_prompts 없음 — 모든 페르소나가 같은 기본 발화)"
    return "persona_prompts" if len(have) == full else f"partial ({len(have)}/{full}, 나머지 기본 발화)"
