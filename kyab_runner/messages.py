"""모델 요청 메시지 구성.

코드북 'AI 전달' 열에서 ○(항상)·△(해당 시)인 값만 모델에 보낸다.
  system_prompt_text (04, ○)  system 메시지. 모든 실행에 같은 문자열
  message_text       (03, ○)  해당 턴의 user 메시지 본문. 글자 하나 바꾸지 않는다
  context_text       (03, △)  값이 있는 턴에만, user 메시지 앞에 붙인다 (C2 기본값)

그 밖의 필드(태그·기대응답·금지응답·위험 단서·페르소나 메모 등)는 이 모듈에 들어오지
않는다. 실행기가 넘기는 것은 03_prompts 행뿐이다.
"""


def user_content(turn, config):
    """03_prompts 행 1개 -> 그 턴에 보낼 user 메시지 문자열."""
    if turn["context_text"] and config["context_position"] == "user_prefix":
        return turn["context_text"] + config["context_separator"] + turn["message_text"]
    return turn["message_text"]


def build_messages(config, history, turn):
    """이번 턴의 요청 메시지 배열.

    history: 앞 턴들의 (보낸 user 문자열, 모델이 실제로 한 응답) 목록. 단일턴은 빈 목록.
    반환값은 05 request_messages_json에 그대로 저장된다.
    """
    messages = [{"role": "system", "content": config.system_prompt_text}]
    for sent, answered in history:
        messages.append({"role": "user", "content": sent})
        messages.append({"role": "assistant", "content": answered})
    messages.append({"role": "user", "content": user_content(turn, config)})
    return messages
