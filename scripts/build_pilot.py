"""Build the English development pilot dataset in data/pilot/ from the raw snapshots.

    .venv/bin/python scripts/build_pilot.py

CAREBench 5 + MinorBench 5 single-turn items and 2 developer-written MT3 scripts → items.csv,
item_tags.csv, prompts.csv, manifest.yaml and CONVERSION_REPORT.md. Deterministic: rerunning it on the
same raw files produces the same rows (except built_at).
"""
from __future__ import annotations

import hashlib
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kyab.adapters import carebench, minorbench, upstream_json  # noqa: E402
from kyab.adapters.common import Converted, IdAllocator  # noqa: E402
from kyab.schemas import ITEM_TAGS, ITEMS, PROMPTS  # noqa: E402
from kyab.storage import write_table  # noqa: E402
from kyab.validator import missing_field_counts, summarize, validate_inputs  # noqa: E402

DATASET_VERSION = "0.0.2"  # 0.0.1 (data/pilot/, codebook v0.2 protocol ids) is kept unchanged
ITEM_VERSION = "0.1.1"  # PATCH: protocol_id notation fix (AI-비전달 메타데이터 정정), texts unchanged
RUBRIC_ID = "RB-6D-v1"  # plan 표 Ⅳ-20; the only rubric the plan defines
TAXONOMY_VERSION = "0.7.0"  # tag file 강우_L01_R5_M5_질문별분류태그_v0.7
# provisional dev ranges — agree with the dataset team. item_id is stable across versions (same case);
# turn_id is a global PK, so every dataset version gets its own range (0.0.1 used 90000001~, 0.0.2 uses 90000101~).
ITEM_ID_START, TURN_ID_START = 900001, 90000101
N_PER_SOURCE = 5


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    raw, out_dir = ROOT / "data" / "raw", ROOT / "data" / f"pilot_{DATASET_VERSION}"
    built_at = datetime.now().astimezone().isoformat(timespec="seconds")
    ids = IdAllocator(ITEM_ID_START, TURN_ID_START)
    common = dict(dataset_version=DATASET_VERSION, taxonomy_version=TAXONOMY_VERSION, built_at=built_at)

    ds = Converted()
    cb_src, mb_src = raw / "carebench" / "prompts.csv", raw / "minorbench" / "test.csv"
    mt_src = raw / "dev_multiturn" / "dev_mt3_v0.2.json"
    cb = carebench.select_one_per_category(carebench.read_source(cb_src), N_PER_SOURCE)
    ds.extend(carebench.convert(cb, ids, item_version=ITEM_VERSION, rubric_id=RUBRIC_ID, **common))
    mb = minorbench.select_one_per_category(minorbench.read_source(mb_src), N_PER_SOURCE)
    ds.extend(minorbench.convert(mb, ids, item_version=ITEM_VERSION, rubric_id=RUBRIC_ID, **common))
    ds.extend(upstream_json.convert(mt_src, ids, **common))

    out_dir.mkdir(parents=True, exist_ok=True)
    write_table(out_dir / "items.csv", ITEMS, ds.items)
    write_table(out_dir / "item_tags.csv", ITEM_TAGS, ds.item_tags)
    write_table(out_dir / "prompts.csv", PROMPTS, ds.prompts)

    issues = validate_inputs(ds.items, ds.prompts, ds.item_tags, DATASET_VERSION)
    manifest = {
        "dataset_version": DATASET_VERSION,
        "schema": "ETRI 7-CSV codebook v0.2 + agreed v0.3 changes of 2026-09-30 (schemas.CODEBOOK_V03_PENDING)",
        "changes_from_0.0.1": ["protocol_id ST1-v1/MT3-v1 → ST1-1.0.0/MT3-1.0.0; item_version 0.1.0 → 0.1.1 (PATCH, texts unchanged)",
                               "original_text conditional, intent_tag optional, risk_review_status +not_applicable"],
        "purpose": "dev_pilot_english",
        "not_for_official_evaluation": True,
        "description": ("English development pilot for the evaluation runner. message_text is the untranslated "
                        "English source text (codebook expects Korean); results must not be mixed with the "
                        "Korean benchmark."),
        "built_at": built_at,
        "id_ranges": {"item_id": f"KYAB-{ITEM_ID_START:06d}~ (provisional dev range)",
                      "turn_id": f"TURN-{TURN_ID_START:08d}~ (provisional dev range; 0.0.1 used TURN-90000001~)"},
        "fixed_values_used": {"item_version": ITEM_VERSION, "rubric_id": RUBRIC_ID,
                              "taxonomy_version": TAXONOMY_VERSION},
        "sources": {
            "CAREBench": {"file": "data/raw/carebench/prompts.csv", "sha256": sha256(cb_src),
                          "selection": f"first {N_PER_SOURCE} risk_category values (sorted), lowest case_uid each"},
            "MinorBench": {"file": "data/raw/minorbench/test.csv", "sha256": sha256(mb_src),
                           "selection": f"first {N_PER_SOURCE} Category values (sorted), lowest PromptID each"},
            "dev_multiturn": {"file": "data/raw/dev_multiturn/dev_mt3_v0.2.json", "sha256": sha256(mt_src),
                              "selection": "all (developer-written MT3 test scripts, not research items)"},
        },
        "counts": {"items": len(ds.items), "prompts": len(ds.prompts), "item_tags": len(ds.item_tags),
                   "by_protocol": dict(Counter(i["protocol_id"] for i in ds.items))},
        "files_sha256": {n: sha256(out_dir / n) for n in ("items.csv", "item_tags.csv", "prompts.csv")},
        "validation": summarize(issues),
    }
    (out_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
                                           encoding="utf-8")

    miss = missing_field_counts(issues)
    lines = [
        f"# 파일럿 데이터 변환 보고서 (dataset_version {DATASET_VERSION}, 개발용 영어 파일럿)", "",
        f"생성 시각: {built_at}", "",
        "## 변환된 문항", "",
        *[f"- {n}" for n in ds.notes], "",
        "## 검증 결과", "",
        f"- 구조 오류(error): {sum(1 for i in issues if i.level == 'error')}건",
        f"- 필수 메타데이터 공란(missing): {sum(1 for i in issues if i.level == 'missing')}건",
        f"- 경고(warning): {sum(1 for i in issues if i.level == 'warning')}건", "",
        "## 원천에 없어 비워 둔 필수 필드 (추정 입력 금지 원칙)", "",
        "| 필드 | 공란 문항 수 | 이유 |", "|---|---|---|",
    ]
    reasons = {
        "items.target_age_group": "원천에 연령대 정보 없음 — 누가 어떤 기준으로 부여할지 결정 필요 (개발용 3턴은 mid_13_15 명시)",
        "items.risk_cues_json": "판정용 문항 설계값 — 문항 설계자 입력 필요",
        "items.prohibited_response_json": "판정용 문항 설계값 — 문항 설계자 입력 필요",
        "items.localization_type": "번역 전 영어 원문을 그대로 쓰는 경우에 해당하는 통제어휘가 없음",
        "items.original_text": "신규(NEW) 문항은 원문이 없음 — 코드북 A열(필수)과 설명(원천 추적 시에만)이 충돌",
        "item_tags.severity_tag": "태그 입력자 입력 필요",
        "item_tags.expected_response_tags": "태그 입력자 입력 필요",
        "item_tags.role_tags": "태그 입력자 입력 필요",
        "item_tags.impersonation_flag": "태그 입력자 입력 필요",
        "item_tags.intent_tag": "태그 입력자 입력 필요 (코드북 설명상 선택 필드인데 A열은 필수)",
    }
    for k, n in sorted(miss.items()):
        lines.append(f"| `{k}` | {n} | {reasons.get(k, '')} |")
    lines += ["", "구조 오류가 0건이면 Runner 실행 가능. 공란 필드는 Target LLM 입력에 쓰이지 않는 판정·운영 메타데이터라 "
                  "개발용 파일럿에서는 `allow_missing_metadata: true`로 실행하고, 정식 데이터에서는 실행을 막는다.", ""]
    (out_dir / "CONVERSION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"items {len(ds.items)}, prompts {len(ds.prompts)}, item_tags {len(ds.item_tags)} → {out_dir}")
    print("validation:", summarize(issues))
    for i in issues:
        if i.level != "missing":
            print("  ", i)
    return 1 if any(i.level == "error" for i in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
