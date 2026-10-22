# youth_ai_benchmark — 청소년 AI 위험 벤치마크 평가 실행기 (1차)

> **2026-10-22 저장소 교체:** 이 브랜치의 코드는 `youth_ai_benchmark` 러너(태그 `runner-v0.2.2` 상당)로 바뀌었다.
> 페르소나 6벌 × 반복, temperature 설정값(1.0), 병렬 실행·재개, max_output_tokens 8,192, A1~A10 태그 검사, 코드북 v0.3(안) 생성기가 들어 있다.
> 이전 `kyab_runner`(판정 입출력·집계·납품 변환 포함)는 커밋 `0056483`에 그대로 남아 있다: `git checkout 0056483 -- kyab_runner tools`.
> 원천 데이터 전체(`data/raw`), 실행 결과(`data/output`), 보고 문서(`reports`)는 올리지 않았다. 파일럿 12문항은 출처·라이선스(CAREBench CC-BY-4.0, MinorBench MIT)를 `items.csv`에 기록했다.


ETRI 7-CSV 코드북 v0.2와 ER 모델(v0.2), 수행계획서 v1.1을 기준으로 만든 **평가 데이터 → Target LLM → 실행 기록** 파이프라인이다.

```
원천 데이터 (CAREBench / MinorBench / 다중턴 JSON)
   └─ adapters/ ─→ items.csv · item_tags.csv · prompts.csv   (코드북 01·02·03)
                      └─ validator (모델 호출 전 검증)
                           └─ runner (N-turn 대화, 턴마다 저장)
                                └─ runs.csv · responses.csv   (코드북 04·05)
                                     └─ [추후] judge → judgments.csv → results.csv  (06·07, 인터페이스만)
```

한국화 데이터가 오면 **실행기는 그대로 두고 `data/<dataset>/`의 CSV만 바꿔 끼운다.**

## 1. 설치

```bash
cd /home/ubuntu/342/yeonjun/youth_ai_benchmark
python3 -m venv --without-pip .venv && curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements.txt
```
(이 서버에는 `python3-venv`가 없어서 `--without-pip` + get-pip로 만든다. `.venv`는 이미 설치되어 있다.)

## 2. 실행

```bash
export PYTHONPATH=src
# (1) 파일럿 데이터 재생성 — data/raw → data/pilot_0.0.2
.venv/bin/python scripts/build_pilot.py
# (2) 검증만 (모델 호출 없음)
.venv/bin/python -m kyab.run_benchmark --config config/pilot.yaml --validate-only
# (3) Mock 실행
.venv/bin/python -m kyab.run_benchmark --config config/pilot.yaml --model mock
# (4) 오류 경로 확인 (KYAB-900012 2턴에서 timeout 주입)
.venv/bin/python -m kyab.run_benchmark --config config/pilot.yaml --model mock_faults --protocols MT3-1.0.0 --output-dir data/output/pilot_0.0.2_faults
# (5) 테스트
.venv/bin/python -m pytest -q
# (6) 한국어 입력 정리 — 인코딩(CP949·BOM)·NFC 오류가 나면 원본은 두고 정리된 사본을 만든다
.venv/bin/python scripts/normalize_inputs.py <원본 폴더> <새 폴더>
```

옵션: `--protocols ST1-1.0.0,MT3-1.0.0`, `--items KYAB-900001,...`, `--rollouts 3`, `--rollout-start 4`(추가 반복을 4번부터 이어 붙임), `--personas PERSONA-MID-F,...`, `--concurrency 4`(동시 실행 run 수), `--retry-failed`, `--output-dir`.

실제 모델은 `config/models.yaml`에 공식 버전을 적은 뒤 `--model <model_id>`로 실행한다. `model_version: TBD`인 모델은 실행을 거부한다. API 키는 `.env`에 `OPENAI_API_KEY=...`처럼 넣고, yaml에는 환경변수 이름만 쓴다. 로컬 모델은 OpenAI 호환 vLLM 서버를 평가하는 동안만 띄운다.

## 3. 폴더

| 경로 | 내용 |
|---|---|
| `docs/` | 코드북 v0.2(xlsx), ER 모델(png), 수행계획서 v1.1(pdf) — 원본은 `../korean_benchmark/` |
| `config/pilot.yaml` | 실행 설정 (데이터셋·버전·생성 파라미터·허용 조건) |
| `config/models.yaml` | 평가 대상 모델 등록 (mock, mock_faults, API·로컬 자리) |
| `config/system_prompt.txt` | 시스템 프롬프트 — **임시값**, 협의 필요 |
| `data/raw/` | 원천 snapshot + `SOURCE.yaml` (HF 커밋, 라이선스, SHA-256) |
| `data/pilot_0.0.2/` | **현행** 표준 CSV + `manifest.yaml` + `CONVERSION_REPORT.md` |
| `data/taxonomy/` | 분류 기준표(판본별 CSV)와 R/M→A crosswalk, `manifest.yaml`. `scripts/build_taxonomy_tables.py`로 수연팀 xlsx `코드북49`에서 생성. **초안(0.9.0-draft)**, item_tags 검증기에는 아직 미연결 |
| `data/pilot/` | dataset 0.0.1 (코드북 v0.2 protocol_id) — 과거 로그 보존용, 새 실행 불가 |
| `data/output/pilot_0.0.2/` | 현행 Mock 실행 결과 `runs.csv`, `responses.csv`, `batches/RBATCH-*.yaml` |
| `data/output/pilot_0.0.2_faults/` | 현행 오류 주입 실행 결과 (과거 0.0.1 로그는 `data/output/README.md` 참조) |
| `src/kyab/schemas.py` | 7개 CSV의 전체 컬럼·필수성·AI 전달·허용값 (코드북 그대로, 테스트로 xlsx와 대조) |
| `src/kyab/validator.py` | 입력·출력 무결성 검사 |
| `src/kyab/runner/conversation.py` | N-turn 대화 실행 |
| `src/kyab/models/` | `base`(공통 인터페이스), `mock`, `openai_compat`(OpenAI API·vLLM), `registry` |
| `src/kyab/adapters/` | `carebench`, `minorbench`, `upstream_json`(다중턴 JSON) |
| `src/kyab/judge/base.py` | Phase 2·3 인터페이스 (미구현) |
| `src/kyab/redact.py` | 저장 전 비밀정보 제거 |
| `config/pilot_persona_qwen.yaml` | 페르소나 6벌 × 반복 3회 × temperature 1.0 구조 확인용 (로컬 Qwen, 영어 파일럿) |
| `data/personas/` | 페르소나 기준표와 페르소나별 발화(확장 CSV). 현재 자리표시만 있음 |
| `config/pilot_live.yaml` | 실모델 첫 연결 확인용 (4문항) |
| `STRUCTURE_REVIEW.md` | 코드북 구조 검토·협의사항 |

## 4. 동작 규칙

- **스키마**: 7개 CSV 모두 코드북 컬럼을 순서까지 그대로 쓴다 (items 27, item_tags 29, prompts 7, runs 28, responses 11, judgments 29, results 35). runs의 `persona_id` 추가와 `rollout_no`·`temperature` 규칙 완화는 v0.3 제안(`schemas.CODEBOOK_V03_PROPOSED`). 입력에 코드북에 없는 컬럼이 있어도 버리지 않는다.
- **검증 등급**:
  - `error`는 구조 오류다. 형식, PK·UQ, FK, turn_index 연속성, 예정 턴수, protocol 일치, current 태그 1개, dataset_version이 여기에 해당하고, 하나라도 있으면 모델을 호출하지 않는다.
  - `missing`은 필수 메타데이터 공란이다. 정식 설정에서는 실행을 막고, 개발 파일럿에서는 `allow_missing_metadata: true`일 때만 통과시킨다.
  - `item_review_status`는 `allowed_item_review_status`에 적힌 값만 실행한다.
- **한국어 입력 검증** (`kyab.text_qa`, 모델 호출 전):
  - UTF-8이 아닌 파일(CP949 등)은 읽기 전에 중단한다. UTF-8 BOM은 제거하고 경고만 남긴다.
  - `error`: 깨진 문자(U+FFFD), 제어 문자, 모델에 전달되는 필드의 NFC 아닌 텍스트(자모 분리 NFD 등).
  - `warning`: 보이지 않는 문자(zero-width·bidi·한글 채움 문자·NBSP, 이모지 ZWJ는 제외), mojibake 의심, 발화 앞뒤 공백, 4,000자 초과. 우회 표현일 수 있어 자동 제거하지 않는다.
  - config나 manifest에 `expected_language: ko`가 있으면 추가로 경고한다: 한글 없음, 한글 비중 30% 미만, 원문(`original_text`)과 같은 문장(번역 누락).
  - `scripts/normalize_inputs.py`는 인코딩과 NFC만 고친 사본을 새 폴더에 쓴다. 나머지는 사람이 판단한다.
- **모델 입력**: 실행기는 `ScriptTurn`(turn_id, turn_index, context_text, message_text)만 받는다. 태그, rubric, 기대·금지 응답, 원천 메타데이터는 구조적으로 모델에 전달될 수 없다. 이는 테스트의 sentinel 검사로 확인한다.
- **페르소나 × 반복** (2026-10 회의 결정 반영):
  - 실행 단위는 문항 × 페르소나 × 반복이다. config에 `persona_dir`이 있으면 `personas.csv`의 페르소나마다 같은 문항을 한 번씩 실행하고, 사용자 발화는 `persona_prompts.csv`의 페르소나별 변형으로 바꾼다. runs에 `persona_id`를 기록한다(페르소나 축이 없으면 `none`).
  - 페르소나별 발화가 아직 없으면 실행을 막는다. 개발 실행만 `persona_prompt_fallback: base`로 기본 발화를 쓰게 할 수 있고, 배치 manifest의 `personas.prompt_source`에 기록된다.
  - 현재 페르소나는 **자리표시**(`data/personas/placeholder_0.0.1/`, 6벌)다. 페르소나팀 자료가 오면 같은 형식으로 새 폴더에 넣고 `persona_dir`만 바꾼다.
  - `temperature`는 고정값이 아니라 설정값이다(반복 실행에는 0보다 커야 함). 제안값 1.0(`config/pilot_persona_qwen.yaml`). top_p 1.0과 max_output_tokens 1,024는 그대로다.
- **병렬 실행** (`--concurrency N`, config `concurrency`, 기본 1):
  - 병렬 단위는 run(문항 × 페르소나 × 반복)이다. 한 run 안의 턴은 앞 턴 응답이 다음 턴 이력에 들어가므로 항상 순차로 실행한다.
  - worker 스레드마다 모델 클라이언트를 따로 만든다. CSV 쓰기와 ID 발급은 프로세스 내부 잠금 뒤에 파일 잠금(flock)을 잡는다. 그래서 행이 섞이거나 ID가 겹치지 않고, NFS에서 flock 경합도 생기지 않는다.
  - 한 run에서 예외가 나도 다른 run은 계속 실행된다. 종료 코드는 1이고, 같은 명령으로 다시 실행하면 그 run만 돈다.
  - 순차 실행과 병렬 실행의 결과가 같다는 것을 `tests/test_parallel.py`로 확인한다. 비교 대상은 (문항, 페르소나, 반복, turn)별 요청·응답·상태의 집합이다.
- **재개·중복 방지** (`kyab.runner.schedule`):
  - 실행 전에 출력 폴더의 runs.csv를 읽는다.
  - 같은 run key(모델 × 데이터셋 × 문항 × protocol × 페르소나 × 반복)에 completed run이 있으면 건너뛴다.
  - 완료되지 않은 run(failed, partial, 중단된 running)은 `--retry-failed`를 줄 때만 새 run_id로 다시 실행한다. 이전 행은 감사 기록으로 남긴다.
  - 같은 key인데 생성 조건(temperature, top_p, max_output_tokens, 시스템 프롬프트, model_version)이 다르면 실행을 거부한다.
- **규모 표기**: "run" = 문항 × 페르소나 × 반복(예: 132 × 6 × 3 = 2,376 runs), "model call" = 실제 턴 호출 수(같은 예에서 단일턴 66 + 3턴 66×3 → 264 × 18 = 4,752 calls). 둘을 섞어 쓰지 않는다.
- `scripts/bench_concurrency.py`: 작은 subset으로 concurrency 단계별 처리량, 지연, 오류율, GPU 사용률을 측정한다.
- **다중턴**: `turn_index` 순서대로 진행한다. 사용자 발화는 대본 그대로 고정하고, 모델의 실제 응답은 다음 턴의 이력에 넣는다. 턴 수는 하드코딩하지 않아서 MT7·MT10도 같은 코드로 돌고, 이는 7턴 테스트로 확인한다.
- **저장**:
  - 응답은 턴마다 `responses.csv`에 append하고 fsync한다. 그다음에야 다음 턴으로 넘어간다.
  - `runs.csv`는 시작할 때 `running`으로 한 줄을 쓰고, 끝나면 최종 상태로 원자적으로 교체한다.
  - ID는 프로젝트 공용 `data/output/id_registry.json`(파일 잠금)에서 발급한다. 재시작해도, 출력 폴더가 달라도 중복되지 않는다.
- **상태**:
  - 모든 턴이 성공하면 `completed/planned_end`로 기록한다.
  - error·timeout·empty가 나면 거기서 멈추고 `partial` 또는 `failed`와 `error`로 기록한다.
  - 공급자 차단(blocked)이면 멈추고 `partial` 또는 `failed`와 `provider_block`으로 기록한다.
  - 모델이 스스로 거절한 응답은 `success`다.
- **`actual_turn_count`**: success 턴만 센다. blocked, empty, timeout, error는 완료 턴이 아니다. 정식 실행 전에 이 해석을 재확인해야 한다 (STRUCTURE_REVIEW C10).
- **Judge 필드**: `first_fail_turn`과 `first_cfc_turn`은 실행 시점에 비워 둔다. 판정 후에 후처리로 채운다(`judge.base.fill_run_failure_turns`).
- **배치**: 한 번 실행할 때 protocol마다 `run_batch_id`를 1개 발급한다. 배치마다 `batches/<id>.yaml`에 입력 해시, 설정, 모델 항목(키 제외), 환경, 명령어를 남긴다.
- **재현 정보**: `execution_library_version`에는 `kyab-runner-<버전>+src.<소스 해시 7자리>`를 기록한다. 이 폴더는 git 저장소가 아니다.

## 5. 이번 파일럿 데이터 (dataset_version 0.0.2)

- 0.0.1 → 0.0.2 변경점 (코드북 v0.3 합의, 2026-09-30):
  - protocol_id를 `ST1-1.0.0` / `MT3-1.0.0`으로 바꿨다.
  - item_version을 0.1.0 → 0.1.1로 올렸다 (PATCH, 문항 본문은 그대로).
  - `original_text`는 조건부, `intent_tag`는 선택으로 바꿨다.
  - 과거 0.0.1 데이터와 로그는 덮어쓰지 않고 보존한다. 구 표기 protocol_id는 판독만 되고, 그 데이터로 새로 실행할 수는 없다.

- CAREBench 5개와 MinorBench 5개는 단일턴(ST1-1.0.0) 영어 원문이다. 선택 규칙은 카테고리 정렬 순서에서 앞의 5개 카테고리를 고르고, 카테고리마다 ID가 가장 작은 문항 1개를 가져오는 것이다.
- 개발용 가상 3턴(MT3-1.0.0) 2개는 연구 문항이 아니라 이력 처리를 확인하기 위한 테스트 대본이다. `source_benchmark=NEW`로 표시했다.
- 개발용 ID 범위(`KYAB-900001~`, `TURN-90000001~`)는 임시로 정한 것이라 데이터팀과 협의해야 한다.
- **영어 개발용 데이터라 정식 한국어 평가 결과와 섞으면 안 된다.** `manifest.yaml`에 `not_for_official_evaluation: true`를 표시해 두었다.
- 원천에 없는 필수 메타데이터(연령대, 판정용 태그 등)는 추정하지 않고 비워 두었다. 목록은 `data/pilot_0.0.2/CONVERSION_REPORT.md`에 있다.

## 6. 상태 (2026-09-30)

**Evaluation Runner 1차 구현 및 Mock 기반 검증 완료.** 실제 LLM 실행과 평가(Judge)는 아직 하지 않았다.

| 완료 | 비고 |
|---|---|
| 스키마 구현 | 7개 CSV, 코드북 xlsx와 자동 대조. v0.3 합의 변경(`schemas.CODEBOOK_V03_PENDING`) 선반영 |
| 파일럿 변환 | 12문항 / 16발화 (CAREBench 5, MinorBench 5, 개발용 MT3 2) |
| Mock 실행 | runs 12, responses 16, 출력 검증 통과 |
| 단일턴 Runner | 완료 |
| 다중턴 Runner | N-turn, 7턴 테스트 포함 |
| 로그 저장 | 요청·원시 응답·실행 조건, 턴마다 fsync |
| 오류 처리 | timeout, error, blocked, empty, 클라이언트 예외 |
| 비밀정보 제거 | 오류 응답에 credential이 섞여 저장될 수 있는 문제를 확인했다. 그래서 저장 전에 공통 sanitizer(`redact.py`)를 거치게 했다. Authorization/Bearer, sk-/sk-ant-/AIza/hf_ 키 형태, `*_API_KEY`·`*_TOKEN` 환경변수 값을 가리고, 회귀 테스트를 추가했다. |
| 자동 테스트 | 105개 통과 (`data/output/pytest_result.txt`) |

| 미완료 | 비고 |
|---|---|
| 상용 API 실제 실행 | **보류 (예산 미확정, 2026-10-01)** — GPT-6 Luna(`gpt_6_luna`)·GPT-5 mini(`gpt_5_mini`) 설정 완료(2026-10-22 결정), API 키 대기 |
| 로컬 LLM 실제 실행 | **탐색적 파일럿 완료 (2026-10-01)** — Qwen3-8B, Qwen3-32B-FP8 각각 12문항 1회, 전부 success (`data/output/live_pilot_0.0.2/`, `PILOT_REPORT_QWEN.md`) |
| LLM-as-a-Judge | 미구현 (인터페이스만) |
| CFC 발생 시 즉시 종료 (online) | 미구현 — 프로토콜 결정 대기 |
| 사람 평가 | 미실행 |
| Metrics / CRRI | 미구현 |
| 최종 한국어 데이터 실행 | 미실행 |

**실모델 첫 실행 (승인 후):** `config/pilot_live.yaml`로 모델당 단일턴 2개 + 3턴 2개, 1회만 실행한다. 확인할 항목은 요청 성공, 파싱, 시스템 프롬프트 적용, 이력 유지, runs·responses·raw 저장, usage 확보, 차단·오류 처리다.
실행 전 준비:
1. `config/models.yaml`에 공식 `model_version`을 채운다. TBD면 실행을 거부한다.
2. `.env`에 API 키를 넣는다.
3. `config/system_prompt.txt`를 확정본으로 바꾼다.

Mock 결과는 실제 모델 동작을 검증한 것이 아니다.
