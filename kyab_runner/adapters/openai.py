"""OpenAI 어댑터 (Chat Completions API, 표준 라이브러리 HTTP).

요청
  POST {base_url}/chat/completions, Authorization: Bearer <키>
  messages는 러너의 메시지 배열 그대로(system·user·assistant).
  store=false로 공급자 쪽 대화 저장을 끈다.
  출력 한도는 max_completion_tokens로 보낸다(추론 토큰 포함 한도).

응답 해석
  finish_reason=content_filter            -> blocked (provider)
  HTTP 400 + 정책 위반 오류 코드           -> blocked (provider)
  본문 있음 (거절 문장 포함)               -> success
  본문 없음                               -> empty. 추론이 한도를 다 쓴 경우 finish_reason=length
  429·5xx                                 -> error, 재시도 대상

확인 필요 (실제 호출 전, 공식 문서·실응답으로 대조)
  * GPT-5.6 Terra의 API 모델 이름, temperature·top_p 수용 여부(추론 모델은 기본값만 받을 수 있음
    → 거부되면 models.yaml omit_params에 추가)
  * 정책 위반 HTTP 400의 error.code 값 (policy_error_codes)
  * message.refusal 필드가 일반 대화 응답에서 채워지는 조건
"""
import time

from . import http_json
from .base import blocked_result, normalize_finish_reason, text_result
from .commercial import CommercialAdapter, connection_error_result, http_error_result, sent_params, timeout_result

_FINISH_REASONS = {"stop": "stop", "length": "length", "content_filter": "content_filter",
                   "tool_calls": "tool_calls"}
_PARAM_FIELDS = {"temperature": "temperature", "top_p": "top_p", "max_output_tokens": "max_completion_tokens"}
_DEFAULT_POLICY_CODES = ("content_policy_violation", "content_filter")


class OpenAIAdapter(CommercialAdapter):
    provider_label = "openai chat.completions"

    def __init__(self, model, transport=http_json.post_json):
        super().__init__(model)
        self._transport = transport
        self._url = self._options.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        self._policy_codes = tuple(self._options.get("policy_error_codes", _DEFAULT_POLICY_CODES))

    def build_request(self, messages, params):
        """요청 본문. 테스트가 실제 전송 내용을 확인할 수 있게 따로 둔다."""
        body = {"model": self._api_model, "messages": messages, "store": False}
        for name, value in sent_params(self._model, params).items():
            body[_PARAM_FIELDS[name]] = value
        return {**body, **self._extra_body}

    def complete(self, messages, params, call_info):
        began = time.monotonic()
        try:
            reply = self._transport(self._url, {"Authorization": f"Bearer {self._api_key}"},
                                    self.build_request(messages, params), self._timeout)
        except http_json.TransportTimeout:
            return timeout_result(self._timeout)
        except http_json.TransportFailure as exc:
            return connection_error_result(exc)
        latency_ms = int((time.monotonic() - began) * 1000)
        if not reply.ok:
            return self._error(reply, latency_ms)
        return self._parse(reply.body, latency_ms)

    def _error(self, reply, latency_ms):
        error = reply.body.get("error", {}) if isinstance(reply.body, dict) else {}
        if reply.status == 400 and isinstance(error, dict) and error.get("code") in self._policy_codes:
            return blocked_result(reply.body, detail=error.get("message", ""), latency_ms=latency_ms)
        return http_error_result(reply, latency_ms)

    @staticmethod
    def _parse(raw, latency_ms):
        choice = (raw.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        finish = choice.get("finish_reason")
        if finish == "content_filter":
            return blocked_result(raw, latency_ms=latency_ms)
        # refusal 필드에 거절 문장이 따로 오면 그것이 모델의 응답 본문이다(모델 스스로의 거절 = success).
        text = message.get("content") or message.get("refusal") or ""
        return text_result(raw, text, normalize_finish_reason(finish, _FINISH_REASONS), latency_ms)
