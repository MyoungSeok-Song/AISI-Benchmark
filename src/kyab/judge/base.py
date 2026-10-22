"""Phase 2/3 hook points — interfaces only, not implemented this phase.

Phase 1 (implemented): items + prompts → runner → runs.csv + responses.csv
Phase 2 (open):        responses.csv → Judge → judgments.csv   (06_judgments, 29 columns)
Phase 3 (open):        judgments.csv → Aggregator → results.csv (07_results, 35 columns)

A Judge reads a response together with its run, the item and the item_tags row of the tag_revision
it applies — judge-side inputs that the target model never saw. It returns rows in the exact
06_judgments column set (schemas.JUDGMENTS), which storage.write_table can persist unchanged.
Run-level fields that depend on judgments (runs.first_fail_turn / first_cfc_turn) are filled by a
post-processing step after Phase 2, never by the runner.
"""
from __future__ import annotations

from typing import Protocol

from ..schemas import JUDGMENTS, RESULTS  # noqa: F401  (column contracts for phases 2 and 3)


class Judge(Protocol):
    judge_type: str  # "llm" | "human"
    judge_id: str

    def evaluate(self, response: dict, run: dict, item: dict, item_tag: dict, prompts: list[dict],
                 evaluation_scope: str) -> dict:
        """Return one row with every column of schemas.JUDGMENTS."""
        ...


class Aggregator(Protocol):
    def aggregate(self, runs: list[dict], responses: list[dict], judgments: list[dict],
                  item_tags: list[dict]) -> list[dict]:
        """Return rows with every column of schemas.RESULTS."""
        ...


def fill_run_failure_turns(runs: list[dict], responses: list[dict], judgments: list[dict],
                           prompts: list[dict]) -> list[dict]:
    """After judging: set runs.first_fail_turn / first_cfc_turn from verdict / critical_failure_code.

    Blocked on codebook 08 open issues (verdict=inconclusive handling, CFC "none" representation).
    """
    raise NotImplementedError("판정 기준 확정 후 구현 (코드북 08_변경이력·미확정 참조)")
