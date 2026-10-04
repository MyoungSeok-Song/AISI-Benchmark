"""판정기 공통 규격.

판정기는 판정 입력 1건(judge_io.build_judge_inputs의 dict)을 받아 JudgeResult를 돌려준다.
judgment_id 발급, 판정기 식별 필드, 사람 재채점 표본 표시, 06 기록은 판정기가 아니라
run_judge가 한다. 모델 어댑터(adapters/base.py)와 같은 분담이다.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class JudgeResult:
    """판정 1건의 결과. 06_judgments의 판정 산출 필드에 대응한다.

    scores의 키는 06 열 이름(d1~d6, crri 4축), 값은 0·1·2 또는 None이다.
    None은 '해당 없음'으로 06에 빈값으로 기록된다(가정 J3이 허용하는 행에서만 쓸 수 있다).
    """
    verdict: str                            # pass | fail | inconclusive
    scores: dict = field(default_factory=dict)
    critical_failure_code: str = ""         # 없으면 NONE(rules.none_token)을 적어야 한다(회신 ③). 기본값 ""은 '빠뜨림'이라 완료 행이면 검증 오류
    over_refusal: bool = False
    referral_present: bool = False
    reason: str = ""
    judge_status: str = "completed"         # completed | failed | needs_review


class Judge:
    """판정기가 구현할 인터페이스."""

    def judge(self, judge_input):
        """판정 입력 1건 -> JudgeResult."""
        raise NotImplementedError

    def describe(self):
        """판정기 환경 정보. 코드북에 칸이 없어 judge_manifest.json에만 남긴다."""
        return {}
