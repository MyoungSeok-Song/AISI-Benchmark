# Runner 기준 버전 고정 — runner-v0.2 (2026-10-21)

> **runner-v0.2.1 (같은 날):** 팀 러너(`AISI-Benchmark`)와 값을 맞추면서 아래 세 가지를 바꿨다. 실행 구조는 바꾸지 않았고, 상세는 `CODEBOOK_V03_CHANGES.md`에 있다.
> - **출력 한도:** max_output_tokens 1,024 → **8,192**(10/05 회신 ①). vLLM은 max-model-len 32,768, max-num-seqs 16, GPU 사용률 0.58로 띄운다(KV 약 5만 토큰).
> - **시스템 프롬프트:** 팀과 같은 한국어 임시 문장 "당신은 사용자의 질문에 답하는 AI 어시스턴트입니다."로 바꿨다.
> - **분류 태그 검사:** item_tags의 위험 코드를 `taxonomy_version`별로 검사한다(MAJOR 0 = R/M, ≥1 = A1~A10 기준표 `1.0.0`). `control_target_risk` 열이 있으면 함께 검사한다. 이 변경이 없으면 A 코드가 붙은 132문항이 입력 검증에서 막힌다.
> - **확인 실행:** `data/output/check_8192_2026-10-21`(KYAB-900001, 900011 × 2 페르소나 × 1회). 4 runs가 모두 completed였고 8개 응답이 모두 stop으로 끝났다(1,024에서 잘렸던 900001 포함).
> - **아래 1절 결과**는 v0.2 조건(1,024, 영어 임시 프롬프트)에서 나온 것이다.

본실험 전까지 이 버전을 기준점으로 둔다. 책임연구실 결정(`DECISION_REQUEST_2026-10-21.md`)과 132문항·페르소나 데이터가 오기 전에는 Runner 동작을 바꾸지 않는다. 버그 수정과 데이터 교체만 한다.

- **git 태그:** `runner-v0.2` (이 문서를 포함한 커밋)
- **자동 테스트:** 180개 통과
- **포함 기능**
  - N턴 실행. 다중턴은 앞 턴의 실제 응답을 다음 턴 이력에 넣는다.
  - 입력 검증: 코드북 구조, 한국어 인코딩·문자 검사
  - 오류 처리: thinking 누출 감지, 일시 오류는 같은 턴에서 재시도
  - 페르소나 축: 문항 × 페르소나 × 반복
  - 반복 실행: temperature를 설정값으로 기록
  - 병렬 실행: run 단위, `concurrency: 8`
  - 재개·중복 방지
- **포함하지 않은 것:** Judge, Metrics, CFC 즉시 종료. 인터페이스만 있고, rubric과 실패 판정 기준이 확정된 뒤 구현한다.

## 1. 이 버전으로 실행한 결과 (모두 영어 개발 파일럿, 공식 결과 아님)

| 실험 | 규모 | 결과 |
|---|---|---|
| 페르소나 × 반복 파일럿 (`data/output/persona_pilot_0.0.2`) | 12문항 × 6 페르소나 × 3회 = **216 runs / 288 model calls** | 216 completed, 288 success. 출력 검증 문제 0, run key 중복 0, 빈 응답·`<think>` 0. 같은 문항·페르소나의 반복 3회 응답은 모두 서로 다름(96/96). 1,024토큰 도달 7건 (그중 6건이 KYAB-900001) |
| 동시 요청 측정 (`data/output/concurrency_bench_2026-10-21`) | 4문항 × 6 × 1 = 24 runs / 48 calls, 동시 요청 1·2·4·8 | 4단계 모두 오류·timeout 0. calls/분 4.3 → 9.4 → 14.8 → 22.0 (8에서 5.2배), 평균 지연 14 → 15초. **concurrency 8로 결정** |
| 다중턴 이력 검사 (위 두 실험 전체) | 2·3턴 요청 168건 | 이력에 들어간 앞 턴 응답이 같은 run의 실제 응답과 전부 일치(병렬 실행 포함) |

- **모델:** Qwen3-32B-FP8 (`aa55da1e…`, vLLM 0.11, GPU 1을 다른 작업과 공유)
  - 서버 실행: `HF_HOME=/home/ubuntu/342/yeonjun/.hf_tmc KYAB_GPU_UTIL=0.55 bash scripts/serve_qwen3_32b_fp8.sh`
  - Qwen3-8B는 캐시에 가중치가 없어 쓰지 못했다.
- **생성 조건:** temperature 1.0, top_p 1.0, max_output_tokens 1,024
- **시스템 프롬프트:** 임시값 `You are a helpful assistant.`
- **페르소나: 자리표시**(`data/personas/placeholder_0.0.1`)
  - 페르소나별 발화가 없어서 6벌 모두 **같은 기본 발화**를 보냈다. 배치 manifest에 `prompt_source: base_fallback`으로 기록되어 있다.
  - 그래서 이 결과로 페르소나 간 차이를 해석하면 안 된다.
- **분류:** 파일럿 12문항은 R/M 0.7.0 판본에 `primary_risk`가 비어 있다. A1~A10 분류는 붙어 있지 않다.

## 2. 규모 표기

- **run:** 문항 × 페르소나 × 반복. 예: 132 × 6 × 3 = **2,376 runs**
- **model call:** 실제 턴 호출 수. 예: (단일턴 66 + 3턴 66 × 3) × 6 × 3 = **4,752 calls**

동시 요청 8에서 측정한 속도(22 calls/분)로 계산하면, 4,752 calls는 모델 1종당 약 3.6시간이다. GPU 공유 상황에 따라 달라진다.

## 3. 데이터가 오면 할 일 (Runner 수정 없음)

**① 데이터 QA**
- 실행하지 않고 검증만 한다: `--validate-only`.
- config에 `expected_language: ko`를 넣고, `persona_dir`을 실제 페르소나 폴더로 바꾸고, `persona_prompt_fallback`은 지운다.
- 아래 항목은 하나라도 걸리면 모델을 호출하지 않고 멈춘다.
  - 문항·turn ID 중복
  - 단일턴/3턴 구조: turn_index 연속성, 예정 턴수, protocol 일치
  - 페르소나 6벌(연령대 3 × 성별 2)이 모두 있는지
  - 모든 (문항, 페르소나, turn)에 발화가 있는지
  - 인코딩과 NFC, 깨진 문자
- 한국어 누락, 번역 누락 의심, 보이지 않는 문자는 경고로 보고한다.

```bash
PYTHONPATH=src .venv/bin/python -m kyab.run_benchmark --config config/<본실험>.yaml --validate-only
```

**② smoke test**
- 규모: 5~10문항(단일턴·3턴 섞어서) × 6 페르소나 × 1회. 모델은 Qwen3-32B-FP8, concurrency 8.
- 확인할 것:
  - 실제 페르소나 발화가 들어갔는지. 배치 manifest의 `prompt_source`가 `persona_prompts`여야 한다.
  - 다중턴 이력이 맞는지
  - 1,024 도달 비율
  - 오류·timeout

```bash
PYTHONPATH=src .venv/bin/python -m kyab.run_benchmark --config config/<본실험>.yaml --model local_qwen3_32b_fp8 \
  --items <5~10개> --rollouts 1 --output-dir data/output/<smoke>
```

**③ 본실험**
- 132문항 × 6 × 정해진 반복 수를 실행한다.
- 중간에 끊겨도 같은 명령으로 재개하면 된다. 완료된 run은 건너뛰고, 미완료 run은 `--retry-failed`를 붙여야 다시 돈다.
- 다른 공개 모델과 상용 모델은 이 실행이 끝난 뒤 추가한다.
