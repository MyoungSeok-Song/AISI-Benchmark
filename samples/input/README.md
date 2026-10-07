# 개발·시험용 샘플 입력 (6문항)

**공식 평가 문항이 아닙니다.** 러너의 실행·판정·집계 경로를 돌려 보기 위한 개발 샘플이며, `tools/build_samples.py`가 생성합니다(재생성하면 이 세 파일과 바이트 단위로 같아야 합니다). ID 대역 `KYAB-9000xx`(단일턴)·`KYAB-9001xx`(3턴)는 본평가 문항과 겹치지 않게 비워 둔 시험용 대역입니다. 분류 코드는 분류팀 문항대응799의 초안을 옮긴 것으로 확정이 아닙니다.

| item_id | 방식 | 유형 | 원천 | 원 문항 ID | 라이선스 |
|---|---|---|---|---|---|
| KYAB-900001 | 단일턴 | 위험 | CAREBench (`handshake-ai-research/CAREBench`, `prompts.csv`) 번역 | CS-ANTH-0016 | CC BY 4.0 |
| KYAB-900002 | 단일턴 | 안전 대조 | MinorBench (`govtech/MinorBench`, `test.csv`) 번역 | 53 | MIT |
| KYAB-900003 | 단일턴 | 위험 | CAREBench 번역 | CS-PSYC-0039 | CC BY 4.0 |
| KYAB-900101 | 3턴 | 위험(위기) | 러너 시험용으로 작성(NEW) | — | 내부(임시 코드 `LicenseRef-KYAB-internal`) |
| KYAB-900102 | 3턴 | 위험 | 러너 시험용으로 작성(NEW) | — | 내부 |
| KYAB-900103 | 3턴 | 안전 대조 | 러너 시험용으로 작성(NEW) | — | 내부 |

- 번역 3문항의 원문(`original_text`)은 원천 CSV에서 그대로 옮겼고, 원천 판본(HF 커밋)·sha256은 `config/sources.yaml`에 있습니다. 원천 원본 CSV 자체는 저장소에 넣지 않습니다(프로젝트 폴더 `data/`).
- 3턴 대본 3개는 러너 시험용으로 작성한 것이며 실제 사례가 아닙니다.
- `02_item_tags.csv`의 대조 문항 2건에는 대조 위험군 연결(`control_target_risk`, tag_revision 2)이 개발 샘플용 가정값으로 들어 있습니다.
