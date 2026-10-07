"""모델 요청 메시지 구성.

코드북 'AI 전달' 열에서 ○(항상)·△(해당 시)인 값만 모델에 보낸다.
  system_prompt_text (04, ○)  system 메시지. 모든 실행에 같은 문자열
  message_text       (03, ○)  해당 턴의 user 메시지 본문. 글자 하나 바꾸지 않는다
  context_text       (03, △)  값이 있는 턴에만, user 메시지 앞에 붙인다 (C2 기본값)

그 밖의 필드(태그·기대응답·금지응답·위험 단서·페르소나 메모 등)는 이 모듈에 들어오지
않는다. 실행기가 넘기는 것은 03_prompts 행뿐이다.
"""


CONTEXT_POSITIONS = ("user_prefix",)      # 구현된 context_text 위치. runner.yaml context_position은 이 중 하나여야 한다


def _user_content(config, turn):
    """03_prompts 행 1개 -> 그 턴에 보낼 user 메시지 문자열.

    지원하지 않는 context_position이면 context_text가 조용히 빠지는 대신 첫 호출에서 ValueError(설정 로드 때도 검사한다).
    """
    position = config["context_position"]
    if position not in CONTEXT_POSITIONS:
        raise ValueError(f"runner.yaml context_position={position!r}는 지원하지 않습니다 (지원: {CONTEXT_POSITIONS})")
    if turn["context_text"]:
        return turn["context_text"] + config["context_separator"] + turn["message_text"]
    return turn["message_text"]


def build_messages(config, history, turn):
    """이번 턴의 요청 메시지 배열.

    history: 앞 턴들의 (보낸 user 문자열, 모델이 실제로 한 응답) 목록. 단일턴은 빈 목록.
             보낸 문자열은 그 턴 요청의 messages[-1]["content"]와 같다(실행기가 그 값을 그대로 쌓는다).
    반환값은 05 request_messages_json에 그대로 저장된다.
    """
    messages = [{"role": "system", "content": config.system_prompt_text}]
    for sent, answered in history:
        messages.append({"role": "user", "content": sent})
        messages.append({"role": "assistant", "content": answered})
    messages.append({"role": "user", "content": _user_content(config, turn)})
    return messages
