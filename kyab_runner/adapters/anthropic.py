"""Anthropic 어댑터 (Messages API, 공식 파이썬 SDK `anthropic` 1.x).

요청
  client.messages.create(model, max_tokens, system, messages, extra_body)
  system 메시지는 system 인자로, user·assistant는 messages로 보낸다.
  Messages API는 호출마다 독립이라 공급자 쪽에 대화가 남지 않는다(매번 전체 이력 전송).

파라미터
  * SDK 1.x는 temperature·top_p를 인자로 받지 않는다. 받는 모델에는 extra_body로 넣는다.
    평가 대상 Claude Sonnet 4.6은 temperature를 받는다(결정성 요구가 있어 유지).
  * Claude 4.x는 temperature와 top_p를 함께 지정할 수 없다 → models.yaml omit_params: [top_p].
  * thinking은 지정하지 않는다. Sonnet 4.6은 지정하지 않으면 thinking 없이 응답한다.
  * 다른 모델로 넘기는 fallbacks는 쓰지 않는다(평가 대상 모델의 응답만 기록해야 함).
  * SDK 자체 재시도는 끈다(max_retries=0). 재시도는 러너가 하고 보조 로그에 남긴다.

응답 해석
  stop_reason=refusal (안전 분류기가 응답을 거절)  -> blocked (provider)
  본문 있음 (모델이 쓴 거절 문장 포함)             -> success
  본문 없음                                       -> empty (stop_reason=max_tokens이면 length)
  408·409·429·5xx(500·502·503·504)·529·연결 오류    -> error, 재시도 대상 (base.RETRYABLE_HTTP_COMMERCIAL)

확인 필요 (실제 호출 전)
  * stop_reason=refusal을 block_source=provider로 볼지 model로 볼지 (코드북 block_source 정의 협의)
"""
import anthropic
from anthropic import DefaultHttpxClient

from .base import (RETRYABLE_HTTP_COMMERCIAL, AdapterResult, blocked_result, connection_error_result,
                   normalize_finish_reason, text_result, timeout_result)
from .commercial import CommercialAdapter, sent_params
from ..vocab import RESPONSE_ERROR

_FINISH_REASONS = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls"}
# SDK 인자로 넘길 수 없어 extra_body로 보내는 샘플링 파라미터
_BODY_PARAMS = ("temperature", "top_p")


class AnthropicAdapter(CommercialAdapter):
    provider_label = "anthropic messages"

    def __init__(self, model, http_client=None):
        """http_client: 테스트가 가짜 전송 계층을 넣을 때만 쓴다(DefaultHttpxClient)."""
        super().__init__(model)
        self._client = anthropic.Anthropic(api_key=self._api_key, timeout=float(self._timeout), max_retries=0,
                                           http_client=http_client or DefaultHttpxClient())

    def build_request(self, messages, params):
        """messages.create에 넘길 인자."""
        sent = sent_params(self._model, params)
        request = {
            "model": self._api_model,
            "max_tokens": sent["max_output_tokens"],
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"],
            "extra_body": {**{k: sent[k] for k in _BODY_PARAMS if k in sent}, **self._extra_body},
        }
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if system:
            request["system"] = system
        return request

    def complete(self, messages, params, call_info):
        try:
            message = self._client.messages.create(**self.build_request(messages, params))
        except anthropic.APITimeoutError:                 # APIConnectionError의 하위 클래스라 먼저 잡는다
            return timeout_result(self._timeout)
        except anthropic.APIConnectionError as exc:
            return connection_error_result(exc)
        except anthropic.APIStatusError as exc:           # RateLimitError(429)도 이 하위 클래스다
            return _status_error(exc, retryable=exc.status_code in RETRYABLE_HTTP_COMMERCIAL)
        return _parse(message.to_dict())


def _status_error(exc, retryable):
    body = exc.body if isinstance(exc.body, dict) else {}
    return AdapterResult(RESPONSE_ERROR, raw_response=body, error_code=f"http_{exc.status_code}",
                         error_message=str(exc.message)[:500], retryable=retryable)


def _parse(raw):
    stop_reason = raw.get("stop_reason")
    if stop_reason == "refusal":
        details = raw.get("stop_details") or {}
        return blocked_result(raw, detail=f"stop_reason=refusal category={details.get('category')}")
    text = "".join(block.get("text", "") for block in raw.get("content") or [] if block.get("type") == "text")
    return text_result(raw, text, normalize_finish_reason(stop_reason, _FINISH_REASONS))
