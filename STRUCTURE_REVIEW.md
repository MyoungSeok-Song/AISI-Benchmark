# 코드북 v0.2 구조 검토 및 협의사항 (LAMDA, 2026-09-30)

> **갱신 (2026-09-30 저녁):** 코드북 담당 회신으로 B1·B3·B4·B5가 해결됐다. 해결 내용은 다음과 같다.
> - `intent_tag`는 선택으로 바뀌고, 존재하지 않는 필드 참조는 정정된다.
> - 시트 번호가 정정된다.
> - `protocol_id`는 `MT3-1.0.0` 형식으로 통일된다.
> - `original_text`는 외부 원천 문항에만 필수다.
>
> 추가로 `primary_risk`는 mapped일 때만 필수이고, `risk_review_status`에 `not_applicable`이 추가됐다. 코드에는 선반영했다 (`schemas.CODEBOOK_V03_PENDING`). R/M → A1~A10 통합 검토는 `TAXONOMY_REVIEW.md`에 있다.

코드북 v0.2, ER 모델, 수행계획서 v1.1을 실행 코드로 옮기면서 확인한 내용이다. 항목은 세 갈래로 나눈다.

- **A**: 코드북 08 시트에 이미 적혀 있는 미확정 사항
- **B**: 구현하면서 새로 발견한 문서 간·문서 내 불일치
- **C**: 실행 프로토콜 관련 협의사항

"현재 구현"은 협의 전까지 임시로 적용한 방식이고, 모두 설정이나 한 곳의 코드만 바꾸면 조정된다.

## 0. 확인된 구조 (구현·테스트 완료)

| ER 관계 | 구현 검증 |
|---|---|
| items PK `item_id + item_version` | PK 중복 검사 |
| item_tags PK `item_id + item_version + tag_revision`, 문항 버전당 `current` 1개 | FK·current 개수 검사, superseded 이력 허용 테스트 |
| prompts PK `turn_id`, UQ `item_id + item_version + turn_index` | PK·UQ·FK·turn_index 1..N 연속·예정 턴수 검사 |
| runs PK `run_id`, FK `item_id + item_version` | 출력 검증 (FK, dataset_version·protocol 일치, prompt 해시) |
| responses PK `response_id`, FK `run_id + turn_id` | 출력 검증 (FK, run의 문항 = turn의 문항, 턴 연속, actual_turn_count = success 수) |
| judgments / results | 컬럼 계약만 코드화 (`schemas.JUDGMENTS`, `RESULTS`), 미구현 |

7개 CSV 모두 필드명, 순서, 필수성(A열), AI 전달 표시(B열)가 코드와 일치하는지 테스트가 매번 xlsx와 대조한다 (`tests/test_schema.py`).

## A. 코드북 08_변경이력·미확정에 기재된 사항

| # | 논점 | 실행 단계 영향 | 현재 구현 |
|---|---|---|---|
| A1 | R/M 위험군·태그 허용값 | 없음 (Target LLM에 전달하지 않음) | 코드북 잠정 어휘로 검증 |
| A2 | critical_failure_code 미발생 표기 (null/빈값/NONE) | 없음 (Judge 단계) | 미구현 |
| A3 | verdict `inconclusive` 허용·집계 | 없음 (Judge 단계) | 잠정 어휘 3개 허용 |
| A4 | 태그 수정 이력 ↔ 판정·집계 연결 | 실행기는 `item_id + item_version`으로만 실행하고 태그를 참조하지 않음. 태그를 바꿔도 **재실행 없이 재판정**할 수 있음 | 판정 단계에서 `tag_revision` 연결 |
| A5 | run_batch_id 발급·재사용 규칙 | 필요 | **실행 1회 × protocol_id마다 새 배치 1개**. 한 배치 안에서는 dataset_version, protocol, system prompt, 생성 파라미터, 코드 버전이 같음. 모델은 실행 1회에 1개. 재사용은 하지 않음 |

## B. 구현 중 발견한 불일치

| # | 위치 | 내용 | 제안 |
|---|---|---|---|
| B1 | 02 `intent_tag` | A열은 "문항·태그 입력 필수"인데 설명은 "선택 필드로 두고…"라고 함. 또 존재하지 않는 `items.expected_safe_response_json`을 참조함 (01_items에 없음. 계획서의 expected_safe_response는 `item_tags.expected_response_tags`로 옮겨져 있음) | 필수성을 하나로 정하고, 참조 필드명을 정정 |
| B2 | 06 `evaluation_scope` | 설명은 "(response)", 허용값은 `turn, conversation` | 허용값 기준으로 설명 정정 |
| B3 | 06 `critical_failure_code`, `verdict` 비고 | "09_변경이력·미확정"을 참조하지만 실제 시트는 08 | 시트 번호 정정 |
| B4 | 01 `protocol_id` ↔ 참고 시트 2절 | 통제어휘는 `ST1-v1…`인데, 버전관리 제안은 "protocol_id에 SemVer 포함, 예: MT3-1.0.0" | 둘 중 하나로 통일. 현재는 통제어휘(`MT3-v1`)로 검증함 |
| B5 | 01 `original_text` | A열은 필수인데, 설명은 "원천 추적이 필요할 때만". 계획서 표 IV-19는 조건부. 신규(NEW) 문항에는 원문이 없음 | 조건부로 정정 (source_benchmark ≠ NEW일 때 필수) |
| B6 | 01 `localization_type` | 번역하지 않은 원문을 그대로 쓰는 경우에 맞는 값이 없음. 영어 파일럿 10문항이 공란 | 파일럿은 공란을 허용하거나, 값(예: `none`)을 추가 |
| B7 | 01 `target_age_group` | 필수인데 CAREBench·MinorBench 원천에는 없음. 추정 입력은 금지 | **누가 어떤 기준으로 부여할지** 결정 필요 (한국화 단계에서 설계값으로 부여?) |
| B8 | 02 `companion_context` | 코드북은 `companion` 또는 공란, 계획서 표 IV-19는 `yes · no · undetermined` | 코드북 기준을 확정 (현재 코드북 기준) |
| B9 | 03 `message_text` | "한국어 UTF-8 원문"으로 정의됨. 영어 파일럿은 이 정의 밖 | 파일럿은 manifest에 `purpose: dev_pilot_english`, `not_for_official_evaluation: true`로 구분. 정식 결과와 섞지 않음 |
| B10 | 04 `stop_reason` 어휘 | 모델 자체 차단(`block_source=model`)·로컬 가드레일 차단, 빈 응답으로 인한 중단에 대응하는 값이 없음 | 현재 blocked는 모두 `provider_block`, empty는 `error`로 기록함. 값 추가 여부 결정 |
| B11 | 06 judgments (conversation scope) | 대화 전체 판정도 `response_id`만 가지므로 어느 실행 전체를 판정한 것인지 암묵적임 | `run_id` 컬럼을 추가하거나, "run의 마지막 response_id를 anchor로 쓴다"는 규칙을 명시 |
| B12 | 04 `first_fail_turn` | 계획서는 "필수 기록", 코드북은 조건부 (실패가 없으면 공란) | 실행 시점에는 공란이고 Judge 이후 후처리로 채움. 코드북 정의를 따름 |
| B13 | 05 `raw_response_json` | "유효한 JSON 객체"인데, timeout·네트워크 오류처럼 공급자 응답이 없는 경우의 규칙이 없음 | 현재 `{"pipeline_note": "no_provider_response", "exception_type": ...}`로 기록. HTTP 오류는 `{"http_status", "body"}`. 이 규칙의 승인 필요 |
| B14 | 05 `error_code` | "정규화 오류 코드"라고만 되어 있고 목록이 없음 | 제안 목록: AUTH_ERROR, RATE_LIMIT, TIMEOUT, NETWORK_ERROR, BAD_REQUEST, CONTEXT_LENGTH, PROVIDER_BLOCK, EMPTY_RESPONSE, PARSER_ERROR, SERVER_ERROR, UNKNOWN_ERROR |
| B15 | 01/03 테스트 문항 ID | `^KYAB-[0-9]{6}$` 하나뿐이라 개발·테스트용 문항과 정식 문항이 같은 공간을 씀 | 테스트 범위를 예약 (현재 `KYAB-900001~`, `TURN-90000001~`를 임시로 사용) |

## C. 실행 프로토콜 협의사항

| # | 논점 | 현재 구현 | 결정 필요 |
|---|---|---|---|
| C1 | **CFC 발생 시 즉시 종료** (계획서 표 IV-20) | 미구현. 판정 모듈이 오프라인이라 실행 중에는 CFC를 알 수 없음. 기술적 실패(오류, 차단)가 나면 중단함 | ① 턴마다 온라인 Judge를 붙여서 CFC가 나면 종료, 또는 ② 전체 턴을 실행한 뒤 오프라인 판정하고 CFC 이후 턴은 집계에서 제외. 프로토콜에 명시 필요 |
| C2 | 시스템 프롬프트 원문 | 계획서는 "위험군·연령대와 무관한 동일 문자열"만 정하고 원문은 없음. 현재 임시값 `You are a helpful assistant.` (원문과 해시를 runs에 기록) | 최종 문자열(한국어 여부 포함) 확정 |
| C3 | `context_text` 전달 템플릿 | `context_text + 빈 줄 + message_text` (임시). 파일럿 데이터에는 context가 없어서 영향 없음 | 템플릿을 protocol 버전에 고정 (`protocol.py` 한 곳) |
| C4 | 공급자별 파라미터 차이 | temperature 0.0, top_p 1.0, 최대 1024 토큰을 그대로 전송. 파라미터 이름 차이(`max_tokens` / `max_completion_tokens`)는 모델 설정으로 처리 | 일부 모델(추론형 등)은 temperature·top_p를 받지 않거나 고정함. **지원하지 않을 때 기록·예외 규칙**을 정하고 후보표에 지원 여부 기재 |
| C5 | 추론(thinking) 모드 | 로컬 Qwen3 설정은 `enable_thinking: false` (임시). **2026-10-11 추가:** 응답 본문에 `<think>` 태그가 있거나, thinking 끔 모델(`allow_reasoning: false`, 기본값)에서 reasoning 출력이 보고되면 그 턴을 `error`/`REASONING_LEAK`로 기록하고 중단한다(이력 오염 방지, 재시도 없음). 파일럿 48건은 0건 | thinking 켜기·끄기와 1024 토큰 안에 thinking을 포함할지는 모델 간 공정성 문제. runs에 기록할 필드도 없음 |
| C6 | 일시 오류 재시도 | **2026-10-11 구현 (제안값):** TIMEOUT·RATE_LIMIT·NETWORK_ERROR·SERVER_ERROR만 같은 턴, 같은 messages로 재시도(기본 최대 3회, 2·4·8초, 상한 30초, config `retry`). 차단·빈 응답·결정적 오류는 재시도하지 않음. responses.csv에는 턴당 최종 시도 1행, 모든 시도는 sidecar `call_attempts.csv`, 정책은 batch manifest에 기록. 재시도 후에도 실패하면 기존처럼 partial·failed | 재시도 횟수·대상 확정, PROVIDER_BLOCK·EMPTY 처리, 429의 Retry-After 반영 여부, run 단위 재실행 시 새 run_id 발급 여부, rollout_no 재사용 여부 |
| C7 | `request_messages_json` 형식 | 표준 `role/content` 배열(OpenAI 형식)을 그대로 전송하고 저장 | 형식이 다른 공급자(Anthropic의 system 분리, Gemini의 contents 등)는 "실제 전송 형식을 그대로" 저장할지 "표준 형식으로" 저장할지 결정 |
| C8 | 로컬 모델의 `model_version`·`api_version` | models.yaml에 기록 (TBD면 실행 거부) | 로컬은 `HF repo@commit` + 정밀도, `api_version`은 vLLM 버전으로 기록하는 규칙 확정 |
| C10 | `actual_turn_count` 계산 규칙 | **현재 구현 규칙: `response_status=success` 턴만 완료 턴으로 센다.** blocked, empty, timeout, error는 세지 않는다. 코드북 정의는 "모델 응답까지 완료된 턴 수"다 | blocked·empty 턴을 "응답 완료"로 볼지 정식 실행 전에 재확인. 결과를 해석할 때 중도 종료 분모에 영향이 있다 |
| C11 | **ID 동시 발급 (병렬 실행 전 보강 필요)** | 공용 `data/output/id_registry.json`을 파일 잠금(flock) 안에서 읽고, 번호를 올리고, 원자적으로 교체한다. 측정(2026-10-01): 로컬 디스크에서 8개 프로세스가 1,600개를 동시 발급했을 때 중복 0건이었다. NFS(프로젝트 위치)에서 단일 프로세스는 정상이었다. 그러나 **NFS에서 3개 프로세스가 동시에 발급하면 중복은 없지만 잠금 경합으로 사실상 멈췄다** (5개 발급에 92초, 100초 시간 초과 2건). 현재 파일럿은 단일 프로세스라 영향이 없다 | 최종 평가를 병렬로 돌리기 전에 발급 방식을 바꾼다. 예: 프로세스마다 번호 블록을 한 번에 예약(잠금 횟수를 크게 줄임), 발급 기록을 로컬 디스크나 DB 시퀀스에 두기, 실행 노드별 번호 대역 |
| C9 | item_review_status 실행 게이트 | 파일럿은 `pending`을 허용하고, 정식은 `verified`만 허용하도록 설정 | 실행 허용 상태값 확정 (verified만? revised 포함?) |

## D. 이번 단계에서 하지 않은 것

- LLM-as-a-Judge, 사람 20% 재채점, Metrics, CRRI (06·07은 컬럼 계약과 인터페이스만)
- 실제 API·로컬 모델 호출 (지시에 따라 보류)
- 한국화
