#!/bin/bash
# 과제 5 종단 시험: 10-02 배치 001~004(Kanana·27B) 복사본 + 모의 실패 계획 배치 2개
#   모의 판정 → 06 검증 → apply_judgments → run_aggregate (count_as_refusal / exclude 비교)
# 원본 samples/output은 건드리지 않는다. runner/ 폴더에서 실행: bash tools/e2e_judge_aggregate.sh  (결과: var/e2e_task5/, git 제외)
set -euo pipefail
PY=.venv/bin/python
E2E=var/e2e_task5
SRC=samples/output

before=$(cat $SRC/RBATCH-20261002-00{1,2,3,4}/*.csv | md5sum)
rm -rf $E2E && mkdir -p $E2E
for b in 001 002 003 004; do cp -r $SRC/RBATCH-20261002-$b $E2E/; done

echo "== 1. 모의 실패 계획 배치 (차단·오류 포함) =="
$PY -m kyab_runner.run_single    --allow-unverified --out $E2E --mock-plan samples/mock_plan_failures.yaml | tail -5
$PY -m kyab_runner.run_multiturn --allow-unverified --out $E2E --mock-plan samples/mock_plan_failures.yaml | tail -5

echo "== 2. 모의 판정 =="
$PY -m kyab_runner.run_judge $E2E/RBATCH-*
echo "== 3. 06 검증 =="
$PY -m kyab_runner.run_judge --validate-only $E2E/RBATCH-*
echo "== 4. apply_judgments =="
$PY tools/apply_judgments.py $E2E/RBATCH-*
echo "== 5. 집계 (모의 판정 거부 확인 → 허용) =="
$PY -m kyab_runner.run_aggregate $E2E/RBATCH-* && exit 1 || echo "(거부됨: 종료 코드 $?)"
$PY -m kyab_runner.run_aggregate --allow-mock-judge $E2E/RBATCH-*
echo "== 6. 비교: provider_block_policy=exclude =="
current=$(sed -n 's/^aggregation_rule_version: //p' config/aggregation_rules.yaml)
variant="${current%.*}.$(( ${current##*.} + 1 ))"          # 현재 판본의 PATCH + 1
sed -e "s/^aggregation_rule_version: $current/aggregation_rule_version: $variant/" \
    -e 's/^  provider_block_policy: count_as_refusal/  provider_block_policy: exclude/' \
    config/aggregation_rules.yaml > $E2E/rules_exclude.yaml
grep -q "^aggregation_rule_version: $variant" $E2E/rules_exclude.yaml || { echo "비교용 규칙 판본 치환 실패"; exit 1; }
grep -q "^  provider_block_policy: exclude" $E2E/rules_exclude.yaml || { echo "비교용 정책 치환 실패"; exit 1; }
$PY -m kyab_runner.run_aggregate --allow-mock-judge --rules $E2E/rules_exclude.yaml $E2E/RBATCH-*

after=$(cat $SRC/RBATCH-20261002-00{1,2,3,4}/*.csv | md5sum)
[ "$before" = "$after" ] && echo "원본 samples/output 001~004 CSV 무변경 확인" || { echo "원본이 바뀌었다!"; exit 1; }
