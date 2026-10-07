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
  408·409·429·5xx(500·502·503·504)·529     -> error, 재시도 대상 (base.RETRYABLE_HTTP_COMMERCIAL)

확인 필요 (실제 호출 전, 공식 문서·실응답으로 대조)
  * GPT-5.6 Terra의 API 모델 이름, temperature·top_p 수용 여부(추론 모델은 기본값만 받을 수 있음
    → 거부되면 models.yaml omit_params에 추가)
  * 정책 위반 HTTP 400의 error.code 값 (policy_error_codes)
  * message.refusal 필드가 일반 대화 응답에서 채워지는 조건
"""
from . import http_json
from .base import blocked_result, chat_completion_result
from .commercial import HttpCommercialAdapter, http_error_result, sent_params

_PARAM_FIELDS = {"temperature": "temperature", "top_p": "top_p", "max_output_tokens": "max_completion_tokens"}
_DEFAULT_POLICY_CODES = ("content_policy_violation", "content_filter")


class OpenAIAdapter(HttpCommercialAdapter):
    provider_label = "openai chat.completions"
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, model, transport=http_json.post_json):
        super().__init__(model, transport)
        self._policy_codes = tuple(self._options.get("policy_error_codes", _DEFAULT_POLICY_CODES))

    def _endpoint(self, base):
        return base + "/chat/completions"

    def _headers(self):
        return {"Authorization": f"Bearer {self._api_key}"}

    def build_request(self, messages, params):
        body = {"model": self._api_model, "messages": messages, "store": False}
        for name, value in sent_params(self._model, params).items():
            body[_PARAM_FIELDS[name]] = value
        return {**body, **self._extra_body}

    def _on_http_error(self, reply):
        """정책 위반 400은 공급자 차단(blocked)으로 본다."""
        error = reply.body.get("error", {}) if isinstance(reply.body, dict) else {}
        if reply.status == 400 and isinstance(error, dict) and error.get("code") in self._policy_codes:
            return blocked_result(reply.body, detail=error.get("message", ""))
        return http_error_result(reply)

    def _parse(self, raw):
        # refusal 필드에 거절 문장이 따로 오면 그것이 모델의 응답 본문이다(모델 스스로의 거절 = success).
        return chat_completion_result(raw, refusal_as_text=True)
