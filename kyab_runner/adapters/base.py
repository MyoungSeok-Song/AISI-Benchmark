"""모델 어댑터 공통 규격.

어댑터는 공급자 호출 1회를 맡고, 결과를 코드북 05_responses의 어휘로 정규화해 돌려준다.
예외를 밖으로 던지지 않는다. 실패도 AdapterResult로 표현한다.
재시도·기록·대화 이력 관리·턴 지연 측정은 어댑터가 아니라 실행기(session)가 한다.
"""
from dataclasses import dataclass, field

from ..vocab import BLOCK_SOURCE_PROVIDER, RESPONSE_BLOCKED, RESPONSE_EMPTY, RESPONSE_ERROR, RESPONSE_SUCCESS, RESPONSE_TIMEOUT

# 다시 보내 볼 만한 HTTP 상태. 재시도는 상용이면 유료 중복 호출이므로 일시 장애만 넣는다(501·505 같은 비일시 오류는 제외).
#   상용: 408 시간 초과 · 409 공급자 쪽 충돌/동시성 · 429 요청 과다 · 5xx 일시 장애 · 529 Anthropic overloaded
#   로컬 vLLM: 409·529를 내지 않으므로 뺀다
RETRYABLE_HTTP_COMMERCIAL = (408, 409, 429, 500, 502, 503, 504, 529)
RETRYABLE_HTTP_LOCAL = (408, 429, 500, 502, 503, 504)

# chat completion(OpenAI 호환) 응답의 finish_reason -> 정규화 어휘(config finish_reasons). 목록에 없으면 other.
CHAT_COMPLETIONS_FINISH_REASONS = {"stop": "stop", "length": "length", "content_filter": "content_filter",
                                   "tool_calls": "tool_calls"}


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


class AdapterSetupError(ValueError):
    """어댑터를 만들 수 없을 때(API 키 없음, 서버 미기동, 잘못된 주소·계획). 실행 전 점검이 잡아 종료 2로 끝낸다."""


# ── 결과 만들기 (모든 어댑터가 같은 규칙을 쓰도록 여기에 둔다) ─────────
def text_result(raw, text, finish_reason):
    """모델이 돌려준 본문으로 결과를 만든다.

    본문이 있으면 success. 모델이 스스로 거절한 문장도 본문이므로 success다.
    본문이 비면 empty. 추론 토큰이 출력 한도를 다 써서 본문이 없는 경우가 여기에 해당하며,
    그때 finish_reason은 length로 남는다.
    """
    common = dict(raw_response=raw, finish_reason=finish_reason)
    if not (text or "").strip():
        return AdapterResult(RESPONSE_EMPTY, error_code="empty_response", error_message="응답 본문이 비어 있음", **common)
    return AdapterResult(RESPONSE_SUCCESS, response_text=text, **common)


def blocked_result(raw, detail="", block_source=BLOCK_SOURCE_PROVIDER):
    """공급자 안전장치가 응답을 막은 경우. 본문은 없고 finish_reason은 content_filter로 통일한다."""
    return AdapterResult(RESPONSE_BLOCKED, raw_response=raw, finish_reason="content_filter",
                         block_source=block_source, error_message=detail)


def timeout_result(timeout_s):
    return AdapterResult(RESPONSE_TIMEOUT, error_code="timeout", error_message=f"{timeout_s}초 안에 응답 없음", retryable=True)


def connection_error_result(exc):
    return AdapterResult(RESPONSE_ERROR, error_code="connection_error", error_message=str(exc), retryable=True)


def invalid_response_result(detail):
    """응답은 왔지만 읽을 수 없는 경우(JSON이 아님 등). 재시도 대상이 아니다."""
    return AdapterResult(RESPONSE_ERROR, error_code="invalid_response", error_message=str(detail)[:500])


def normalize_finish_reason(value, mapping):
    """공급자 종료 사유 -> 정규화 어휘(config finish_reasons). 목록에 없으면 other, 값이 없으면 빈 값."""
    return mapping.get(value, "other") if value else ""


def chat_completion_result(raw, refusal_as_text=False):
    """chat completion(OpenAI 호환) 원본 응답 -> AdapterResult. 원본은 손대지 않고 그대로 보존한다.

    refusal_as_text: OpenAI는 거절 문장을 message.refusal에 따로 담을 수 있다. 그것도 모델의 응답 본문(success)이다.
    """
    choice = (raw.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    finish = choice.get("finish_reason")
    if finish == "content_filter":
        return blocked_result(raw)
    text = message.get("content") or (message.get("refusal") if refusal_as_text else None) or ""
    return text_result(raw, text, normalize_finish_reason(finish, CHAT_COMPLETIONS_FINISH_REASONS))


class Adapter:
    """어댑터가 구현할 인터페이스."""

    def complete(self, messages, params, call_info):
        """messages: [{role, content}], params: {temperature, top_p, max_output_tokens}."""
        raise NotImplementedError

    def describe(self):
        """실행 환경 정보(서버 버전, dtype 등). 코드북에 칸이 없어 manifest·보조 로그에만 남긴다."""
        return {}
