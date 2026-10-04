#!/bin/bash
# 판정·집계 종단 시험 (과제 5·6).
#   모의 배치(ST1, MT3, 실패 계획 ST1·MT3 — 출력 한도 8,192 조건) → 모의 판정 → 06 검증 → apply_judgments
#   → run_aggregate(모의 판정 거부 확인 → 허용) → 차단 정책 비교(count_as_refusal / exclude) → 결과 확인.
# runner/ 폴더에서 실행:  bash tools/e2e_judge_aggregate.sh            (결과: var/e2e_task6/, git 제외)
# 실모델 배치를 함께 넣으려면 폴더를 인자(또는 E2E_SOURCE_BATCHES, 공백 구분)로 준다. 복사해서 쓰므로 원본은 바뀌지 않는다.
#   bash tools/e2e_judge_aggregate.sh samples/output/RBATCH-2026MMDD-001 samples/output/RBATCH-2026MMDD-002
# 1,024 한도로 기록된 옛 배치(2026-10-02 001~004)는 overlay OV-R1005-1(허용값 8192) 때문에 apply 단계에서 거부되므로
# 기본값에서 뺐다. GPU 재실행 뒤 8,192 조건의 새 배치를 인자로 넣는다.
set -euo pipefail
PY=.venv/bin/python
E2E=${E2E_DIR:-var/e2e_task6}
SOURCES=("$@")
if [ ${#SOURCES[@]} -eq 0 ] && [ -n "${E2E_SOURCE_BATCHES:-}" ]; then read -r -a SOURCES <<< "$E2E_SOURCE_BATCHES"; fi

rm -rf "$E2E" && mkdir -p "$E2E"
before=""
for src in "${SOURCES[@]}"; do
  before+=$(cat "$src"/*.csv | md5sum)
  cp -r "$src" "$E2E"/
done
echo "== 0. 실모델 배치 복사: ${#SOURCES[@]}개 =="

echo "== 1. 모의 배치 (정상 2 + 실패 계획 2: 차단·오류·시간초과·빈 응답·절단) =="
$PY -m kyab_runner.run_single    --allow-unverified --out "$E2E" | tail -3
$PY -m kyab_runner.run_multiturn --allow-unverified --out "$E2E" | tail -3
$PY -m kyab_runner.run_single    --allow-unverified --out "$E2E" --mock-plan samples/mock_plan_failures.yaml | tail -4
$PY -m kyab_runner.run_multiturn --allow-unverified --out "$E2E" --mock-plan samples/mock_plan_failures.yaml | tail -4

echo "== 2. 모의 판정 =="
$PY -m kyab_runner.run_judge "$E2E"/RBATCH-*
echo "== 3. 06 검증 =="
$PY -m kyab_runner.run_judge --validate-only "$E2E"/RBATCH-*
echo "== 4. apply_judgments =="
$PY tools/apply_judgments.py "$E2E"/RBATCH-*
echo "== 5. 집계 (모의 판정 거부 확인 → 허용) =="
set +e; $PY -m kyab_runner.run_aggregate "$E2E"/RBATCH-*; code=$?; set -e
[ "$code" -eq 2 ] && echo "(거부됨: 종료 코드 2)" || { echo "모의 판정 거부가 동작하지 않음 (종료 코드 $code)"; exit 1; }
$PY -m kyab_runner.run_aggregate --allow-mock-judge "$E2E"/RBATCH-*
echo "== 6. 비교: provider_block_policy=exclude =="
current=$(sed -n 's/^aggregation_rule_version: //p' config/aggregation_rules.yaml)
variant="${current%.*}.$(( ${current##*.} + 1 ))"          # 현재 판본의 PATCH + 1
sed -e "s/^aggregation_rule_version: $current/aggregation_rule_version: $variant/" \
    -e 's/^  provider_block_policy: count_as_refusal/  provider_block_policy: exclude/' \
    config/aggregation_rules.yaml > "$E2E"/rules_exclude.yaml
grep -q "^aggregation_rule_version: $variant" "$E2E"/rules_exclude.yaml || { echo "비교용 규칙 판본 치환 실패"; exit 1; }
grep -q "^  provider_block_policy: exclude" "$E2E"/rules_exclude.yaml || { echo "비교용 정책 치환 실패"; exit 1; }
$PY -m kyab_runner.run_aggregate --allow-mock-judge --rules "$E2E"/rules_exclude.yaml "$E2E"/RBATCH-* | tail -4

echo "== 7. 결과 확인 =="
$PY - "$E2E" "$current" "$variant" <<'PYEOF'
import csv, glob, json, sys
e2e, current, variant = sys.argv[1:4]
sys.path.insert(0, ".")
from kyab_runner import csv_io, judge_io, metrics
from kyab_runner.context import load_environment, open_views
env = load_environment()
batches = sorted(glob.glob(f"{e2e}/RBATCH-*"))
_, views, _ = open_views(env, "samples/input", batches)
blank = finished = rows06 = errors = 0
for view in views:
    rows = judge_io.load_judgments(env.codebook, view.batch.dir)
    rows06 += len(rows)
    finished += sum(1 for r in rows if not env.rules.is_unfinished(r))
    blank += sum(1 for r in rows if not env.rules.is_unfinished(r) and r["critical_failure_code"] == "")
    errors += sum(1 for i in judge_io.validate_judgments(env.codebook, env.rules, view, rows) if i.level == "error")
print(f"06: {rows06}행, 완료 행 {finished}건 중 CFC 빈칸 {blank}건, 검증 오류 {errors}건")
assert blank == 0 and errors == 0
results = sorted(glob.glob(f"{e2e}/RESULTS-*"))
assert len(results) == 2, results
versions = []
for d in results:
    rows = csv_io.read_table(env.codebook, "07_results", f"{d}/07_results.csv")
    notes = json.load(open(f"{d}/results_notes.json", encoding="utf-8"))
    violations = sum(len(env.codebook.check_row("07_results", r)) for r in rows)
    issues = metrics.validate_results(env.codebook, env.rules, rows, {k: v["fr_valid_units"] for k, v in notes["rows"].items()})
    denominators = list(csv.DictReader(open(f"{d}/results_denominators.csv", encoding="utf-8-sig", newline="")))
    one_to_one = {r["result_id"] for r in denominators} == {r["result_id"] for r in rows}
    orr_rows = [r for r in rows if r["slice_level"] == "risk_group" and r["over_refusal_rate"] != ""]
    versions.append(rows[0]["aggregation_rule_version"])
    print(f"{d.split('/')[-1]}: 07 {len(rows)}행 코드북 위반 {violations} 검증 문제 {len(issues)} | 분모 {len(denominators)}행 result_id 1:1 {one_to_one} "
          f"| risk_group 행 중 ORR 있음 {len(orr_rows)} | 정책 {notes['provider_block_policy']} 규칙 {rows[0]['aggregation_rule_version']} | 모의 {notes['mock_judge_used']}")
    assert violations == 0 and not issues and one_to_one and orr_rows and metrics.validate_denominators(notes) == []
assert versions == [current, variant], versions
print("e2e 확인 통과")
PYEOF

if [ ${#SOURCES[@]} -gt 0 ]; then
  after=""; for src in "${SOURCES[@]}"; do after+=$(cat "$src"/*.csv | md5sum); done
  [ "$before" = "$after" ] && echo "원본 배치 CSV 무변경 확인" || { echo "원본이 바뀌었다!"; exit 1; }
fi
