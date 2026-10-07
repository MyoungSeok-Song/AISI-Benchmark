"""Gemini 어댑터 (generateContent API, 표준 라이브러리 HTTP).

요청
  POST {base_url}/models/{모델}:generateContent, x-goog-api-key: <키>
  system 메시지 -> systemInstruction, user -> role user, assistant -> role model.
  generateContent는 호출마다 독립이라 공급자 쪽에 대화가 남지 않는다(매번 전체 이력 전송).
  safetySettings는 보내지 않는다(safety_profile=service_default: 서비스 기본 설정 그대로).

응답 해석
  promptFeedback.blockReason 있음 (후보 없음)  -> blocked (provider): 입력 단계 차단
  finishReason이 안전 계열(SAFETY 등)          -> blocked (provider): 출력 단계 차단
  본문 있음 (거절 문장 포함)                   -> success
  본문 없음                                   -> empty. 사고 토큰이 한도를 다 쓰면 finishReason=MAX_TOKENS
  408·409·429·5xx(500·502·503·504)·529         -> error, 재시도 대상 (base.RETRYABLE_HTTP_COMMERCIAL)

확인 필요 (실제 호출 전, 공식 문서·실응답으로 대조)
  * Gemini 3.8 Flash의 API 모델 이름과 API 버전 경로(v1beta 여부)
  * 안전 차단에 쓰이는 finishReason 전체 목록 (_BLOCK_FINISH_REASONS)
  * 사고(thinking)를 끄거나 줄이는 generationConfig.thinkingConfig 필드 — 모델 세대마다 다름.
    필요하면 models.yaml extra_generation_config에 넣는다.
"""
from .base import blocked_result, normalize_finish_reason, text_result
from .commercial import HttpCommercialAdapter, sent_params

_FINISH_REASONS = {"STOP": "stop", "MAX_TOKENS": "length"}
_BLOCK_FINISH_REASONS = ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY")
_PARAM_FIELDS = {"temperature": "temperature", "top_p": "topP", "max_output_tokens": "maxOutputTokens"}
_ROLES = {"user": "user", "assistant": "model"}


class GeminiAdapter(HttpCommercialAdapter):
    provider_label = "gemini generateContent"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _endpoint(self, base):
        return f"{base}/models/{self._api_model}:generateContent"

    def _headers(self):
        return {"x-goog-api-key": self._api_key}

    def build_request(self, messages, params):
        generation = {_PARAM_FIELDS[name]: value for name, value in sent_params(self._model, params).items()}
        generation.update(self._options.get("extra_generation_config", {}))
        body = {"contents": [{"role": _ROLES[m["role"]], "parts": [{"text": m["content"]}]}
                             for m in messages if m["role"] != "system"],
                "generationConfig": generation}
        system = [m["content"] for m in messages if m["role"] == "system"]
        if system:
            body["systemInstruction"] = {"parts": [{"text": text} for text in system]}
        return {**body, **self._extra_body}

    def _parse(self, raw):
        feedback = raw.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            return blocked_result(raw, detail=f"promptFeedback.blockReason={feedback['blockReason']}")
        candidate = (raw.get("candidates") or [{}])[0]
        finish = candidate.get("finishReason")
        if finish in _BLOCK_FINISH_REASONS:
            return blocked_result(raw, detail=f"finishReason={finish}")
        parts = (candidate.get("content") or {}).get("parts") or []
        # thought=true인 부분은 모델의 사고 요약이라 사용자에게 보이는 응답이 아니다.
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return text_result(raw, text, normalize_finish_reason(finish, _FINISH_REASONS))
