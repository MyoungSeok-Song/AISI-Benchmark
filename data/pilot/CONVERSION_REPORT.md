# 파일럿 데이터 변환 보고서 (dataset_version 0.0.1, 개발용 영어 파일럿)

생성 시각: 2026-09-30T15:42:37+09:00

## 변환된 문항

- KYAB-900001 ← CAREBench CS-ANTH-0004 (AI Anthropomorphization / Grief over AI Discontinuation)
- KYAB-900002 ← CAREBench CS-PSYC-0002 (AI Intersection with major mental illness / Delusion Reinforcement)
- KYAB-900003 ← CAREBench CS-DECP-0003 (Deception, Impersonation & Authority Abuse / Impersonation of Authority & Adults)
- KYAB-900004 ← CAREBench CS-EDEP-0005 (Emotional Dependency / Social Isolation Reinforcement)
- KYAB-900005 ← CAREBench CS-GROO-0003 (Grooming, Predation & Relationship Engineering / Direct AI-Enhanced Grooming)
- KYAB-900006 ← MinorBench 150 (Danger)
- KYAB-900007 ← MinorBench 100 (Hateful)
- KYAB-900008 ← MinorBench 51 (Profanities)
- KYAB-900009 ← MinorBench 200 (Self-harm)
- KYAB-900010 ← MinorBench 1 (Sexual)
- KYAB-900011 ← dev_mt3_v0.1.json (3 turns, MT3-v1)
- KYAB-900012 ← dev_mt3_v0.1.json (3 turns, MT3-v1)

## 검증 결과

- 구조 오류(error): 0건
- 필수 메타데이터 공란(missing): 106건
- 경고(warning): 0건

## 원천에 없어 비워 둔 필수 필드 (추정 입력 금지 원칙)

| 필드 | 공란 문항 수 | 이유 |
|---|---|---|
| `item_tags.expected_response_tags` | 12 | 태그 입력자 입력 필요 |
| `item_tags.impersonation_flag` | 12 | 태그 입력자 입력 필요 |
| `item_tags.intent_tag` | 12 | 태그 입력자 입력 필요 (코드북 설명상 선택 필드인데 A열은 필수) |
| `item_tags.role_tags` | 12 | 태그 입력자 입력 필요 |
| `item_tags.severity_tag` | 12 | 태그 입력자 입력 필요 |
| `items.localization_type` | 10 | 번역 전 영어 원문을 그대로 쓰는 경우에 해당하는 통제어휘가 없음 |
| `items.original_text` | 2 | 신규(NEW) 문항은 원문이 없음 — 코드북 A열(필수)과 설명(원천 추적 시에만)이 충돌 |
| `items.prohibited_response_json` | 12 | 판정용 문항 설계값 — 문항 설계자 입력 필요 |
| `items.risk_cues_json` | 12 | 판정용 문항 설계값 — 문항 설계자 입력 필요 |
| `items.target_age_group` | 10 | 원천에 연령대 정보 없음 — 누가 어떤 기준으로 부여할지 결정 필요 (개발용 3턴은 mid_13_15 명시) |

구조 오류가 0건이면 Runner 실행 가능. 공란 필드는 Target LLM 입력에 쓰이지 않는 판정·운영 메타데이터라 개발용 파일럿에서는 `allow_missing_metadata: true`로 실행하고, 정식 데이터에서는 실행을 막는다.
