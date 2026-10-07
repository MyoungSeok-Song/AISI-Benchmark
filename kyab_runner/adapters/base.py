"""모델 어댑터 공통 규격.

어댑터는 공급자 호출 1회를 맡고, 결과를 코드북 05_responses의 어휘로 정규화해 돌려준다.
예외를 밖으로 던지지 않는다. 실패도 AdapterResult로 표현한다.
재시도·기록·대화 이력 관리는 어댑터가 아니라 실행기(session)가 한다.
"""
from dataclasses import dataclass, field

from ..vocab import BLOCK_SOURCE_PROVIDER, RESPONSE_BLOCKED, RESPONSE_EMPTY, RESPONSE_SUCCESS


@dataclass(frozen=True)
class CallInfo:
    """호출의 위치 정보. 기록용이며, 모의 어댑터는 시나리오 선택에도 쓴다."""
    item_id: str
    turn_index: int
    rollout_no: int
    attempt: int          # 같은 턴의 몇 번째 시도인지 (1부터)


@dataclass(frozen=True)
class AdapterResult:
    """호출 1회의 결과. 필드 이름은 05_responses와 맞춘다."""
    response_status: str            # success | blocked | error | timeout | empty
    response_text: str = ""         # 모델이 출력한 본문. 없으면 빈 문자열
    raw_response: dict = field(default_factory=dict)   # 공급자 원본 응답 객체. 응답 자체가 없으면 {}
    finish_reason: str = ""         # 정규화 값 (config finish_reasons)
    block_source: str = ""          # 차단일 때만: provider | model | local_guardrail | unknown
    error_code: str = ""            # 정규화 오류 코드
    error_message: str = ""         # 공급자·파이프라인 원문 메시지
    retryable: bool = False         # 같은 요청을 다시 보내 볼 만한 일시 오류인가
    latency_ms: int = 0             # 보조 로그용. CSV에는 칸이 없다


# ── 결과 만들기 (모든 어댑터가 같은 규칙을 쓰도록 여기에 둔다) ─────────
def text_result(raw, text, finish_reason, latency_ms=0):
    """모델이 돌려준 본문으로 결과를 만든다.

    본문이 있으면 success. 모델이 스스로 거절한 문장도 본문이므로 success다.
    본문이 비면 empty. 추론 토큰이 출력 한도를 다 써서 본문이 없는 경우가 여기에 해당하며,
    그때 finish_reason은 length로 남는다.
    """
    common = dict(raw_response=raw, finish_reason=finish_reason, latency_ms=latency_ms)
    if not (text or "").strip():
        return AdapterResult(RESPONSE_EMPTY, error_code="empty_response", error_message="응답 본문이 비어 있음", **common)
    return AdapterResult(RESPONSE_SUCCESS, response_text=text, **common)


def blocked_result(raw, detail="", latency_ms=0, block_source=BLOCK_SOURCE_PROVIDER):
    """공급자 안전장치가 응답을 막은 경우. 본문은 없고 finish_reason은 content_filter로 통일한다."""
    return AdapterResult(RESPONSE_BLOCKED, raw_response=raw, finish_reason="content_filter",
                         block_source=block_source, error_message=detail, latency_ms=latency_ms)


def normalize_finish_reason(value, mapping):
    """공급자 종료 사유 -> 정규화 어휘(config finish_reasons). 목록에 없으면 other, 값이 없으면 빈 값."""
    return mapping.get(value, "other") if value else ""


class Adapter:
    """어댑터가 구현할 인터페이스."""

    def complete(self, messages, params, call_info):
        """messages: [{role, content}], params: {temperature, top_p, max_output_tokens}."""
        raise NotImplementedError

    def describe(self):
        """실행 환경 정보(서버 버전, dtype 등). 코드북에 칸이 없어 manifest·보조 로그에만 남긴다."""
        return {}
