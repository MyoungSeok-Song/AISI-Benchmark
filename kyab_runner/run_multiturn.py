"""다중턴 실행기 (MT3: 3턴 고정 대본).

실행 1건 = 문항 1 × 모델 1 × 반복 번호 1 = 모델 호출 최대 3회.
수행계획서 표 Ⅳ-20: 사전 정의된 3턴 고정 대본을 쓰고, 모델 응답과 무관하게
다음 턴 입력을 그대로 투입한다.

  * 사용자 발화는 03_prompts의 대본 그대로다. 모델 응답에 따라 바꾸지 않는다.
  * assistant 자리에는 이 실행에서 모델이 실제로 한 응답을 넣는다.
  * 대화 이력은 실행마다 새로 만든다. 공급자 쪽 대화 저장 기능은 쓰지 않는다.
    매 턴 전체 메시지를 다시 보내므로 다른 실행의 내용이 섞일 수 없다.
  * 어느 턴이 성공하지 못하면(차단·오류·시간초과·빈 응답) 거기서 멈춘다.
    뒤 턴은 앞 턴 응답이 있어야 보낼 수 있기 때문이다.
  * 치명적 실패에 따른 즉시 종료는 하지 않는다. 실행 중에 판정기가 없으므로
    끝까지 진행하고, first_fail_turn·first_cfc_turn은 판정 후에 채운다(C3).

MT7·MT10은 턴 수만 다른 같은 규칙이므로 --protocol만 바꿔 실행한다.

사용 예 (runner/ 폴더에서)
  .venv/bin/python -m kyab_runner.run_multiturn --allow-unverified
  .venv/bin/python -m kyab_runner.run_multiturn --allow-unverified --mock-plan samples/mock_plan_failures.yaml
"""
import sys

from . import cli
from .messages import build_messages, user_content
from .vocab import RESPONSE_SUCCESS

DEFAULT_PROTOCOL = "MT3-1.0.0"


def conduct_multiturn(session, turns):
    """다중턴 대화 1건: 대본의 발화를 순서대로 보내며 실제 응답을 이력에 쌓는다."""
    config = session.batch.config
    history = []                                    # [(보낸 user 문자열, 모델의 실제 응답)]
    for turn in turns:                              # turn_index 오름차순
        messages = build_messages(config, history, turn)
        result = session.turn(turn, messages)
        if result.response_status != RESPONSE_SUCCESS:
            break                                   # 이 턴의 응답이 없어 다음 턴을 이어 갈 수 없다
        history.append((user_content(turn, config), result.response_text))


def main(argv=None):
    return cli.main(__doc__, DEFAULT_PROTOCOL, cli.MODE_MULTI, conduct_multiturn, argv)


if __name__ == "__main__":
    sys.exit(main())
