# KYAB 러너

코드북 7 CSV 형식으로 모델 실행 기록(`04_runs.csv`, `05_responses.csv`)을 만드는 실행 코드입니다.
단일턴 실행기와 3턴 실행기가 따로 있고, 입력 검증·기록·모의 모델은 함께 씁니다.

- 기준 명세: `project proposal/ETRI_7CSV_codebook_v0.2.xlsx` + 확정 변경(`schema/overlay_v0.3_confirmed.yaml`)
- 현재 범위: **모의 모델과 로컬 vLLM 모델**을 실행합니다. 상용 API 어댑터는 없습니다(API 키·D06·D08 확인 전 호출 금지).
- 채점은 하지 않습니다. 판정 단계가 쓸 빈 틀(`06_judgments_template.csv`)만 만듭니다.

## 설치

```bash
cd runner
python3 -m venv --without-pip .venv          # 이 서버에는 ensurepip이 없어 pip 없이 만든다
python3 -m pip --python .venv/bin/python install -r requirements.txt
```

## 실행

모든 명령은 `runner/` 폴더에서 실행합니다.

```bash
# 1. 입력 검증만
.venv/bin/python -m kyab_runner.run_single --validate-only

# 2. 단일턴 (ST1-1.0.0) · 3턴 (MT3-1.0.0). 샘플은 검토 전 문항이라 --allow-unverified 필요
.venv/bin/python -m kyab_runner.run_single    --allow-unverified
.venv/bin/python -m kyab_runner.run_multiturn --allow-unverified

# 3. 실패 경로 확인 (차단·오류·시간초과·빈 응답·길이 절단·재시도)
.venv/bin/python -m kyab_runner.run_multiturn --allow-unverified --mock-plan samples/mock_plan_failures.yaml

# 4. 중단된 배치 이어서 실행
.venv/bin/python -m kyab_runner.run_multiturn --allow-unverified --batch-id RBATCH-20260930-002

# 5. 테스트 (35개, 서버·GPU 없이 돈다)
.venv/bin/python -m unittest discover -s tests
```

## 로컬 모델 (vLLM)

이 서버에서 띄운 vLLM에만 요청을 보냅니다. 문항이 서버 밖으로 나가지 않습니다(어댑터가 localhost가 아닌 주소를 거부, 서버는 `HF_HUB_OFFLINE=1`·사용 통계 전송 끔·`127.0.0.1` 바인딩).

```bash
# 서버 기동 (GPU 1장). 먼저 nvidia-smi로 다른 작업이 GPU를 쓰는지 확인한다
.venv/bin/python tools/vllm_server.py start --model qwen3-8b-local --gpu 0

# 실행
.venv/bin/python -m kyab_runner.run_single    --allow-unverified --model qwen3-8b-local
.venv/bin/python -m kyab_runner.run_multiturn --allow-unverified --model qwen3-8b-local

# 반복 간 응답 동일 여부
.venv/bin/python tools/check_determinism.py samples/output/<run_batch_id>

# 서버 종료 (GPU 비우기)
.venv/bin/python tools/vllm_server.py stop
```

| 항목 | 값 |
|---|---|
| 모델 판본 (`model_version`) | HF 캐시 스냅샷 revision. 서버가 그 스냅샷을 올렸는지 실행 전에 확인 |
| 호출 파라미터 | `temperature` 0.0, `top_p` 1.0, `max_tokens` 1024. 모델 폴더의 `generation_config.json`은 쓰지 않음(`--generation-config vllm`) |
| thinking | 끔 (`chat_template_kwargs.enable_thinking=false`) |
| 접두부 캐시 | 끔 (`--no-enable-prefix-caching`). 켜면 같은 입력의 첫 호출과 이후 호출 응답이 갈림 |
| 서버·라이브러리 정보 | 기동 명령, dtype, GPU, vLLM·torch 버전, CUDA 빌드 → `batch_manifest.json`의 `adapter_info`, `runner_events.jsonl` |
| 모델 다운로드 | 서버 도구는 받지 않음. HF 캐시에 스냅샷이 없으면 멈춤 |

### vLLM venv 설치

러너 venv와 따로 둡니다. 위치는 `config/runner.yaml`의 `vllm_venv`이며 **경로에 공백이 없어야** 합니다(vLLM이 쓰는 FlashInfer가 첫 실행 때 커널을 빌드하는데 공백 경로에서 실패). 그래서 러너 폴더 밖에 있습니다.

이 서버의 NVIDIA 드라이버(570.124.06)는 CUDA 12.8까지 지원합니다. PyPI 기본 vllm 휠은 CUDA 13 빌드라 그대로는 뜨지 않습니다. 드라이버·시스템 패키지는 건드리지 않고 venv 안에서 CUDA 12 빌드로 맞춥니다. 전역 pip 설정의 추가 인덱스(`pypi.ngc.nvidia.com`)가 조회되지 않아 설치가 멈추므로 `--isolated`를 씁니다.

```bash
V=/home/ubuntu/342/myoungseok/.venvs/etri-vllm
python3 -m venv --without-pip $V
PIP="python3 -m pip --isolated --no-cache-dir --python $V/bin/python"
$PIP install vllm==0.30.0
$PIP install torch==2.13.0+cu126 torchvision==0.28.0+cu126 torchaudio==2.11.0+cu126 --index-url https://download.pytorch.org/whl/cu126
$PIP install --no-deps torchcodec==0.16.0+cu126 --index-url https://download.pytorch.org/whl/cu126
$PIP install --no-deps --force-reinstall "https://github.com/vllm-project/vllm/releases/download/v0.30.0/vllm-0.30.0%2Bcu129-cp38-abi3-manylinux_2_28_x86_64.whl"
```

고정 버전은 `requirements-vllm.txt`에도 적혀 있습니다.

| 옵션 | 뜻 | 기본값 |
|---|---|---|
| `--input` | `01_items.csv`·`02_item_tags.csv`·`03_prompts.csv` 폴더 | `samples/input` |
| `--out` | 출력 루트. 아래에 `<run_batch_id>/` 폴더가 생김 | `samples/output` |
| `--model` | `config/models.yaml`의 `model_id` | `mock-echo` |
| `--protocol` | 실행할 `protocol_id` | 단일 `ST1-1.0.0`, 다중 `MT3-1.0.0` |
| `--rollouts` | 문항당 반복 횟수 | 3 |
| `--items` | 실행할 `item_id` (쉼표 구분) | 프로토콜에 맞는 전체 |
| `--batch-id` | 이어서 실행할 배치 | 새 배치 |
| `--allow-unverified` | 검토 미통과·비활성 문항도 실행 | 꺼짐 |
| `--mock-scenario` / `--mock-plan` | 모의 응답 시나리오 (전체 / 호출별) | `normal` |

종료 코드: 0 정상, 1 실행할 문항 없음, 2 입력 오류, 130 사람이 중단.

## 출력

`<out>/<run_batch_id>/` 아래에 생깁니다. 실행 명령 1회가 배치 1개입니다.

| 파일 | 내용 | 코드북 |
|---|---|---|
| `04_runs.csv` | 실행 1건당 1행 (문항 × 모델 × 반복 번호) | 27필드, 열 순서 그대로 |
| `05_responses.csv` | 턴 1개당 1행. 실제 보낸 메시지와 원본 응답 포함 | 11필드 |
| `06_judgments_template.csv` | 판정 단계가 채울 빈 틀. 4개 필드만 미리 채움 | 29필드 |
| `batch_manifest.json` | 배치의 고정 조건(프로토콜·모델·입력 해시·적용한 overlay) | CSV 밖 |
| `runner_events.jsonl` | 턴별 시도·지연·재시도 내역 | CSV 밖 |

코드북에 칸이 없는 값은 CSV에 열을 만들지 않고 아래처럼 둡니다.

| 값 | 위치 |
|---|---|
| 토큰 사용량 | `raw_response_json`의 `usage` (실행 요약이 합산) |
| 턴별 지연시간, 재시도 횟수 | `runner_events.jsonl` |

## 동작 규칙

### 모델에 보내는 것

코드북 'AI 전달' 열이 ○·△인 값만 보냅니다.

| 메시지 | 내용 |
|---|---|
| system | `config/system_prompt.txt` (모든 실행 동일) |
| user | `context_text`가 있으면 그 뒤에 빈 줄을 두고 `message_text` |
| assistant (3턴만) | 같은 실행에서 모델이 실제로 한 앞 턴 응답 |

태그, 기대 안전응답, 금지 응답, 위험 단서, 페르소나 메모는 보내지 않습니다.
호출 파라미터는 `temperature` 0.0, `top_p` 1.0, `max_output_tokens` 1024입니다.

### 3턴 진행

사용자 발화는 대본 그대로 넣고, assistant 자리에는 모델의 실제 응답을 넣습니다.
매 턴 전체 메시지를 다시 보내며 공급자 쪽 대화 저장 기능은 쓰지 않습니다.
어느 턴이 성공하지 못하면 그 실행은 거기서 멈춥니다. 치명적 실패에 따른 즉시 종료는 하지 않습니다(실행 중 판정기가 없음).

### 실행 상태

| 상황 | `run_status` | `stop_reason` |
|---|---|---|
| 계획한 턴 모두 성공 | completed | planned_end |
| 턴 차단, 성공 턴 1개 이상 / 0개 | partial / failed | provider_block |
| 턴 오류·시간초과·빈 응답, 성공 턴 1개 이상 / 0개 | partial / failed | error |
| 사람이 중단 | partial / failed | manual_stop |

- 모델이 스스로 거절한 문장은 차단이 아니라 `success`입니다. 공급자 안전필터가 응답을 막은 경우만 `blocked`입니다.
- 출력 한도에서 잘린 응답은 `success` + `finish_reason=length`이고 실행은 계속됩니다.
- 일시 오류는 같은 턴을 최대 3회 시도합니다. 응답 행은 턴당 1개(마지막 시도)입니다.
- `first_fail_turn`, `first_cfc_turn`은 판정에서 나오는 값이라 비워 둡니다.

### 기록과 재시작

- 이미 쓴 행은 고치지 않습니다. 실행 행은 실행이 끝났을 때 한 번 쓰므로 `04_runs.csv`에 `queued`·`running` 행은 남지 않습니다.
- 재시작하면 이미 기록된 (문항, 판본, 모델, 반복 번호)는 건너뜁니다. `failed`·`partial`로 끝난 실행도 기록이므로 다시 실행하지 않습니다.
- 입력 파일·프로토콜·모델·시스템 프롬프트가 처음과 다르면 같은 배치로 이어 쓰지 않습니다.
- 같은 출력 루트에 두 실행기를 동시에 돌리면 ID가 겹칠 수 있습니다. 한 번에 하나만 실행합니다.

## 입력 검증

실행 전에 입력 3종을 검사하고, 오류가 하나라도 있으면 모델을 호출하지 않습니다.

| 묶음 | 검사 |
|---|---|
| 형식 | 머리글이 코드북 열과 순서까지 같음, 행마다 셀 수 일치 |
| 필드 | 허용값, 정규식, JSON·날짜·SemVer 형식, 필수 여부 |
| 키·연결 | PK 중복, 턴·태그가 가리키는 문항 존재, `turn_index` 1부터 연속 |
| 문항 | `conversation_mode` ↔ `planned_round_count` ↔ `protocol_id`, 외부 원천 문항의 `original_text`·`source_item_id`, 등록된 `rubric_id` |
| 태그 | 문항 판본당 `current` 1개, 분류 코드(아래), `mapped`이면 주대분류·주소분류 필수, `ambiguous` 단독 사용 |

분류 코드는 `taxonomy_version`의 MAJOR로 나눠 검사합니다.

| MAJOR | 체계 | 규칙 |
|---|---|---|
| 1 이상 | A1~A10 | `primary_risk` A1~A10, `sub_risk_codes` 0~1개이며 주대분류의 자식, `secondary_risks`에 주대분류 중복 금지, `m_review_*` 공란 |
| 0 | 이전 R/M (과거 이력 행) | `primary_risk` R1~R5, `m_review_codes` M01~M05, `m_review_status` 필수 |

`turn_id`는 입력에 있어야 합니다. 러너는 발급하지 않습니다.

## 실행 코드 버전과 git

`runner/`는 **로컬 전용 git 저장소**입니다. 원격을 추가하거나 push하지 않습니다.

| 상태 | `execution_library_version` |
|---|---|
| 커밋된 상태 그대로 실행 | `runner-0.1.0+<커밋 SHA 7자리>` |
| 커밋 안 된 변경이 있음 | `runner-0.1.0+<SHA>.dirty` (실행은 막지 않고 표시만 남김) |
| git 저장소가 아님 | `runner-0.1.0+src<소스 해시 7자리>` |

본평가는 `.dirty`가 붙지 않은 상태에서 돌립니다. `.venv/`, `samples/output/`, `var/`, 모델 가중치는 저장소에 넣지 않습니다(`.gitignore`).

## 폴더 구조

```
runner/
  tools/
    extract_codebook.py     코드북 xlsx → schema/codebook_v0.2.json
    extract_taxonomy.py     분류팀 xlsx → schema/taxonomy_A1-A10.json, crosswalk_RM_to_A.csv
    build_samples.py        개발 샘플 입력 6건 생성
    vllm_server.py          로컬 vLLM 서버 기동·종료·상태
    check_determinism.py    반복 간 응답 동일 여부 확인
  schema/
    codebook_v0.2.json            코드북 추출본 (손으로 고치지 않음)
    overlay_v0.3_confirmed.yaml   v0.3 xlsx가 오기 전까지의 확정·가정 변경
    taxonomy_A1-A10.json          분류체계 상위 10 · 하위 39
    crosswalk_RM_to_A.csv         이전 코드 → 새 코드 49행
  config/
    runner.yaml             코드북이 정하지 않은 규칙의 기본값
    models.yaml             모델 등록부
    system_prompt.txt       시스템 프롬프트 원문
  kyab_runner/
    codebook.py             명세 로드, 값·행 검사
    taxonomy.py             분류체계·이전 코드
    config.py               설정 로드
    csv_io.py               코드북 열 순서로 CSV 읽기·쓰기
    validate.py             입력 검증
    ids.py                  RBATCH·RUN·RESP 발급
    messages.py             요청 메시지 구성
    adapters/               base.py(규격), mock.py(모의), local_vllm.py(로컬 vLLM)
    session.py              실행 1건의 기록 절차 (두 실행기 공용)
    cli.py                  명령행·배치 진행 (두 실행기 공용)
    run_single.py           단일턴 실행기
    run_multiturn.py        다중턴 실행기
    judge_io.py             판정 빈 틀
  samples/
    input/                  샘플 입력 (900000번대 ID, 실제 문항 아님)
    output/                 샘플 실행 결과
    mock_plan_failures.yaml 실패 경로 모의 계획
  tests/                    test_runner.py, test_local_vllm.py
  var/                      서버 기동 정보·로그 (git 제외)
```

## 명세가 바뀔 때

| 바뀐 것 | 할 일 |
|---|---|
| 코드북 xlsx (v0.3) | `tools/extract_codebook.py`의 파일명·출력 경로를 새 판으로 바꿔 실행 → `paths.py`의 `CODEBOOK_JSON` 갱신 → `overlay_v0.3_confirmed.yaml`에서 반영된 항목 삭제(전부 반영됐으면 파일 삭제) → 테스트 |
| 분류표 (분류팀 xlsx) | `tools/extract_taxonomy.py` 실행. 검산 21항목이 모두 OK여야 파일을 씀 |
| 협의 결과 (재시도 횟수, context 위치, 등록 코드 등) | `config/runner.yaml`만 수정 |
| 새 모델 | `config/models.yaml`에 등록하고 `adapters/`에 어댑터 추가 |

## 가정과 알려진 한계

overlay의 `provisional` 항목과 `config/runner.yaml`의 기본값은 결정 전 가정입니다.

| 항목 | 현재 처리 | 근거 |
|---|---|---|
| `sub_risk_codes` | 주소분류 0~1개 | 검토메모 §3 A안 |
| `m_review_codes`, `m_review_status` | 새 체계 행은 공란, 필수 해제 | 검토메모 §6 |
| `response_text` | 성공 응답만 필수 | 차단·오류 시 본문이 없음 |
| `MT7`·`MT10` 표기 | `MT7-1.0.0`, `MT10-1.0.0` | 회신은 ST1·MT3만 예시 |
| 성공 턴이 0개인 실행 | `failed` (단일턴 차단 포함) | C4 기본값 |
| 대화 범위 판정 행 | 마지막 성공 응답의 `response_id` 참조 | 06에 `run_id` FK가 없음 |
| 로컬 모델 `provider` | 임시 등록 코드 `local_vllm` | 등록 코드 목록 미정 |
| 신규 문항 `source_license` | 임시 값 `LicenseRef-KYAB-internal` | 내부 코드 미정 |
| 상용 모델 | 미구현 | API 키·D06·D08 확인 후 |
