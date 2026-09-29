"""테스트 공용 도구 — 격리된 임시 Git 저장소."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 사용자 전역·시스템 git 설정(서명 강제, 색상, diff 옵션 등)이 결과를 바꾸지 못하게 끊는다.
HERMETIC_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


class TempRepo:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        self.git("-c", "init.defaultBranch=main", "init", "-q")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, check=True, capture_output=True, text=True,
        ).stdout

    def write(self, relative: str, content: str) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit_all(self, message: str = "commit") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def cleanup(self) -> None:
        self._tmp.cleanup()


class GitRepoTestCase(unittest.TestCase):
    """테스트마다 새 저장소를 만들고, git 설정을 격리한다."""

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, HERMETIC_GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)

    @property
    def cwd(self) -> str:
        return str(self.repo.path)
