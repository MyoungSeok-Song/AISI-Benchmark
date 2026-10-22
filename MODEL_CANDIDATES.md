# 평가 대상 모델 후보표 (상용 API 3종 + 공개 가중치 2종)

- **기준일:** 2026-09-30. 공식 문서(개발사 docs, 가격 페이지, HF 모델카드·LICENSE, vLLM 문서)만 확인했다. 실제 API·로컬 호출은 하지 않았다.
- **"확인 필요"** 표시는 공식 원문으로 확인하지 못한 항목이다. 추정해서 채운 값은 없다.
- 계산으로 낸 값(비용, VRAM)은 **계산값**으로 표시했다.
- 최종 선정은 연구진이 결정한다. 아래 "추천"은 LAMDA의 제안이다.

---

## ⚠ 먼저 결정할 사항: 고정 생성 조건과 현행 상용 모델이 충돌함

계획서 표 Ⅳ-20과 코드북 04_runs는 **temperature 0.0, top_p 1.0, 최대 1,024 토큰**을 전 모델에 고정하도록 정하고 있다. 그런데 세 회사의 현행 주력 모델은 모두 이 조건을 그대로 받지 않는다.

| 공급사 | temperature 0.0 / top_p 1.0 | 1,024 토큰 한도 |
|---|---|---|
| OpenAI (GPT-6 Astra, GPT-6.1 Sol) | reasoning을 끌 수 없어서 temperature·top_p를 **보낼 수 없음** (문서: reasoning effort가 `none`이 아니면 제거) | reasoning 토큰이 한도에 포함 → 답변이 비거나 잘릴 위험 (OpenAI는 약 25K 여유를 권장) |
| Anthropic (Claude Opus 5.5, Sonnet 5.5) | temperature 1.0 외 값은 **HTTP 400**. top_p는 0.99 이상만 허용 | Opus 5.5는 thinking을 끌 수 없고 thinking이 `max_tokens`에 포함됨 |
| Google (Gemini 3.x) | temperature·top_p **폐기**(2026-07-21 changelog). 보냈을 때 무시되는지 오류인지는 확인 필요 | thinking을 끌 수 없고 `maxOutputTokens`에 포함됨 |

그래서 "전 모델 동일 생성 조건"을 유지하려면 다음 중 하나를 정해야 한다 (→ `DECISION_REQUEST.md` 5번).
- **(a)** 공급사별 예외 규칙을 두고 runs에 실제 적용값을 기록한다.
- **(b)** 출력 한도를 "보이는 답변 기준 1,024"로 재정의하고, thinking 한도는 별도로 둔다.
- **(c)** 온도 고정이 가능한 모델(예: GPT-6 Luna `none`, Claude Haiku 4.5)로 후보를 좁힌다. 대신 대표성은 떨어진다.

공개 가중치 모델(vLLM)은 temperature 0.0, top_p 1.0을 그대로 적용할 수 있다. thinking 모드는 chat template으로 끌 수 있다.

---

## 1. 최종 평가 5종 후보

| 구분 | 추천 모델 | API model ID / checkpoint | 버전·snapshot | 실행 | 비용 (1M 토큰, Standard) | 자원 | 라이선스·약관 |
|---|---|---|---|---|---|---|---|
| 상용 ① OpenAI | **GPT-6 Astra** (대안: GPT-6.1 Sol) | `gpt-6-astra` (대안 `gpt-6.1-sol`) | 날짜 snapshot 없음. alias 하나뿐이라 실행일 기록 필요 | API | Astra 입력 $10 / 출력 $50 (Batch 50%↓). Sol $2 / $10 | — | OpenAI 약관·사용정책 |
| 상용 ② Anthropic | **Claude Opus 5.5** (대안: Claude Sonnet 5.5) | `claude-opus-5-5` (대안 `claude-sonnet-5-5`) | 날짜 없는 ID 자체가 고정 snapshot (공식 문서). 2027-09-22 이전 은퇴 없음 약속 | API | Opus 5.5 $4 / $20. Sonnet 5.5 $2 / $10 (Batch 50%↓) | — | Anthropic 약관·AUP |
| 상용 ③ Google | **Gemini 3.1 Pro (Preview)** (대안: Gemini 3.8 Flash GA) | `gemini-3.1-pro-preview` (대안 `gemini-3.8-flash`) | **Pro는 Preview뿐**이고 고정 버전 ID가 없음. 응답의 `modelVersion`과 실행일을 기록해야 함 | API | 3.1 Pro $2 / $12 (≤200K 프롬프트). 3.8 Flash $0.75 / $3.75 (2026-12-31까지, 이후 $1.50 / $7.50) | — | Gemini API 약관·금지사용정책. **유료 티어 필수** (무료는 데이터를 제품 개선에 사용) |
| 공개 ④ Qwen | **Qwen3.8-27B** (대안: Qwen3.6-35B-A3B) | `Qwen/Qwen3.8-27B` @ `1d4bf0f2ff60…` (2026-08-14) | HF commit으로 고정 | 로컬 vLLM | GPU 자체 보유 (추가 과금 없음) | bf16 55.6 GB (계산값, 가중치만) → **H100 1장**. **vLLM ≥0.17 필요** | Apache-2.0 |
| 공개 ⑤ 한국 개발 | **Kakao Kanana-2-30B-A3B-Instruct-2601** (대안: LG EXAONE 4.5-33B) | `kakaocorp/kanana-2-30b-a3b-instruct-2601` @ `4a781fe5027d…` (2026-07-29) | HF commit으로 고정 | 로컬 vLLM | GPU 자체 보유 | bf16 61.3 GB (계산값) → H100 1장 (KV 여유가 작아 2장 TP 권장). vLLM 최소 버전 확인 필요 | Kanana License: 연구·자체 서비스 무료. 재배포·보고 시 **"Powered by Kanana"** 표기 |

### 모델별 추천 이유와 주의점

**① GPT-6 Astra**
- 추천 이유: OpenAI 공식 flagship이고, 청소년이 실제로 접하는 대표 상용 모델이다. 한국어 지원도 한다.
- 주의:
  - 일부 조직부터 순차 공개 중이라 우리 조직에서 쓸 수 있는지 **확인 필요**.
  - 날짜 snapshot이 없다.
  - reasoning 최저가 `low`라서 temperature를 고정할 수 없다.
  - 비용이 가장 크다. 비용을 줄이려면 대안 GPT-6.1 Sol("near-Astra", 가격 1/5)이 있지만 온도 문제는 똑같다.

**② Claude Opus 5.5**
- 추천 이유: Anthropic 현행 Opus이고, 가격이 이전 Opus보다 낮다($4 / $20). ID가 고정 snapshot이라 재현성이 좋다.
- 주의:
  - thinking을 끌 수 없어 1,024 한도 안에서 thinking과 답변이 경쟁한다.
  - 비용을 줄이려면 대안 Sonnet 5.5가 있다. thinking은 `between_tools`로 최소화할 수 있다.
  - 안전 분류기가 거절하면 `stop_reason: "refusal"`로 온다. **서버 측 fallback 옵션은 쓰면 안 된다.** 켜면 다른 모델이 대신 답해서 평가가 오염된다.

**③ Gemini 3.1 Pro**
- 추천 이유: Google 현행 Pro이다.
- 주의:
  - **Preview 상태**라 모델이 바뀔 수 있고 종료일이 공지되지 않았다. 재현성을 우선하면 GA인 3.8 Flash가 낫다.
  - 조정 가능한 안전 필터의 기본값이 **OFF**라서 `service_default`는 "필터 꺼짐"으로 기록해야 한다.
  - **아동 안전 보호장치는 끌 수 없는 상시 차단**이라서, 그루밍·성착취 문항은 공급사 차단(blocked)으로 나올 수 있다.
  - 유료 티어도 55일 동안 남용 모니터링 목적으로 보관되고, 필터에 걸린 요청은 사람이 검토할 수 있다.

**④ Qwen3.8-27B**
- 추천 이유: 현행 최신 Qwen 세대 중 H100 1장에 들어가는 dense 모델이다. Apache-2.0이라 라이선스 부담이 없다.
- 주의:
  - thinking이 기본으로 켜져 있어 `enable_thinking: false`로 고정해야 한다.
  - 한국어 지원이 모델카드에 명시되어 있지 않다. Qwen3.5 블로그의 201개 언어는 **확인 필요**.
  - Qwen 권장 디코딩(temp 0.7, top_p 0.8)과 프로토콜 값이 달라서 방법론에 적어야 한다.

**⑤ Kanana-2-30B-A3B-Instruct**
- 추천 이유: 국내 개발 모델이고 한국어를 명시 지원한다. non-thinking 전용 instruct라 조건 통제가 쉽다. 연구 평가 이용이 허용된다.
- 대안 EXAONE 4.5-33B의 특징:
  - 인지도와 한국어 성능이 높다.
  - 대신 **비상업(NC) 라이선스**라서 과제 산출물이 상업적으로 쓰이지 않는지 확인이 필요하다.
  - 발표 시 귀속 표기 의무가 있다.
  - thinking이 기본으로 켜져 있고, vLLM ≥0.20이 필요하며, bf16은 H100 2장이 든다.

### 공급사별 접근·정책 제약 (공통 위험)

- **아동 성착취, 그루밍, 자해 조장 관련 출력은 세 회사 모두 금지하며, 연구 목적 면제 조항이 없다.**
  - Anthropic: 미성년자 유인이 탐지되면 당국에 신고한다.
  - OpenAI: 반복 위반 시 identifier 단위 차단 후 조직 단위 중단이 가능하다.
  - Google: 필터에 걸린 요청은 사람이 검토한다.
- 따라서 고위험 문항을 API로 실행하기 전에 **기관 승인과 공급사 사전 문의**가 필요하다 (→ `DECISION_REQUEST.md` 6번).
- **데이터 보관:**
  - OpenAI: 남용 모니터링 목적으로 30일 보관한다. `store: false`를 명시해야 한다.
  - Anthropic: 30일 보관한다. 위반으로 flag되면 최대 2년 보관한다.
  - Google 유료: 55일 보관한다.
  - 보관을 없애는 ZDR(무보관 옵션)은 별도 계약이 필요하다.
- **한국 이용:** 세 회사 모두 South Korea를 지원 지역으로 명시한다.

---

## 2. 우선 Pilot 실행 대상 (연결 확인용, 성능 결과로 쓰지 않음)

| 구분 | 모델 | ID / checkpoint | 이유 | 비고 |
|---|---|---|---|---|
| **Pilot API** | **GPT-6 Luna** | `gpt-6-luna` | 최저가($0.10 / $0.50)이고 `reasoning.effort: "none"`을 지원해서 **temperature 0.0 / top_p 1.0을 그대로 보낼 수 있다**. Chat Completions를 지원하므로 현재 OpenAI 호환 클라이언트를 그대로 쓸 수 있다 | max 토큰 파라미터는 `max_completion_tokens`. `store: false` 추가 필요 |
| **Pilot Local** | **Qwen3-8B** | `Qwen/Qwen3-8B` @ `b968826d9c46dd6066d109eabc6255188de91218` (서버에 캐시됨) | 서버에 이미 있고, 기존 vLLM 0.11.0으로 바로 실행할 수 있다. bf16 16.4 GB (계산값), H100 1장 | `enable_thinking: false` (models.yaml에 설정해 둠) |

Pilot 비용은 `config/pilot_live.yaml` 기준 4문항 × 1회라 1센트 미만이다 (계산값).

---

## 3. 전체 실행 예상 비용 (계산값, 상한 추정)

**가정**
- 480문항 = 단일턴 240 + 3턴 240.
- 문항당 3회 반복.
- 시스템 프롬프트 + 발화는 턴당 약 250 토큰이고, 이후 턴은 사용자 발화 약 150 토큰이 추가된다.
- 응답은 매 턴 1,024 토큰(상한)이다.
- 이 가정으로 입력 약 **3.26M**, 출력 약 **2.95M** 토큰이 된다.
- **thinking·reasoning 토큰은 포함하지 않았다.** 출력 한도를 늘리기로 결정하면 출력 비용이 크게 늘 수 있다.

| 모델 | Standard | Batch (50%) |
|---|---|---|
| GPT-6 Astra | 약 $180 | 약 $90 |
| GPT-6.1 Sol (대안) | 약 $36 | 약 $18 |
| Claude Opus 5.5 | 약 $72 | 약 $36 |
| Claude Sonnet 5.5 (대안) | 약 $36 | 약 $18 |
| Gemini 3.1 Pro Preview | 약 $42 | 약 $21 |
| Gemini 3.8 Flash (대안) | 약 $14 (2026년 가격) | 약 $7 |
| GPT-6 Luna (Pilot) | 약 $2 | 약 $1 |
| Qwen3.8-27B / Kanana-2 (로컬) | GPU 자체 보유, API 비용 없음. 소요 시간은 **확인 필요** (실측 후 기입) | — |

- 추천 3종(Astra, Opus 5.5, Gemini 3.1 Pro)으로 한 번 실행하면 약 **$294** (Batch 약 $147)이다.
- 파일럿, 재실행, 판정(Judge) 비용은 별도다.

---

## 4. 로컬 실행 환경

- **GPU:** NVIDIA H100 80GB × 2 (공유 서버, 다른 세션과 같이 씀). 모델 서버는 평가하는 동안만 띄운다.
- **현재 vLLM:** `evconflict/.venv`에 0.11.0이 있다. Qwen3-8B, Mi:dm 2.0은 이 버전으로 돌지만, **Qwen3.8·3.6, EXAONE 4.5, HyperCLOVA X SEED 14B는 돌지 않는다.** 최종 로컬 모델용으로 **새 vLLM(≥0.20, 최신 0.30.0) 전용 venv**가 필요하다.
- **서버에 캐시된 checkpoint:**
  - Qwen3-8B `b968826d…`
  - Qwen3-8B-FP8 `220b46e3…`
  - Qwen3-14B-FP8 `9a283b4a…`
  - Qwen3-32B-FP8 `aa55da1e…`
  - 한국 모델은 캐시된 것이 없다.
- **재현성:** temperature 0이어도 vLLM 배치 조건에 따라 출력이 완전히 같지 않을 수 있다. Haiku 4.5 공식 문서도 같은 점을 명시한다. 3회 반복 간 불일치율을 함께 기록하기를 권장한다.

## 5. Runner 연결 시 필요한 작업 (후보 확정 후)

- OpenAI(GPT-6 계열): 지금의 OpenAI 호환 클라이언트로 가능하다. 추가로 필요한 것:
  - `max_completion_tokens`
  - `reasoning_effort`
  - `store: false`
  - 모델별로 temperature·top_p를 **보내지 않는 옵션**
- Anthropic과 Gemini: 전용 어댑터가 필요하다. 각 회사 공식 SDK나 API 형식을 쓰고, OpenAI 호환 shim은 쓰지 않는다. 차단 신호도 매핑해야 한다.
  - Anthropic: `stop_reason: refusal`
  - Gemini: `promptFeedback.blockReason`, `finishReason: SAFETY/PROHIBITED_CONTENT`
- 새로 `finish_reason: length`(한도 초과 잘림)의 집계 규칙이 필요하다. thinking이 한도를 소진해서 답변이 비는 경우를 어떻게 셀지 정해야 한다.
- runs에는 모델 버전 외에 **실제로 적용된 생성 조건**(reasoning·thinking 설정 포함)을 기록할 필드가 없다. 코드북 v0.3 반영이 필요하다.

---

## 공식 출처 (2026-09-30 확인)

**OpenAI**
- https://developers.openai.com/api/docs/models
- https://developers.openai.com/api/docs/models/gpt-6-astra, …/gpt-6.1-sol, …/gpt-6-luna
- https://developers.openai.com/api/docs/pricing
- https://developers.openai.com/api/docs/guides/latest-model
- https://developers.openai.com/api/docs/guides/reasoning
- https://developers.openai.com/api/docs/guides/safety-checks
- https://developers.openai.com/api/docs/guides/safety-checks/under-18-api-guidance
- https://developers.openai.com/api/docs/guides/your-data
- 사용정책·가격 원문(openai.com)은 403으로 막혀 직접 대조하지 못했다 (**확인 필요**).

**Anthropic**
- https://platform.claude.com/docs/en/about-claude/models/overview.md
- https://platform.claude.com/docs/en/about-claude/pricing.md
- https://platform.claude.com/docs/en/models/opus-5-5/overview.md
- https://platform.claude.com/docs/en/models/sonnet-5-5/overview.md
- https://platform.claude.com/docs/en/models/haiku-4-5/overview.md
- https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback.md
- https://www.anthropic.com/legal/aup
- https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data
- https://www.anthropic.com/supported-countries

**Google**
- https://ai.google.dev/gemini-api/docs/models
- https://ai.google.dev/gemini-api/docs/models/gemini-3.1-pro-preview
- https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/changelog
- https://ai.google.dev/gemini-api/docs/thinking
- https://ai.google.dev/gemini-api/docs/safety-settings
- https://ai.google.dev/gemini-api/docs/usage-policies
- https://ai.google.dev/gemini-api/terms
- https://policies.google.com/terms/generative-ai/use-policy
- https://ai.google.dev/gemini-api/docs/available-regions

**공개 가중치**
- https://huggingface.co/Qwen/Qwen3.8-27B (+LICENSE)
- https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- https://huggingface.co/Qwen/Qwen3-8B
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://huggingface.co/kakaocorp/kanana-2-30b-a3b-instruct-2601 (+LICENSE)
- https://huggingface.co/LGAI-EXAONE/EXAONE-4.5-33B (+LICENSE)
- https://docs.vllm.ai/en/latest/models/supported_models/
- https://github.com/vllm-project/vllm/releases
- 기타 조사 후보: HyperCLOVA X SEED Think 14B/32B, Upstage Solar-Open, KT Mi:dm 2.0, SKT A.X. Solar-Open과 A.X-K는 H100 2장에 들어가지 않는다.
