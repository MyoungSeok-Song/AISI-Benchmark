"""단일턴 실행기 (ST1).

실행 1건 = 문항 1 × 모델 1 × 반복 번호 1 = 모델 호출 1회.
요청은 [system] + [user]. 앞선 대화가 없다.

사용 예 (runner/ 폴더에서)
  .venv/bin/python -m kyab_runner.run_single --allow-unverified
  .venv/bin/python -m kyab_runner.run_single --validate-only
  .venv/bin/python -m kyab_runner.run_single --batch-id RBATCH-20260930-001 --allow-unverified   # 이어서
"""
import sys

from . import cli
from .messages import build_messages

DEFAULT_PROTOCOL = "ST1-1.0.0"


def conduct_single(session, turns):
    """단일턴 대화 1건: 하나뿐인 발화를 보내고 응답을 받는다."""
    turn = turns[0]
    messages = build_messages(session.batch.config, history=[], turn=turn)
    session.turn(turn, messages)


def main(argv=None):
    return cli.main(__doc__, DEFAULT_PROTOCOL, "single", conduct_single, argv)


if __name__ == "__main__":
    sys.exit(main())
