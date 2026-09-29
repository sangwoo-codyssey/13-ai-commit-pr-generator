"""AI API 호출 — 표준 라이브러리 urllib 로 Claude Messages REST API 를 직접 부른다.

요청 구성 → 전송 → 응답 해석 → 오류 분류가 모두 이 파일에 있다.
자동 재시도는 하지 않는다 — 1회 실행당 호출 상한(2회)을 넘지 않기 위해서다.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.anthropic.com"
MESSAGES_PATH = "/v1/messages"               # Anthropic 규격 경로 — 호환 게이트웨이도 같다
API_URL = DEFAULT_BASE_URL + MESSAGES_PATH
API_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 60.0
MAX_CALLS = 2


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]       # 이름은 소문자로
    body: bytes


Transport = Callable[[str, Mapping[str, str], bytes, float], HttpResponse]
Recorder = Callable[[dict[str, object]], None]      # 호출 1회가 끝날 때마다 기록 한 건을 받는다


def urllib_transport(url: str, headers: Mapping[str, str], body: bytes,
                     timeout: float) -> HttpResponse:
    """POST 한 번. 4xx/5xx 도 응답으로 돌려준다 — 원인이 본문에 있기 때문이다.

    연결 실패·시간 초과는 그대로 올려 보내고 분류는 호출한 쪽이 한다.
    """
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, _lower_keys(response.headers), response.read())
    except urllib.error.HTTPError as e:          # URLError 의 하위 클래스라 먼저 잡는다
        with e:
            return HttpResponse(e.code, _lower_keys(e.headers), e.read())


def _lower_keys(headers) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


class AIError(Exception):
    """API 호출 실패. `kind` 로 원인을 분류하고, 메시지는 사람이 읽을 원인 + 대처법이다."""

    def __init__(self, kind: str, message: str, *, status: int | None = None,
                 hint: str | None = None, request_id: str | None = None) -> None:
        self.kind = kind
        self.status = status
        self.hint = hint
        self.request_id = request_id
        lines = [message]
        if hint:
            lines.append(f"→ {hint}")
        if request_id:
            lines.append(f"request-id: {request_id}")
        super().__init__("\n".join(lines))


@dataclass(frozen=True)
class Completion:
    text: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int

    @property
    def truncated(self) -> bool:
        """max_tokens 에 걸려 중간에 끊겼다."""
        return self.stop_reason == "max_tokens"


class CallBudget:
    """1회 실행 동안의 API 호출 횟수 상한. 보내기 직전에 센다 — 실패한 호출도 한 번이다."""

    def __init__(self, limit: int = MAX_CALLS) -> None:
        self.limit = limit
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def take(self) -> None:
        if self.used >= self.limit:
            raise AIError("budget", f"1회 실행당 API 호출 한도({self.limit}회)를 모두 썼습니다.")
        self.used += 1


# 상태 코드 → (분류, 대처법). 429 는 retry-after 를 붙여 따로 만든다.
STATUS_GUIDE: dict[int, tuple[str, str]] = {
    400: ("bad_request", "요청 파라미터를 확인하세요. temperature 를 받지 않는 모델이면 "
                         "--temperature none 으로 빼고 다시 실행하세요."),
    401: ("auth", "API Key 가 올바른지 확인하세요 (오타·만료·폐기)."),
    402: ("billing", "결제 수단·크레딧을 확인하세요."),
    403: ("permission", "이 API Key 로는 허용되지 않은 요청입니다. 권한·조직 설정을 확인하세요."),
    404: ("not_found", "모델 ID 를 확인하세요 (--model)."),
    413: ("too_large", "요청이 너무 큽니다. --safe-mode 로 전송량을 줄이세요."),
    500: ("server", "API 서버 오류입니다. 잠시 후 다시 시도하세요."),
    529: ("overloaded", "API 가 일시적으로 과부하 상태입니다. 잠시 후 다시 시도하세요."),
}


class ClaudeClient:
    def __init__(self, api_key: str, *, model: str, temperature: float | None,
                 max_tokens: int, timeout: float = DEFAULT_TIMEOUT,
                 budget: CallBudget | None = None, url: str = API_URL,
                 transport: Transport = urllib_transport,
                 recorder: Recorder | None = None) -> None:
        self._api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.budget = budget or CallBudget()
        self.url = url
        self._transport = transport
        self.recorder = recorder

    def build_request(self, system: str,
                      messages: list[dict[str, str]]) -> tuple[dict[str, str], bytes]:
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
        }
        if self.temperature is not None:        # None 이면 아예 보내지 않는다
            payload["temperature"] = self.temperature
        headers = {
            "content-type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
        }
        return headers, json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def complete(self, system: str, messages: list[dict[str, str]]) -> Completion:
        headers, body = self.build_request(system, messages)
        self.budget.take()
        started = time.monotonic()
        response: HttpResponse | None = None
        failure: AIError | None = None
        try:
            response = self._send(headers, body)
            if response.status != 200:
                raise error_from_response(response)
            return parse_completion(response.body)
        except AIError as e:
            failure = e
            raise
        finally:
            if self.recorder is not None:
                self.recorder(self._record(body, response, failure, started))

    def _record(self, body: bytes, response: HttpResponse | None, failure: AIError | None,
                started: float) -> dict[str, object]:
        """호출 1회의 기록. 요청은 실제로 보낸 바이트를 다시 읽은 것이고, 헤더(API Key)는 넣지 않는다."""
        return {
            "call": self.budget.used,
            "call_limit": self.budget.limit,
            "endpoint": self.url,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "request": json.loads(body),
            "status": response.status if response else None,
            "response": _decode_body(response.body) if response else None,
            "error": {"kind": failure.kind, "message": str(failure)} if failure else None,
        }

    def _send(self, headers: Mapping[str, str], body: bytes) -> HttpResponse:
        """전송만 한다. 연결 실패·시간 초과를 원인별 AIError 로 바꾼다."""
        try:
            return self._transport(self.url, headers, body, self.timeout)
        except TimeoutError:
            raise self._timeout_error() from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):     # 연결 단계의 시간 초과는 URLError 로 감싸 온다
                raise self._timeout_error() from None
            raise AIError("network", f"네트워크 오류로 API 에 연결하지 못했습니다: {e.reason}",
                          hint="인터넷 연결·프록시·방화벽을 확인하세요.") from None
        except (OSError, http.client.HTTPException) as e:
            raise AIError("network", f"API 와 통신하는 중 연결이 끊겼습니다: {e!r}",
                          hint="잠시 후 다시 시도하세요.") from None

    def _timeout_error(self) -> AIError:
        return AIError("timeout", f"API 응답이 {self.timeout:g}초 안에 오지 않았습니다.",
                       hint="잠시 후 다시 시도하거나 --max-tokens 를 줄여 보세요.")


def error_from_response(response: HttpResponse) -> AIError:
    """오류 응답 본문 `{"type":"error","error":{"type","message"},"request_id"}` 을 읽는다.

    프록시가 HTML 을 돌려주는 등 본문이 JSON 이 아니어도 상태 코드로 분류는 한다.
    """
    status = response.status
    request_id = response.headers.get("request-id")
    api_type = api_message = None
    try:
        data = json.loads(response.body)
        error = data.get("error") or {}
        api_type, api_message = error.get("type"), error.get("message")
        request_id = data.get("request_id") or request_id
    except (ValueError, AttributeError):
        pass

    if status == 429:
        wait = response.headers.get("retry-after")
        kind = "rate_limit"
        hint = (f"요청 한도를 넘었습니다. {wait}초 뒤에 다시 시도하세요." if wait
                else "요청 한도를 넘었습니다. 잠시 뒤에 다시 시도하세요.")
    elif status in STATUS_GUIDE:
        kind, hint = STATUS_GUIDE[status]
    elif status >= 500:
        kind, hint = "server", "API 서버 오류입니다. 잠시 후 다시 시도하세요."
    else:
        kind, hint = "http", None

    label = f"HTTP {status}" + (f" {api_type}" if api_type else "")
    reason = api_message or "(응답 본문에 오류 설명이 없습니다)"
    return AIError(kind, f"API 요청 실패 ({label}): {reason}", status=status,
                   hint=hint, request_id=request_id)


def _decode_body(body: bytes) -> object:
    """기록용 — JSON 이면 객체로, 아니면(프록시 HTML 등) 앞부분만 문자열로."""
    try:
        return json.loads(body)
    except ValueError:
        return body[:2000].decode("utf-8", "replace")


def parse_completion(body: bytes) -> Completion:
    try:
        data = json.loads(body)
    except ValueError:
        raise AIError("bad_response", "API 응답이 JSON 형식이 아닙니다.") from None
    if not isinstance(data, dict) or data.get("type") != "message":
        raise AIError("bad_response", "API 응답에 message 객체가 없습니다.")

    stop_reason = data.get("stop_reason")
    if stop_reason == "refusal":
        raise AIError("refusal", "모델이 요청 처리를 거절했습니다 (stop_reason=refusal).",
                      hint="diff 내용이나 --hint 를 확인하세요.")
    # content 는 블록 배열이다. text 블록만 이어 붙인다 (thinking 등 다른 블록은 무시).
    text = "".join(block.get("text", "") for block in data.get("content") or []
                   if isinstance(block, dict) and block.get("type") == "text")
    if not text.strip():
        hint = "--max-tokens 를 늘려 보세요." if stop_reason == "max_tokens" else None
        raise AIError("bad_response", "API 응답에 텍스트가 없습니다.", hint=hint)

    usage = data.get("usage") or {}
    return Completion(text=text, stop_reason=stop_reason,
                      input_tokens=int(usage.get("input_tokens", 0)),
                      output_tokens=int(usage.get("output_tokens", 0)))
