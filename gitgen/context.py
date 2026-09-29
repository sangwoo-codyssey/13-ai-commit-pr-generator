"""수집 단계가 만들고 프롬프트 단계가 읽는 데이터."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FileChange:
    status: str                      # M, A, D, R, C, U ... (한 글자)
    path: str
    orig_path: str | None = None     # 이름 변경(R)·복사(C) 일 때 원래 경로


@dataclass
class ChangeContext:
    mode: str                        # "commit" | "pr"
    source: str                      # diff 출처: "staged" | "unstaged" | "branch"
    branch: str | None               # 현재 브랜치. detached HEAD 면 None
    base: str | None                 # pr 의 비교 기준 브랜치
    files: list[FileChange]          # 변경 파일 전체 — diff 가 잘려도 이름은 전부 남긴다
    untracked: list[str]             # 미추적 파일 — git diff 에 나오지 않아 이름만
    diff: str                        # AI 에 보낼 diff 텍스트
    omitted_files: int = 0           # 전송량 제한으로 본문이 빠진 파일 수
    omitted_lines: int = 0           # 전송량 제한으로 빠진 diff 줄 수
    hint: str | None = None          # --hint 로 받은 변경 이유

    @property
    def diff_line_count(self) -> int:
        return len(self.diff.splitlines())


@dataclass
class Collected:
    """수집 결과와, 사용자에게 알릴 판단 근거(어떤 diff 를 왜 골랐는지)."""

    context: ChangeContext
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
