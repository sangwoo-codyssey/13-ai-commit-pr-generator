"""API 호출 기록 — 호출 1회를 JSONL 한 줄로 이어 붙인다 (AI_LOG_FILE 로 켠다).

한 줄: 시각·실행 ID·모드 + 요청 바디 그대로 + 응답 원문 + 상태·소요 시간·오류.
API Key(헤더)는 넣지 않는다. 요청은 safe-mode 가 적용된 뒤의 값이다.
기록은 부가 기능이라, 파일을 못 쓰면 경고만 하고 본 작업은 계속한다.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


class JsonlCallLog:
    def __init__(self, path: Path, mode: str, warn: Callable[[str], None]) -> None:
        self.path = path
        self.mode = mode
        # 한 실행의 호출들(1차·재생성)을 묶는 ID — 시각 + 짧은 난수
        self.run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        self._warn = warn

    def __call__(self, record: dict[str, object]) -> None:
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "mode": self.mode,
            **record,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 코드 diff 가 들어 있으니 새로 만들 때는 본인만 읽을 수 있게 (0600)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as file:
                file.write(line)
        except OSError as e:
            self._warn(f"호출 기록을 쓰지 못했습니다 ({self.path}): {e.strerror or e}")
