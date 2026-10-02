"""판정기 등록부. judges.yaml의 adapter 이름으로 판정기를 만든다.

지금은 모의 판정기뿐이다. LLM 판정기는 루브릭 본문·판정 프롬프트·CFC 목록을 받은 뒤
base.Judge 규격으로 추가한다.
"""
from .mock_judge import MockJudge


def create_judge(entry, rules):
    """JudgeEntry -> Judge."""
    if entry.adapter == "mock_judge":
        return MockJudge(entry, rules)
    raise ValueError(f"알 수 없는 판정기 '{entry.adapter}'")
