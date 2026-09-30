"""127.0.0.1 에 띄우는 가짜 Messages API — 실제 API 대신 정해 둔 응답을 돌려준다.

클라이언트의 진짜 urllib 경로(HTTPError·타임아웃·본문 읽기)를 그대로 태우면서도
외부로는 한 번도 나가지 않는다.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Reply:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0


@dataclass
class Received:
    path: str
    headers: dict[str, str]          # 이름은 소문자
    body: bytes

    def json(self) -> dict:
        return json.loads(self.body)


def message_body(*texts: str, stop_reason: str = "end_turn", input_tokens: int = 100,
                 output_tokens: int = 20, extra_blocks: list[dict] | None = None) -> bytes:
    """성공 응답 본문 — 실제 응답과 같은 모양."""
    content = list(extra_blocks or []) + [{"type": "text", "text": t} for t in texts]
    return json.dumps({
        "id": "msg_fake", "type": "message", "role": "assistant", "model": "fake-model",
        "content": content, "stop_reason": stop_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }, ensure_ascii=False).encode("utf-8")


def error_body(error_type: str, message: str, request_id: str = "req_fake") -> bytes:
    return json.dumps({"type": "error", "error": {"type": error_type, "message": message},
                       "request_id": request_id}).encode("utf-8")


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass                         # 타임아웃 테스트에서 클라이언트가 먼저 끊는 것은 정상이다


class FakeApiServer:
    def __init__(self) -> None:
        self.replies: list[Reply] = []
        self.received: list[Received] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                server.received.append(Received(
                    self.path, {k.lower(): v for k, v in self.headers.items()},
                    self.rfile.read(length)))
                reply = server.replies.pop(0) if server.replies else Reply(500, b"no reply queued")
                time.sleep(reply.delay)
                self.send_response(reply.status)
                self.send_header("Content-Type", "application/json")
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                self.wfile.write(reply.body)

            def log_message(self, *args):
                pass

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        # 기본 poll_interval(0.5초)이면 shutdown() 이 테스트마다 0.5초씩 기다린다.
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.01}, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def url(self) -> str:
        return self.base_url + "/v1/messages"

    def reply(self, status: int = 200, body: bytes = b"", headers: dict[str, str] | None = None,
              delay: float = 0.0) -> None:
        self.replies.append(Reply(status, body, headers or {}, delay))

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def closed_port_url() -> str:
    """아무도 듣지 않는 포트 — 연결 거부를 만든다."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1/messages"
