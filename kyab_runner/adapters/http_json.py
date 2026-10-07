"""JSON을 주고받는 최소 HTTP 호출부 (표준 라이브러리 urllib).

어댑터는 이 모듈의 post_json을 '전송 함수'로 주입받는다. 테스트는 같은 모양의 가짜 함수를
넣어 네트워크 없이 어댑터를 검증한다.

HTTP 상태 코드는 예외로 바꾸지 않는다. 4xx·5xx도 HttpReply로 돌려주어 어댑터가 본문을 보고
차단인지 일시 오류인지 가릴 수 있게 한다. 응답 자체가 오지 않은 경우만 예외다.
"""
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass


class TransportTimeout(Exception):
    """제한 시간 안에 응답이 오지 않음."""


class TransportFailure(Exception):
    """연결 실패 등으로 응답을 받지 못함."""


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: object          # JSON으로 읽히면 dict·list, 아니면 None
    text: str             # 본문 원문 (JSON이 아닐 때의 오류 메시지용)

    @property
    def ok(self):
        return 200 <= self.status < 300


def _reply(status, raw_bytes):
    text = raw_bytes.decode("utf-8", "replace")
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    return HttpReply(status, body, text)


def _send(request, timeout):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _reply(response.status, response.read())
    except urllib.error.HTTPError as exc:           # 4xx·5xx: 본문을 읽어 그대로 돌려준다
        return _reply(exc.code, exc.read())
    except (socket.timeout, TimeoutError) as exc:
        raise TransportTimeout(str(exc)) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise TransportTimeout(str(exc.reason)) from exc
        raise TransportFailure(str(exc.reason)) from exc
    except OSError as exc:
        raise TransportFailure(str(exc)) from exc


def post_json(url, headers, body, timeout):
    """JSON 본문을 POST하고 HttpReply를 돌려준다."""
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **headers})
    return _send(request, timeout)

