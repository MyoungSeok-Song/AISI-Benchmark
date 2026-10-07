"""로컬 vLLM 어댑터. 이 서버에서 띄운 vLLM의 OpenAI 호환 API(localhost)만 호출한다.

문항이 서버 밖으로 나가지 않는다. 접속 주소가 localhost가 아니면 실행을 거부한다.
서버 기동·종료는 tools/vllm_server.py가 맡고, 이 어댑터는 떠 있는 서버에 요청만 보낸다.
HTTP는 표준 라이브러리(urllib)로 보내므로 러너 venv에 추가 패키지가 필요 없다.

models.yaml options
  base_url            예: http://127.0.0.1:8000/v1
  served_model_name   서버의 --served-model-name
  request_timeout_s   호출 1회 제한 시간(초)
  extra_body          요청 본문에 그대로 더할 값. 예: Qwen 계열 thinking 끄기
                      {chat_template_kwargs: {enable_thinking: false}}

기록하는 모델 판본(04 model_version)은 models.yaml의 HF 스냅샷 revision이다.
시작할 때 서버가 실제로 그 스냅샷을 올렸는지(/v1/models의 root 경로) 확인하고,
다르면 실행하지 않는다.
"""
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .. import fileio, paths
from ..vocab import RESPONSE_ERROR
from .base import (RETRYABLE_HTTP_LOCAL, Adapter, AdapterResult, chat_completion_result, connection_error_result,
                   invalid_response_result, timeout_result)

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
SERVER_INFO_FILE = paths.VLLM_SERVER_INFO                           # tools/vllm_server.py가 기록


class LocalVllmAdapter(Adapter):
    def __init__(self, model):
        options = model.options
        self._model = model
        self._base_url = options["base_url"].rstrip("/")
        self._served_name = options["served_model_name"]
        self._timeout = options.get("request_timeout_s", 120)
        self._extra_body = options.get("extra_body", {})
        if urlparse(self._base_url).hostname not in _LOCAL_HOSTS:
            raise ValueError(f"local_vllm은 localhost만 호출합니다: {self._base_url}")
        self._served = self._find_served_model()

    # ── 시작 전 확인 ────────────────────────────────────────────────────
    def _find_served_model(self):
        """서버가 떠 있고, 등록한 이름·스냅샷으로 모델을 올렸는지 확인한다."""
        try:
            listing = self._get("/models")
        except (urllib.error.URLError, OSError) as exc:
            raise SystemExit(f"vLLM 서버에 연결할 수 없습니다 ({self._base_url}): {exc}\n"
                             f"먼저 tools/vllm_server.py start 로 서버를 띄우세요.")
        served = next((m for m in listing["data"] if m["id"] == self._served_name), None)
        if served is None:
            raise SystemExit(f"서버에 '{self._served_name}' 모델이 없습니다: {[m['id'] for m in listing['data']]}")
        if self._model.model_version not in served.get("root", ""):
            raise SystemExit(f"서버가 올린 모델 경로({served.get('root')})가 등록된 revision "
                             f"{self._model.model_version}과 다릅니다")
        return served

    def describe(self):
        """서버 버전·모델 경로·기동 정보. batch_manifest.json과 runner_events.jsonl에 남는다."""
        info = {"adapter": "local_vllm", "base_url": self._base_url,
                "served_model_name": self._served_name, "model_root": self._served.get("root"),
                "max_model_len": self._served.get("max_model_len"), "extra_body": self._extra_body}
        try:                                          # /version은 /v1 밖에 있다
            with urllib.request.urlopen(self._base_url.rsplit("/v1", 1)[0] + "/version", timeout=10) as r:
                info["vllm_version"] = json.load(r).get("version")
        except (urllib.error.URLError, OSError, ValueError):
            info["vllm_version"] = None
        if SERVER_INFO_FILE.exists():                 # 기동 명령·dtype·GPU
            info["server"] = fileio.read_json(SERVER_INFO_FILE)
        return info

    # ── 호출 ────────────────────────────────────────────────────────────
    def complete(self, messages, params, call_info):
        body = {"model": self._served_name, "messages": messages,
                "temperature": params["temperature"], "top_p": params["top_p"],
                "max_tokens": params["max_output_tokens"], **self._extra_body}
        # http_json(상용 어댑터의 전송부)을 쓰지 않는다: 오류 메시지·원본(05 error_message·raw_response_json)의 표기가
        # 지금 기록과 달라진다(str(URLError) 대 str(exc.reason), HTTP 오류 본문의 보존 방식).
        try:
            raw = self._post("/chat/completions", body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            return AdapterResult(RESPONSE_ERROR, error_code=f"http_{exc.code}", error_message=detail,
                                 retryable=exc.code in RETRYABLE_HTTP_LOCAL)
        except (socket.timeout, TimeoutError):
            return timeout_result(self._timeout)
        except (urllib.error.URLError, OSError) as exc:
            if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
                return timeout_result(self._timeout)
            return connection_error_result(exc)
        except ValueError as exc:                     # 본문이 JSON이 아님
            return invalid_response_result(exc)
        return _to_result(raw)

    # ── HTTP ────────────────────────────────────────────────────────────
    def _get(self, path):
        with urllib.request.urlopen(self._base_url + path, timeout=10) as response:
            return json.load(response)

    def _post(self, path, body):
        request = urllib.request.Request(self._base_url + path, data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return json.load(response)


def _to_result(raw):
    """chat completion 원본 응답 -> AdapterResult (base.chat_completion_result). 원본은 손대지 않고 그대로 보존한다."""
    return chat_completion_result(raw)
