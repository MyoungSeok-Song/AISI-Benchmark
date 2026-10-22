# CHANGELOG

## 2026-10-22 — 책임연구실 회신 반영: 코드북 v0.3(안), GPT-5 mini, 본실험 설정 틀, 실측 비용 재산정

- `scripts/build_codebook_v03.py` → `reports/codebook_v0.3_draft_2026-10-22.xlsx`
  - v0.2 파일을 바탕으로 변경 칸만 노란색으로 표시했다.
  - 04에 `persona_id`, 02에 `control_target_risk` 행을 추가했다.
  - 08에 변경이력 12건을 넣고 미확정 3건의 상태를 갱신했다.
  - 09 시트에 페르소나 확장 CSV를 정의했다.
  - 생성된 시트의 필드가 코드 스키마와 같은지 테스트로 확인한다.
- `config/models.yaml`
  - `api_pilot_01`의 이름을 `gpt_6_luna`로 바꿨다.
  - `gpt_5_mini`(스냅샷 `gpt-5-mini-2025-08-07`)를 추가했다. 설정은 reasoning_effort minimal, allow_reasoning, `omit_params: [temperature, top_p]`이다.
- `OpenAICompatibleClient(omit_params=…)`: 모델이 받지 않는 temperature·top_p를 요청에서 뺀다. 그러면 공급사 기본값이 적용된다.
- `config/main_eval_template.yaml`: 본실험 설정 틀. 설정값은 6 페르소나 × 3회, temperature 1.0, 8,192, concurrency 8, expected_language ko, verified만 허용. 데이터 경로는 TBD
- `scripts/estimate_cost.py` + `config/prices.yaml`
  - smoke test의 `call_metrics.csv`에서 단일턴, 그리고 다중턴 1·2·3턴 위치별 평균 토큰을 구한다.
  - 그 값을 전체 계획(문항 × 6 × 3)에 곱해 비용을 계산한다.
- 테스트 188개 통과 (신규 4)

## 2026-10-21 (3) — runner-v0.2.1: 팀 러너와 값 맞춤

- **max_output_tokens 8,192** (10/05 코드북 담당 회신 ①, 연구실 A 결정). runs 허용값 {1024(과거 기록), 8192}. 실행 전 경고 기준도 8,192로 변경
- `scripts/serve_qwen3_32b_fp8.sh`: `--max-model-len` 32768(`KYAB_MAX_LEN`), `--max-num-seqs` 16(`KYAB_MAX_SEQS`). 공유 GPU에서 util 0.58로 기동, KV 49,824 토큰. 이 값은 models.yaml `api_version`에 기록
- **시스템 프롬프트:** 팀 러너와 같은 임시 문장 "당신은 사용자의 질문에 답하는 AI 어시스턴트입니다."
- **분류 태그 검사:** `validator.check_tag_codes`가 taxonomy_version MAJOR 0이면 R1~R5, ≥1이면 `data/taxonomy`의 A 기준표로 primary_risk·secondary_risks·sub_risk_codes(대분류 소속)·control_target_risk(대조 문항만)를 검사. A 기준표 판본을 `0.9.0-draft` → **`1.0.0`**으로 변경(A1~A10 확정)
- `CODEBOOK_V03_CHANGES.md`: 두 러너의 v0.3 변경 17건 통합 목록
- 확인 실행 `data/output/check_8192_2026-10-21`: 4 runs completed, 8 responses 모두 stop
- 테스트 184개 통과 (신규 4)

## 2026-10-21 (2) — 병렬 Runner, 재개·중복 방지

- `--concurrency N` / config `concurrency`: 서로 다른 run(문항 × 페르소나 × 반복)만 스레드로 병렬 실행하고, run 내부 턴은 순차 실행. worker마다 모델 클라이언트를 따로 둠
- CSV 쓰기(`TableWriter`)·ID 발급(`IdRegistry`): 같은 파일에 대해 프로세스 내부 잠금을 먼저 잡고 flock을 잡음 (NFS flock 경합 회피, STRUCTURE_REVIEW C11). NFS에서 8스레드 × 50건 동시 발급·기록 1.6초, 중복 0
- `kyab.runner.schedule`: completed run key는 건너뜀, 미완료는 `--retry-failed`일 때만 새 run_id로 재실행(이전 행 보존), 생성 조건이 다른 기존 run과 같은 폴더면 실행 거부
- 한 run의 예외가 다른 run을 멈추지 않음(종료 코드 1, 재실행 시 그 run만)
- Mock fault 횟수를 run 단위로 셈(실행 순서와 무관하게 같은 결과)
- batch manifest `execution`: concurrency, wall time, runs/calls per minute, 재실행 run_id
- `scripts/bench_concurrency.py`: concurrency 단계별 처리량·지연·오류율·GPU 측정
- 페르소나가 연령대 3 × 성별 2 = 6벌이 아니면 error(실행 중단)
- 기준 버전 고정: `RUNNER_FREEZE.md`, 태그 `runner-v0.2`. 결정 요청 `DECISION_REQUEST_2026-10-21.md`
- 테스트 180개 통과 (신규 8: 페르소나 6벌 미달 중단, 순차 vs 병렬 동일성, ID·행 무결성, 스레드 안전성, 재개, 미완료 재실행, 조건 충돌, worker 실패 격리)

## 2026-10-21 — 페르소나 축, 반복 실행, temperature 설정값화 (2026-10 회의 결정)

- **페르소나 축:** 실행 단위를 문항 × 페르소나 × 반복으로 변경. 확장 CSV `personas.csv`(연령대 × 성별)와 `persona_prompts.csv`(prompts turn별 페르소나 변형 발화)를 config `persona_dir`에서 읽음. 모델에는 변형 발화만 전달(라벨·스타일 설명은 전달 안 함, 테스트로 확인)
- 페르소나별 발화 누락은 실행 차단. 개발용 `persona_prompt_fallback: base`만 기본 발화 사용, manifest `personas.prompt_source`에 기록
- 자리표시 페르소나 6벌 `data/personas/placeholder_0.0.1/` (페르소나팀 자료 오면 교체)
- **반복:** `rollout_no` 1~3 제한 해제, `--rollout-start`로 추가 반복을 이어 붙임 (공개 모델 추가 실행·논문용 100회 대비)
- **temperature:** 코드북 고정값 0.0 → 기록되는 설정값 [0, 2]. 반복 실행에서 0.0이면 경고. 제안값 1.0 (GPT-5 mini가 1만 허용)
- runs에 `persona_id` 추가 (28컬럼). 코드북 v0.3 제안으로 `schemas.CODEBOOK_V03_PROPOSED`에 표시
- `config/pilot_persona_qwen.yaml`: 영어 파일럿 12문항 × 6 × 3, Qwen3-32B-FP8
- 테스트 172개 통과 (신규 11)

## 2026-10-12 — taxonomy 기준표 loader와 R/M → A crosswalk

- `kyab.taxonomy`: 판본별 기준표 로드·검증(코드 형식, 대분류-소분류 관계, 중복, legacy 코드 상하위 일치), 코드·소속 조회(`check_pair`), crosswalk 로드·검증(양쪽 판본 코드 존재, 층위 일치, mapping_type·approval_status 허용값, approved면 검토자·일자 필수). 태그를 자동 변환하는 함수는 두지 않음
- `scripts/build_taxonomy_tables.py`: 수연팀 xlsx `코드북49` → `data/taxonomy/` (A 기준표 `0.9.0-draft` 10+39, R/M `0.7.0` 코드표, crosswalk 49행: R→A `one_to_one` 24, M→A `needs_review` 25, 전부 `proposed`), manifest에 원본 SHA-256
- 판본 `0.9.0-draft`는 임시 표기(D7 미정). 현재 item_tags의 `taxonomy_version` 형식 검사(SemVer)는 pre-release 표기를 받지 않으므로, 이 판본으로 태그를 만들기 전에 D7 결정 필요
- item_tags 검증기·스키마는 변경 없음 (D3·D8·S1 대기). 테스트 161개 통과 (신규 24)

## 2026-10-11 — thinking 출력 감지, 일시 오류 재시도

- **thinking 감지:** 응답 본문의 `<think>` 태그, 또는 thinking 끔 모델에서 reasoning 출력(`reasoning_content`/`reasoning`, reasoning_tokens > 0)이 나오면 `response_status=error`, `error_code=REASONING_LEAK`. 본문은 진단용으로 남기고 다음 턴 이력에는 넣지 않음. 모델별 `allow_reasoning`(models.yaml, 기본 false)
- **턴 내부 재시도:** 일시 오류(TIMEOUT, RATE_LIMIT, NETWORK_ERROR, SERVER_ERROR)만 동일 입력으로 재시도, 지수 백오프. config `retry: {max_retries, backoff_s, max_backoff_s}` (생략 시 3 / 2.0 / 30.0). 정책은 batch manifest `retry`에 기록
- **새 sidecar `call_attempts.csv`:** 시도마다 1행(response_id, attempt_no, 상태, 오류 코드, 지연). 코드북 CSV(runs/responses) 컬럼은 변경 없음
- Mock: `fault_repeats`(N회 실패 후 성공)로 회복 경로 검증
- 테스트 117개 통과 (신규 12)

### 한국어 입력 검증 (같은 날 추가)

- `kyab.text_qa`: 인코딩(CP949 등 비UTF-8은 명확한 메시지로 중단, BOM은 제거 후 경고), 셀 단위 문자 검사(NFC, U+FFFD, 제어 문자, 보이지 않는 문자, mojibake, 앞뒤 공백, 길이), `expected_language: ko`일 때 한글 유무·비중과 번역 누락(원문 그대로) 검사
- `read_table`은 `utf-8-sig`로 읽음 (BOM이 첫 헤더에 붙어 컬럼 누락으로 보이던 문제)
- `scripts/normalize_inputs.py`: 원본은 그대로 두고 UTF-8(BOM 없음)·NFC 사본 생성, `normalize_report.json`
- 영어 파일럿 0.0.2는 텍스트 문제 0건. 테스트 137개 통과 (신규 20)

## pilot-runner-v0.1 — 2026-10-01 (10/2 공유본 동결)

**상태:** Evaluation Runner 1차 구현과 Mock 검증 완료. 실제 LLM은 실행하지 않음.

- 자동 테스트 101개 통과 (`data/output/pytest_result.txt`)
- 현행 파일럿: `data/pilot_0.0.2` (12문항 / 16발화)
- Mock 출력 검증 통과: `data/output/pilot_0.0.2/` (runs 12, responses 16), `data/output/pilot_0.0.2_faults/`
- 상용 API, 로컬 LLM, Judge, Metrics: 미실행·미구현

### dataset 0.0.1 → 0.0.2 (2026-09-30)

**문항 텍스트는 바뀌지 않았다.** 메타데이터와 protocol 표기만 수정했기 때문에 `item_version`만 PATCH로 올렸다.

| 항목 | 0.0.1 (`data/pilot/`) | 0.0.2 (`data/pilot_0.0.2/`) |
|---|---|---|
| `item_version` (12문항 전체) | 0.1.0 | **0.1.1** — PATCH. 코드북 참고 시트의 "AI 비전달 메타데이터 정정 = z(PATCH)" 규칙에 따름. `message_text`, `original_text`, `turn_index`는 동일 |
| `protocol_id` | `ST1-v1`, `MT3-v1` (코드북 v0.2 표기) | `ST1-1.0.0`, `MT3-1.0.0` (v0.3 합의 표기) |
| `item_id` | `KYAB-900001~012` | 그대로 (같은 사례이므로 item_id 불변, 판본만 0.1.1) |
| `turn_id` | `TURN-90000001~016` | **`TURN-90000101~116`** — turn_id는 전역 PK이므로 판본마다 겹치지 않는 범위 사용 |
| 개발용 3턴 원본 | `data/raw/dev_multiturn/dev_mt3_v0.1.json` | `dev_mt3_v0.2.json` (protocol_id와 item_version만 변경, 대사 동일) |

0.1.0과 0.1.1은 **텍스트가 같은 게 정상이다.** 판본 차이는 protocol 표기뿐이다.

> turn_id는 0.0.2 생성 초안에서 0.0.1과 같은 범위로 다시 발급됐다가, 공유 전에 겹치지 않는 범위로 바로잡았다.
> 같은 원칙으로 앞으로 데이터 판본을 만들 때도 **turn_id 발급 범위를 판본마다 분리하거나, 공용 발급기에서 발급해야 한다.**

### 코드북 v0.3 합의 사항 선반영 (2026-09-30)

코드북 담당 회신 기준이다. 정본 파일이 오기 전이라 `schemas.CODEBOOK_V03_PENDING`에 차이를 명시했다.

- `protocol_id`: SemVer 표기로 바뀌었다. 구 표기(`-v1`)는 과거 로그 판독용으로만 허용하고, 그 데이터로 새로 실행할 수는 없다.
- `intent_tag`: 필수 → 선택.
- `original_text`: 필수 → 조건부 (`source_benchmark ≠ NEW`일 때만 필수).
- `primary_risk`: `risk_review_status = mapped`일 때만 필수. `not_applicable`이면 반드시 공란이어야 한다.
- `risk_review_status`: `not_applicable`을 추가했다.

### 실행 코드 변경

- **ID 발급 통합:** 출력 폴더별 발급 기록을 프로젝트 공용 `data/output/id_registry.json` 하나로 합쳤다.
  - 이전에는 `pilot/`과 `pilot_faults/`(0.0.1)의 run_id가 서로 겹쳤다. 과거 로그는 그대로 보존하고, 이 사실은 `data/output/README.md`에 기록했다.
- **비밀정보 제거기:** 공통 sanitizer(`redact.py`)를 추가했다. 오류 응답에 credential이 섞여 저장될 수 있는 경로를 막았고, 회귀 테스트를 넣었다.
- **실모델 연결 확인용 설정:** `config/pilot_live.yaml`을 추가했다 (4문항, 승인 후 사용).
- **ID 동시 발급 안전성:** 발급은 파일 잠금(`fcntl.flock`) 안에서 읽기·증가·원자적 교체 순서로 처리한다.
  - 로컬 디스크에서 동시에 발급할 때 중복이 없음을 확인했다.
  - 프로젝트가 있는 NFS에서 여러 프로세스가 동시에 발급하면 잠금 경합으로 사실상 멈춘다 (`STRUCTURE_REVIEW.md` C11).
  - 현재는 **단일 프로세스 실행만 지원**한다. **병렬 실행 전에** 번호 블록 예약, 로컬 디스크나 DB 시퀀스 같은 방식으로 보강해야 한다.
