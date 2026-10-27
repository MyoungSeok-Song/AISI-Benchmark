# 시범 50문항 실행 파이프라인

생성 → 채점 → 계산 → 결과 꾸러미. 채점 기준·계산은 연구팀 패키지(`reference.py`)를 그대로 호출한다.

| 단계 | 명령 | 결과 |
|---|---|---|
| 1. 응답 생성 | `python3 pipeline/generate.py <config>` | `runs/<run_tag>/dialogues/<대상>/<문항>__<연령>_<성별>.json`, `logs/generation.jsonl` |
| 2. 채점·계산 | `python3 pipeline/judge.py <config>` | `inputs/`·`judgments/`(원평정)·`results/` (채점 모델별 폴더), `logs/judging.jsonl` |
| 3. 꾸러미 | `python3 pipeline/package.py <config> tools/prices.json` | `report/` — 대화별 꾸러미, 결과요약.csv, 집계.json, 판단어려운사례, 사용량_비용.md |

- 작업 폴더 `llm_pilot50/`에서 실행. OpenAI 호출 시 `.pylib`(openai 패키지)와 상위 폴더 `.env`의 `OPENAI_API_KEY`를 쓴다.
- 이미 만든 대화·결과는 건너뛴다(중단 후 다시 실행하면 이어서 진행). 원본 기록은 덮어쓰지 않는다.
- 연령·성별 주입 방식이 `pending`이면 생성기가 실행을 거부한다(`01_결정기록.md` 3절). 결정되면 config의 `persona`를 `decided`로 채운다.
- `smoke: true` + `persona.status=none_smoke`는 파이프라인 점검용이며 결과를 보고에 쓰지 않는다.
- 일부만 돌릴 때: config에 `"items": [...]`, `"conditions": ["13-15/F", ...]`.

설정 예: `config.example.json`(실사용 틀), `config.mock_smoke.json`(가짜 모델로 전체 점검).

## 설정 항목
| 키 | 내용 |
|---|---|
| `card_version` | 쓸 조건표 판본(예: `cond-v0.2-20261026`). 없으면 최신 고정본. 대화마다 기록되며 채점·꾸러미는 그 판본을 쓴다 |
| `concurrency` | `{"generation": N, "judge": N}` 동시 호출 수(대화 단위 병렬, 한 대화의 턴은 순서대로) |
| `retries` | `generation`: 기술 오류 재시도 횟수, `judge`: 형식·근거 검증 실패 재시도 횟수 |
| `judge_feedback` | 기본 true. 채점 재시도 때 직전 응답과 실패 이유를 덧붙여 다시 요청(원 요청은 그대로) |
| 대상·채점 모델의 `backoff` | `{"max_tries", "base_seconds", "max_seconds"}` 429·시간초과·연결·5xx 오류 시 대기 후 재전송. `Retry-After`가 있으면 따름. 대기는 로그의 `waits`에 기록 |
| `persona.status` | `pending`(생성 차단) / `none_smoke`(점검 전용) / `decided`(템플릿 앞붙이기·system) / `variants`(1번 방식: 변형 파일) / `context`(2번 방식: 이용자 정보 system 메시지) / `variants+context` |

### 변형 파일 형식 (1번 방식)
```json
{"variant_version": "…", "rule_version": "변형 규칙 v0.2",
 "items": [{"item_id": "…", "age_band": "13-15", "gender": "F",
            "turns": [{"turn": 1, "text": "…"}],
            "persona_record": {"실제_페르소나": "…", "발화_속_주장": "…", "위장_태그": "…"}}]}
```
- 문항 × 조건마다 한 항목. 턴 수·턴 번호는 원문과 같아야 하며, 첫 API 호출 전에 전체를 검사해 하나라도 어긋나면 아무것도 생성하지 않는다.
- `persona_record`(위장 태그 등)는 대화 기록에만 남고 평가 대상 AI에는 보내지 않는다.

### 2번 방식 설정 예
```json
"persona": {"status": "context", "context": {"template": "…{age}…{gender}…", "age_text": {"10-12": "…", "13-15": "…", "16-18": "…"}, "gender_text": {"M": "…", "F": "…"}}}
```

## 꾸러미 산출물 (`report/`)
결과_<run_tag>.xlsx(요약·대화 원문·턴별 점수·E/F/CFC·적용 조건과 근거·집계·판단 어려운 사례), 결과요약.csv, 집계.json, 채점일치도.md/.json(판정 κ, 차원별 가중 κ, CFC, 자기 채점 비교), 판단어려운사례.md/.json, 사용량_비용.md, bundles/(대화별 전체 기록).

## 처리 규칙 (01_결정기록과 동일)
- 생성: 호출 오류·빈 응답·길이 잘림은 기술 오류로 재시도(`retries.generation`). 끝까지 실패하면 그 턴을 `missing`으로 두고 이후 턴 미진행, `execution_status=error`. 공급사 필터(`content_filter`)로 받은 응답은 그대로 채점하고 표시.
- 채점: JSON 파싱·스키마·근거 인용 검증(`reference.validate_judgment`) 실패 시 재시도(`retries.judge`). 끝까지 실패한 job은 원평정 누락으로 남기며 0점·통과로 채우지 않는다.
- 집계: 패키지 `summarize_runs`의 평가 단위 키에 이용자 조건(연령·성별)이 없어, 이용자 조건별로 나눠 호출하고 전체는 같은 공식으로 분자·분모를 합산한다.
