"""집계 실행기: 배치들의 실행(04·05)과 판정(06)에서 지표를 계산해 07_results.csv를 만든다.

  1. 배치마다 06_judgments.csv를 검증한다. 오류가 있으면 집계하지 않는다.
  2. 본평가에 쓸 수 없는 판정기(모의)의 판정이 섞여 있으면 거부한다(--allow-mock-judge로만 허용).
  3. 모델 × 슬라이스마다 지표를 계산한다(metrics.py, 규칙은 config/aggregation_rules.yaml).
  4. 결과를 검증한 뒤 <출력 루트>/RESULTS-YYYYMMDD-###/ 에 쓴다(임시 폴더에 다 쓴 뒤 이름을 바꾼다 — 셋 중
     하나만 남는 일이 없다).
       07_results.csv             코드북 35열
       results_denominators.csv   회신 ④(확정): 보류를 분모에서 뺐으면 건수·비율·분모를 함께 제시.
                                  형식(지표마다 D 유효·I 보류·U 판정 없음·other·target, 보류율 I/(D+I))은 잠정(지표 명세 전).
                                  result_id로 07과 1:1. 코드북 밖 보조 산출물(07_ 접두어 아님)
       results_notes.json         07에 칸이 없는 보조 기록(분모·보류, 제외·가상 판정 건수, 모델별 verdict 분포,
                                  코드북에 없는 분해, 코드북 협의 후보). 코드북 표가 아니다.

집계할 때마다 새 폴더와 새 result_id가 생긴다. 이미 쓴 결과 행은 고치지 않는다.

사용 예 (runner/ 폴더에서)
  .venv/bin/python -m kyab_runner.run_aggregate samples/output/RBATCH-20261002-001 samples/output/RBATCH-20261002-002
  .venv/bin/python -m kyab_runner.run_aggregate --allow-mock-judge <배치 폴더> ...     # 모의 판정으로 경로 확인
  .venv/bin/python -m kyab_runner.run_aggregate --rules <다른 규칙 파일> <배치 폴더> ...   # 규칙을 바꿔 비교

종료 코드: 0 정상, 1 집계할 실행이 없음, 2 입력·검증 오류 또는 거부.
"""
import argparse
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import clock, csv_io, fileio, ids, judge_io, metrics, paths, validate
from .context import READ_ERRORS, prepare, report_read_error
from .exitcodes import EXIT_INVALID, EXIT_NOTHING, EXIT_OK                   # noqa: F401 (테스트가 이 모듈 이름으로 쓴다)
from .judge_io import MOCK_WARNING
from .layout import DENOMINATORS_FILE, NOTES_FILE                           # noqa: F401 (테스트가 이 모듈 이름으로 쓴다)
from .validate import CONTROL_TARGET_FIELD

# 코드북 07에 칸이 없어 results_notes.json에만 두는 항목. 열을 추가하지 않고 협의 후보로만 적는다 (S7).
# 맨 뒤 P1~P4는 납품 형식(JSONL)의 출처 역추적 항목이다.
CODEBOOK_CANDIDATES = [
    "대조 문항의 위험군 연결: 칼럼 추가 반영(회신 ② → 02 control_target_risk, 이름·위치 잠정 OV-P4). 07 slice_key_json 표기({\"primary_risk\": X}에 대조 문항 포함)와 혼합 행의 n_items·n_runs·n_responses·κ·사람 검토율 정의는 잠정 — 코드북 담당 지표 명세 확인 필요",
    "집계 전용 필드(control_target_risk)만 바뀐 태그 판본 상승에도 재채점을 요구할지(주 판정 집합이 current 판본만 쓰므로 현재는 재채점 필요) — 채점 운영 규칙 협의 후보",
    "slice_level에 case_type·성별(user_gender)·컴패니언 구분 없음 → 이 분해는 extra_slices에만 있음 (수행계획서 v1.1의 성별 보고 요구)",
    "유효 평가 대상 수(FR 분모)와 inconclusive·판정 없음 건수를 적을 07 열 없음 → results_denominators.csv와 rows.<result_id>에 있음",
    "집계에서 뺀 실행 수(stop_reason별)와 차단을 거절로 센 건수를 적을 07 열 없음 → 분모 행 other(run_excluded)와 notes에 있음",
    "판정기 식별(judge_id)이 07에 없어 모의 판정·실제 판정으로 만든 결과를 07만으로는 구분할 수 없음",
    "CRRI 임계값을 적을 열 없음(aggregation_rule_id·version으로만 추적)",
    "06: judge_status가 failed·needs_review인 행의 verdict·점수 필수성 (현재 가정 J4로 빈값 허용)",
    "06: 사람 검토가 끝났을 때 llm 행의 human_review_status를 고칠지(append-only와 충돌) — 현재는 human 행의 존재로 판단",
    "06 memo '값이 있으면 verdict=fail' → 'NONE이 아닌 값이면 verdict=fail'로 정정 제안 (회신 ③ NONE 도입에 따른 잠정 해석 OV-J1b)",
    "04 first_fail_turn·first_cfc_turn의 공란은 '판정 전·apply 미실행'과 '실패 없음'을 구분하지 못함(형식이 '정수 또는 공란'이라 NONE 불가)",
    "07 v0.3 제안: denominators_json 1열(지표별 D·I·U). 지금은 results_denominators.csv를 07과 함께 봐야 함(회신 ④)",
    "제안(기본 꺼짐, 지표 명세 몫): 보류율 경고 임계값(inconclusive_report.warn_rate), '보류를 실패로 본 FR' 참고값",
    "집계 섞임 거부는 호출 파라미터(temperature·top_p·max_output_tokens)만 본다. system_prompt_hash·safety_profile·tool_profile 등 "
    "다른 실행 조건이 한 모델 묶음에 섞이는 경우의 처리(거부·분리)는 미정 — 협의 후보",
    "제안(채점 운영 규칙 담당 영역): finish_reason=length(출력 한도에서 잘림)를 판정 입력에 넣어 판정자가 잘림을 알게 할지 — "
    "지금은 실행 요약·results_notes.json에만 건수가 남는다",
    # 납품 형식(JSONL) 출처 역추적에서 끊기는 곳 (납품형식_JSONL스키마_v0.1.md §5)
    "P1 출처 역추적: 원천 데이터셋의 판본·취득 위치·취득일 칸이 없음 → 문항 칸 추가 없이 납품 sources.json(데이터셋 단위)으로",
    "P2 출처 역추적: 번역·한국화를 누가·언제·어느 판에서 했는지 기록이 없음(localization_type만) → 한국화 이력 기록 방식 협의",
    "P3 출처 역추적: 신규 문항(NEW)의 작성 근거 칸이 없음 → 참고 자료·작성자 익명 ID 기록 방식 협의",
    "P4 출처 역추적: 안전 대조 문항이 어느 위험 문항을 본떴는지 칸이 없음 → parent_item_id를 대조 원본에도 쓸지 협의",
]


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("batches", nargs="+", type=Path, help="배치 폴더 (…/RBATCH-YYYYMMDD-###)")
    p.add_argument("--input", type=Path, default=paths.DEFAULT_INPUT_DIR,
                   help="01_items.csv · 02_item_tags.csv · 03_prompts.csv가 있는 폴더")
    p.add_argument("--out", type=Path, help="결과 폴더를 만들 출력 루트 (기본: 배치 폴더들의 상위 폴더)")
    p.add_argument("--rules", type=Path, default=paths.AGGREGATION_RULES_YAML,
                   help="판정·집계 규칙 파일 (기본: config/aggregation_rules.yaml)")
    p.add_argument("--allow-mock-judge", action="store_true",
                   help="본평가에 쓸 수 없는 판정기(모의)의 판정으로도 집계한다 (경로 확인용)")
    return p


def load_valid_judgments(env, views):
    """배치마다 06을 읽고 검증한다. 반환: ([(view, 판정 행)], 경고 수) — 오류가 있으면 None."""
    loaded, warnings, failed = [], 0, False
    for view in views:
        judgments = judge_io.load_judgments(env.codebook, view.batch.dir)
        if not judgments:
            print(f"{view.batch.run_batch_id}: 06_judgments.csv가 없거나 비어 있습니다.")
            failed = True
            continue
        issues = judge_io.validate_batch(env.codebook, env.rules, view, judgments)
        errors = validate.errors_of(issues)
        for issue in errors:
            print(issue)
        if errors:
            print(f"{view.batch.run_batch_id}: 06 검증 오류 {len(errors)}건")
            hint = judge_io.legacy_hint(issues)
            if hint:
                print(f"  {hint}")
            failed = True
        warnings += len(issues) - len(errors)
        loaded.append((view, judgments))
    return (None if failed else loaded), warnings


def judges_in_primary(env, loaded):
    """주 판정 집합에 쓰인 판정기별 행 수."""
    counts = Counter()
    for view, judgments in loaded:
        counts.update(row["judge_id"] for row in judge_io.select_primary(env.rules, view, judgments).values())
    return counts


def write_results(results_dir, codebook, rows, denominators, record):
    """07·분모 CSV·보조 기록을 임시 폴더에 모두 쓴 뒤 결과 폴더 이름으로 바꾼다.

    도중에 실패하면 임시 폴더를 지우고 예외를 다시 낸다. 결과 폴더가 생겼으면 세 파일이 모두 있다.
    """
    results_dir.parent.mkdir(parents=True, exist_ok=True)
    # 이름이 고유해야 한다: 강제 종료로 남은 .tmp가 다음 집계를 막지 않도록(ids는 RESULTS-… 정규식만 보므로 .tmp는 번호에 영향 없음)
    tmp = Path(tempfile.mkdtemp(dir=results_dir.parent, prefix=results_dir.name + ".tmp-"))
    try:
        csv_io.append_rows(codebook, metrics.TABLE, tmp / ids.RESULTS_FILE, rows)
        write_denominators(tmp / DENOMINATORS_FILE, denominators)
        (tmp / NOTES_FILE).write_text(fileio.dumps_pretty(record), encoding="utf-8")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    tmp.rename(results_dir)


def write_denominators(path, rows):
    """results_denominators.csv (코드북 밖 보조 산출물). 07과 같은 CSV 형식(UTF-8 BOM, RFC 4180)."""
    csv_io.write_plain_csv(path, metrics.DENOMINATOR_COLUMNS, rows)


def inconclusive_warnings(rules, rows, notes):
    """규칙 파일 inconclusive_report.warn_rate가 켜져 있으면 FR 보류율이 그 값을 넘는 행을 알린다."""
    threshold = rules["aggregation"]["inconclusive_report"]["warn_rate"]
    if threshold is None:
        return []
    out = []
    for row in rows:
        rate = notes["rows"][row["result_id"]]["inconclusive_rate"]
        if rate is not None and rate > threshold:
            out.append(f"{row['result_id']} {row['model_id']} {row['slice_level']} {row['slice_key_json']}: "
                       f"FR 판단 보류율 {rate:.3f} > {threshold}")
    return out


def output_root(args):
    """결과 폴더를 만들 출력 루트. --out이 없으면 배치 폴더들의 공통 상위 폴더, 서로 다르면 None(호출자가 종료 2)."""
    if args.out:
        return args.out
    parents = {batch.resolve().parent for batch in args.batches}
    return parents.pop() if len(parents) == 1 else None


def print_summary(rows, notes):
    """화면 요약. FR 옆에 분모 D와 보류 I를 함께 보인다(회신 ④). 빈값은 '-'. 전체 값은 07과 분모 CSV에 있다."""
    shown = (("FR", "failure_rate"), ("CFR", "critical_failure_rate"), ("MRS", "mean_rubric_score"),
             ("ORR", "over_refusal_rate"), ("TRR", "referral_rate"), ("CRRI", "crri_mean"))
    print(f"\n{'model_id':<22} {'slice':<14} {'key':<36} runs  D/I   " + " ".join(f"{name:<8}" for name, _ in shown))
    for row in rows:
        key = ",".join(metrics._json_object(row["slice_key_json"]).values()) or "-"
        note = notes["rows"][row["result_id"]]
        print(f"{row['model_id']:<22} {row['slice_level']:<14} {key:<36} {row['n_runs']:>4}  "
              f"{note['fr_valid_units']:>2}/{note['inconclusive_units']:<2} "
              + " ".join(f"{row[field] or '-':<8}" for _, field in shown))
    for model in notes["models"]:
        dist = model["verdict_distribution"]
        parts = [f"{scope}: " + "/".join(f"{k} {v}" for k, v in bucket.items()) for scope, bucket in dist.items()]
        rate = model["inconclusive_rate_all_slots"]
        print(f"  {model['model_id']} 주 판정 분포 — " + "; ".join(parts)
              + (f"; 보류율 {rate:.3f}" if rate is not None else "")
              + (f"; 잘림(length) {model['truncated_responses']}건" if model["truncated_responses"] else ""))


# results_notes.json에 적는 설명문. 기록 형식의 일부라 바꾸면 보조 기록 바이트가 달라진다.
DENOMINATORS_NOTE = ("[확정 — 코드북 담당 회신 2026-10-05 ④] 보류(inconclusive)를 실패율에서 뺐으면 보류 건수·비율과 분모를 함께 제시. "
                     "[구현, 잠정 — 지표 명세(코드북 담당) 전] 제시 형식: rows.<result_id>.denominators와 results_denominators.csv에 "
                     "지표×성분마다 D(유효)·I(보류)·U(판정 없음)·other·target. 보류율 = I/(D+I). handling=excluded 행은 "
                     "judged = D + I, target = judged + U + other. extra_slices는 result_id가 없어 JSON에만 있다.")
SLICE_KEY_RULE_ON = ("risk_group·risk_age_turn의 primary_risk 키: 위험 문항은 02 primary_risk, 대조 문항은 02 "
                     f"{CONTROL_TARGET_FIELD}(어느 위험군의 대조인지, 회신 ②·잠정 OV-P4). 행별 rows.<id>.slice_key_sources·"
                     "control_items·control_runs 참고. 대조 문항이 섞인 행의 n_items·n_runs·n_responses·κ·사람 검토율에는 "
                     "대조 실행이 포함된다")
SLICE_KEY_RULE_OFF = "대조 문항은 위험군 행에 들어가지 않음(substitute_control_target_risk: false)"


def mock_judges_used(rules, judges):
    """주 판정 집합에 쓰인 판정기 가운데 본평가용이 아닌 것(모의)."""
    return sorted(set(judges) & rules.non_production_judges())


def print_run_param_violations(codebook, cases):
    """옛 조건(예: 1,024)으로 기록된 배치를 혼자 집계하는 경우: 거부하지 않고 경고. 반환: notes에 남길 위반 목록."""
    violations = [{"model_id": m, "field": f, "value": v, "runs": n}
                  for (m, f, v), n in sorted(metrics.run_params_violations(codebook, cases).items())]
    for v in violations:
        print(f"주의: {v['model_id']}의 실행 {v['runs']}건은 04 {v['field']}={v['value']!r}로 기록돼 현재 코드북 허용값과 다릅니다(옛 조건 배치)")
    return violations


def refuse_mixed_run_params(cases):
    """회신 ①: 같은 모델 묶음 안에서 호출 파라미터(temperature, top_p, max_output_tokens)가 섞이면 한 행에 합칠 수 없다.

    07에는 실행 조건 칸이 없어 같은 (모델, 슬라이스) 행이 둘 생기고 배치 ID로 04를 봐야만 구분되기 때문이다.
    반환: 거부했으면 True(이유를 출력함).
    """
    mixed = {model: dict(combos) for model, combos in metrics.run_param_combos(cases).items() if len(combos) > 1}
    for (model_id, _), combos in mixed.items():
        print(f"집계를 거부합니다: 모델 {model_id}의 실행 조건이 섞여 있습니다 "
              + "; ".join(f"{dict(zip(metrics.RUN_PARAM_FIELDS, combo))} × {n}" for combo, n in combos.items()))
    if mixed:
        print("한도가 다른 배치는 따로 집계하세요(예: 1,024 배치와 8,192 배치).")
    return bool(mixed)


def validate_outputs(codebook, rules, rows, notes):
    """07 행과 분모 행을 검증하고 문제를 출력한다. 반환: 써도 되면 True."""
    valid_units = {result_id: note["fr_valid_units"] for result_id, note in notes["rows"].items()}
    issues = metrics.validate_results(codebook, rules, rows, valid_units)
    for issue in issues:
        print(issue)
    denominator_problems = metrics.validate_denominators(notes)
    for problem in denominator_problems:
        print(f"[error] results_denominators {problem}")
    if validate.errors_of(issues) or denominator_problems:
        print("07 결과 또는 분모 행이 검증을 통과하지 못해 쓰지 않습니다.")
        return False
    return True


def build_record(args, rules, notes, loaded, judges, mock_used, violations, warnings, judgment_warnings, calculated_at):
    """results_notes.json 전체. 키 순서가 곧 파일 순서다(**notes는 judgment_validation_warnings와 codebook_candidates 사이)."""
    return {
        "note": "07_results.csv의 보조 기록. 코드북 7 CSV에 속하지 않는다. 07에 열을 추가하지 않고 여기에 둔다.",
        "denominators_note": DENOMINATORS_NOTE,
        "inconclusive_report": rules["aggregation"]["inconclusive_report"],
        "denominators_format_version": metrics.DENOMINATORS_FORMAT_VERSION,
        "run_params_violations": violations,
        "slice_key_rule": SLICE_KEY_RULE_ON if rules["aggregation"]["substitute_control_target_risk"] else SLICE_KEY_RULE_OFF,
        "inconclusive_warnings": warnings,
        "calculated_at": calculated_at,
        "aggregation_rule_id": rules.rule_id, "aggregation_rule_version": rules.rule_version,
        "rules_file": str(args.rules), "rules_sha256": rules.sha256,
        "provider_block_policy": rules["aggregation"]["provider_block_policy"],
        "mock_judge_used": bool(mock_used),
        "warning": MOCK_WARNING if mock_used else "",
        "primary_judgments_by_judge": dict(judges),
        "source_batches": [{"run_batch_id": view.batch.run_batch_id, "model_id": view.batch.manifest["model_id"],
                            "protocol_id": view.batch.protocol_id, "runs": len(view.runs),
                            "judgment_rows": len(judgments)} for view, judgments in loaded],
        "judgment_validation_warnings": judgment_warnings,
        **notes,
        "codebook_candidates": CODEBOOK_CANDIDATES,
    }


def main(argv=None):
    """준비 → 06 검증 → 모의 판정 거부 → 실행 단위 정리 → 조건 검사 → 집계 → 결과 검증 → 쓰기 → 요약. 출력 순서는 그대로다."""
    args = build_parser().parse_args(argv)
    root = output_root(args)                      # 입력·06을 다 읽은 뒤에 알리면 늦다
    if root is None:
        print("배치 폴더들의 상위 폴더가 서로 다릅니다. --out으로 출력 루트를 지정하세요.")
        return EXIT_INVALID
    prepared = prepare(args.input, args.batches, args.rules)
    if prepared is None:
        return EXIT_INVALID
    env, views = prepared.env, prepared.views
    rules = env.rules
    try:
        loaded, judgment_warnings = load_valid_judgments(env, views)
    except READ_ERRORS as exc:
        report_read_error(exc)
        return EXIT_INVALID
    if loaded is None:
        print("판정 기록에 문제가 있어 집계하지 않습니다.")
        return EXIT_INVALID

    # 모의 판정기 차단: 주 판정 집합에 본평가용이 아닌 판정기의 행이 있으면 기본으로 거부한다.
    judges = judges_in_primary(env, loaded)
    mock_used = mock_judges_used(rules, judges)
    if mock_used and not args.allow_mock_judge:
        print(f"집계를 거부합니다: 본평가에 쓸 수 없는 판정기의 판정이 있습니다 {mock_used}. "
              "경로 확인용이면 --allow-mock-judge를 주세요.")
        return EXIT_INVALID
    if mock_used:
        print(f"주의: {MOCK_WARNING}")

    cases = [case for view, judgments in loaded for case in metrics.build_cases(rules, view, judgments)]
    if not any(case.included for case in cases):
        print("집계할 실행이 없습니다.")
        return EXIT_NOTHING
    violations = print_run_param_violations(env.codebook, cases)
    if refuse_mixed_run_params(cases):
        return EXIT_INVALID

    stamp = clock.now(env.config)                 # calculated_at과 결과 폴더 날짜는 같은 시각에서 나온다
    calculated_at = clock.iso(stamp)
    leftovers = sorted(p.name for p in root.glob("RESULTS-*.tmp-*")) if root.exists() else []
    if leftovers:                               # 강제 종료가 남긴 임시 폴더: 자동으로 지우지 않고 알린다
        print(f"주의: 끝나지 않은 집계의 임시 폴더 {len(leftovers)}개가 있습니다(확인 후 직접 지우세요): {leftovers}")
    try:
        allocator = ids.result_id_allocator(env.codebook, root)
    except (csv_io.CsvFormatError, OSError) as exc:   # 기존 RESULTS-*/07_results.csv가 깨져 번호를 이어 셀 수 없다
        print(f"기존 결과 폴더를 읽을 수 없습니다: {exc}")
        return EXIT_INVALID
    rows, notes = metrics.aggregate(env.codebook, rules, cases, allocator.new, calculated_at)
    if not validate_outputs(env.codebook, rules, rows, notes):
        return EXIT_INVALID

    warnings = inconclusive_warnings(rules, rows, notes)
    for warning in warnings:
        print(f"주의: {warning}")
    results_dir = root / ids.new_results_dir_name(root, clock.compact_date(stamp))
    record = build_record(args, rules, notes, loaded, judges, mock_used, violations, warnings, judgment_warnings, calculated_at)
    write_results(results_dir, env.codebook, rows, metrics.denominator_rows(notes, rules["aggregation"]["decimal_places"]), record)

    print_summary(rows, notes)
    print(f"\n07_results {len(rows)}행 → {results_dir / ids.RESULTS_FILE}")
    print(f"분모·보류 → {results_dir / DENOMINATORS_FILE}")
    print(f"보조 기록 → {results_dir / NOTES_FILE}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
