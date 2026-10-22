# 페르소나 자리표시 (placeholder 0.0.1)

페르소나팀 자료가 오기 전, 실행 구조(문항 × 페르소나 × 반복)를 확인하기 위한 자리표시다.

- `personas.csv`: 연령대 3 × 성별 2 = 6벌. `persona_status=placeholder`.
- `persona_prompts.csv`: **없음.** 그래서 이 디렉터리로 실행하려면 config에 `persona_prompt_fallback: base`가 필요하고,
  모든 페르소나가 같은 기본 발화를 보낸다(배치 manifest의 `personas.prompt_source`에 기록됨).
  이 결과는 페르소나 간 비교에 쓰면 안 된다.

페르소나팀 자료가 오면 새 디렉터리(예: `data/personas/<버전>/`)에 같은 두 파일 형식으로 넣고,
config의 `persona_dir`만 바꾸고 `persona_prompt_fallback`을 지운다.

| 파일 | 열 |
|---|---|
| personas.csv | persona_id (`PERSONA-…`), persona_version, age_band, gender, persona_label, style_note, persona_status |
| persona_prompts.csv | persona_id, turn_id (prompts.csv의 turn), message_text (그 페르소나 말투로 다시 쓴 발화) |

모델에는 `persona_prompts.message_text`만 전달된다. 라벨·스타일 설명은 전달하지 않는다.
