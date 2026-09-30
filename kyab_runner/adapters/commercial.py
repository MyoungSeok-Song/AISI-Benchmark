"""상용 API 어댑터의 공통부: API 키, 호출 파라미터 정책, HTTP 오류 분류.

!! 실제 호출은 API 키 + D06(유료 집행 승인) + D08(외부 전송 조건)이 모두 확인된 뒤에만 한다.
   그 전에는 config/models.yaml에서 enabled: false로 두며, 실행기가 호출을 거부한다.
   이 어댑터들은 가짜 응답으로만 검증됐다(tests/test_commercial_adapters.py).

API 키
  환경변수에서만 읽는다. 변수 이름은 models.yaml options.api_key_env.
  키 값은 파일·로그·manifest·예외 메시지 어디에도 쓰지 않는다. describe()에는 변수 이름만 남긴다.

호출 파라미터 정책
  러너는 코드북 고정값(temperature 0.0, top_p 1.0, max_output_tokens 1024)을 요청한다.
  공급자·모델에 따라 일부를 받지 않거나 함께 지정할 수 없다. 그런 파라미터는
  models.yaml options.omit_params에 적어 보내지 않는다.
  04_runs에는 코드북 고정값을 그대로 적고, 실제로 보낸 값은 describe()를 통해
  batch_manifest.json과 runner_events.jsonl에 남겨 차이를 추적한다.

대화 저장
  매 호출 전체 메시지를 다시 보낸다. 공급자 쪽 대화 저장·이어 쓰기 기능은 쓰지 않는다.
"""
import os
import sys

from .base import Adapter, AdapterResult

# 다시 보내 볼 만한 HTTP 상태: 요청 과다, 서버 쪽 일시 장애
RETRYABLE_HTTP = (408, 409, 429, 500, 502, 503, 504, 529)


def require_api_key(model):
    """환경변수에서 API 키를 읽는다. 없으면 키 값을 묻지 않고 명확한 안내와 함께 멈춘다."""
    env_name = model.options["api_key_env"]
    key = os.environ.get(env_name, "")
    if not key:
        sys.exit(f"모델 '{model.model_id}' 실행에는 환경변수 {env_name}가 필요합니다. "
                 f"키는 환경변수로만 전달하고 파일에 적지 마세요.")
    return key


def sent_params(model, params):
    """러너가 요청한 파라미터 중 이 모델에 실제로 보낼 것만 남긴다."""
    omit = set(model.options.get("omit_params", []))
    return {name: value for name, value in params.items() if name not in omit}


def http_error_result(reply, latency_ms=0):
    """성공이 아닌 HTTP 응답 -> error 결과. 본문이 JSON이면 원본으로 보존한다."""
    error = reply.body.get("error") if isinstance(reply.body, dict) else None
    detail = error.get("message") if isinstance(error, dict) else None
    return AdapterResult("error", raw_response=reply.body if isinstance(reply.body, dict) else {},
                         error_code=f"http_{reply.status}", error_message=(detail or reply.text)[:500],
                         retryable=reply.status in RETRYABLE_HTTP, latency_ms=latency_ms)


def timeout_result(timeout_s):
    return AdapterResult("timeout", error_code="timeout",
                         error_message=f"{timeout_s}초 안에 응답 없음", retryable=True)


def connection_error_result(exc):
    return AdapterResult("error", error_code="connection_error", error_message=str(exc), retryable=True)


class CommercialAdapter(Adapter):
    """상용 어댑터의 공통 뼈대. 하위 클래스는 요청 구성과 응답 해석만 구현한다."""

    provider_label = ""           # describe()에 남길 API 이름

    def __init__(self, model):
        self._model = model
        self._options = model.options
        self._api_model = self._options["api_model"]              # 공급자에 보낼 모델 이름
        self._timeout = self._options.get("request_timeout_s", 120)
        self._extra_body = self._options.get("extra_body", {})    # 요청 본문에 그대로 더할 값
        self._api_key = require_api_key(model)

    def describe(self):
        """실제로 보내는 설정. 키 값은 넣지 않는다."""
        return {"adapter": self._model.adapter, "api": self.provider_label,
                "api_model": self._api_model, "api_key_env": self._options["api_key_env"],
                "omit_params": self._options.get("omit_params", []),
                "extra_body": self._extra_body, "conversation_storage": "off"}
