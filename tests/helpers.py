"""테스트 공용 도구 — 격리된 임시 Git 저장소."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gitgen.context import ChangeContext, Prompt

# 사용자 전역·시스템 git 설정(서명 강제, 색상, diff 옵션 등)이 결과를 바꾸지 못하게 끊는다.
HERMETIC_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def fake_secret(prefix: str, body_length: int) -> str:
    """마스킹 규칙에 걸리는 모양의 가짜 값. 실행할 때 조립한다.

    소스에 키 모양 문자열을 그대로 두지 않는다 — public 레포라 비밀값 스캐너 오탐을 막는다.
    본문은 FAKE + 0 반복이라 실제 키로 오해될 여지가 없다.
    """
    return prefix + "FAKE" + "0" * (body_length - 4)


def echo_prompt(ctx: ChangeContext) -> Prompt:
    """테스트용 프롬프트 빌더 — 받은 컨텍스트를 그대로 비춘다 (실제 프롬프트는 Phase 5 에서 사용자가)."""
    lines = [f"{f.status}  {f.path}" for f in ctx.files]
    if ctx.hint:
        lines.append(f"hint: {ctx.hint}")
    lines.append(f"omitted_files={ctx.omitted_files}")
    return Prompt(system="ECHO SYSTEM", user="\n".join(lines) + "\n" + ctx.diff)


def echo_retry(violations: list[str]) -> str:
    return "RETRY:\n" + "\n".join(violations)


def patch_prompts(test: unittest.TestCase) -> None:
    patcher = mock.patch.multiple("gitgen.prompts", build_commit_prompt=echo_prompt,
                                  build_pr_prompt=echo_prompt, build_retry_message=echo_retry)
    patcher.start()
    test.addCleanup(patcher.stop)


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
