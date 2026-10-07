"""상용 API 어댑터의 공통부: API 키, 호출 파라미터 정책, HTTP 오류 분류, HTTP 어댑터 공통 틀.

!! 실제 호출은 API 키 + D06(유료 집행 승인) + D08(외부 전송 조건)이 모두 확인된 뒤에만 한다.
   그 전에는 config/models.yaml에서 enabled: false로 두며, 실행기가 호출을 거부한다.
   이 어댑터들은 가짜 응답으로만 검증됐다(tests/test_commercial_adapters.py).

API 키
  환경변수에서만 읽는다. 변수 이름은 models.yaml options.api_key_env.
  키 값은 파일·로그·manifest·예외 메시지 어디에도 쓰지 않는다. describe()에는 변수 이름만 남긴다.

호출 파라미터 정책
  러너는 runner.yaml run_params(temperature 0.0, top_p 1.0, max_output_tokens — 코드북 04 허용값)를 요청한다.
  공급자·모델에 따라 일부를 받지 않거나 함께 지정할 수 없다. 그런 파라미터는
  models.yaml options.omit_params에 적어 보내지 않는다.
  04_runs에는 코드북 고정값을 그대로 적고, 실제로 보낸 값은 describe()를 통해
  batch_manifest.json과 runner_events.jsonl에 남겨 차이를 추적한다.

대화 저장
  매 호출 전체 메시지를 다시 보낸다. 공급자 쪽 대화 저장·이어 쓰기 기능은 쓰지 않는다.
"""
import os

from . import http_json
from .base import (RETRYABLE_HTTP_COMMERCIAL, Adapter, AdapterResult, AdapterSetupError,   # noqa: F401 (재수출)
                   connection_error_result, invalid_response_result, timeout_result)
from ..vocab import RESPONSE_ERROR

RETRYABLE_HTTP = RETRYABLE_HTTP_COMMERCIAL          # 이 모듈의 옛 이름


def require_api_key(model):
    """환경변수에서 API 키를 읽는다. 없으면 키 값을 묻지 않고 명확한 안내와 함께 멈춘다."""
    env_name = model.options["api_key_env"]
    key = os.environ.get(env_name, "")
    if not key:
        raise AdapterSetupError(f"모델 '{model.model_id}' 실행에는 환경변수 {env_name}가 필요합니다. "
                                f"키는 환경변수로만 전달하고 파일에 적지 마세요.")
    return key


def sent_params(model, params):
    """러너가 요청한 파라미터 중 이 모델에 실제로 보낼 것만 남긴다."""
    omit = set(model.options.get("omit_params", []))
    return {name: value for name, value in params.items() if name not in omit}


def http_error_result(reply):
    """성공이 아닌 HTTP 응답 -> error 결과. 본문이 JSON이면 원본으로 보존한다."""
    error = reply.body.get("error") if isinstance(reply.body, dict) else None
    detail = error.get("message") if isinstance(error, dict) else None
    return AdapterResult(RESPONSE_ERROR, raw_response=reply.body if isinstance(reply.body, dict) else {},
                         error_code=f"http_{reply.status}", error_message=(detail or reply.text)[:500],
                         retryable=reply.status in RETRYABLE_HTTP_COMMERCIAL)


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


class HttpCommercialAdapter(CommercialAdapter):
    """표준 라이브러리 HTTP(http_json)로 JSON을 주고받는 상용 어댑터의 공통 틀(OpenAI·Gemini).

    하위 클래스가 정하는 것: default_base_url, _endpoint(base), _headers(), build_request(messages, params),
    _parse(raw), 필요하면 _on_http_error(reply). Anthropic은 SDK를 쓰므로 CommercialAdapter를 직접 상속한다.
    transport는 테스트가 가짜 전송 함수를 넣는 자리다.
    """

    default_base_url = ""

    def __init__(self, model, transport=http_json.post_json):
        super().__init__(model)
        self._transport = transport
        self._url = self._endpoint(self._options.get("base_url", self.default_base_url).rstrip("/"))

    def _endpoint(self, base):
        raise NotImplementedError

    def _headers(self):
        raise NotImplementedError

    def build_request(self, messages, params):
        """요청 본문. 테스트가 실제 전송 내용을 확인할 수 있게 따로 둔다."""
        raise NotImplementedError

    def _parse(self, raw):
        raise NotImplementedError

    def _on_http_error(self, reply):
        """2xx가 아닌 응답. 기본은 error(재시도 여부는 상태 코드 표). 차단으로 볼 400은 하위 클래스가 가린다."""
        return http_error_result(reply)

    def complete(self, messages, params, call_info):
        try:
            reply = self._transport(self._url, self._headers(), self.build_request(messages, params), self._timeout)
        except http_json.TransportTimeout:
            return timeout_result(self._timeout)
        except http_json.TransportFailure as exc:
            return connection_error_result(exc)
        if not reply.ok:
            return self._on_http_error(reply)
        if not isinstance(reply.body, dict):             # 2xx인데 JSON 객체가 아님(HTML 오류 페이지 등)
            return invalid_response_result(f"JSON 객체가 아닌 응답 본문: {reply.text[:200]!r}")
        return self._parse(reply.body)
