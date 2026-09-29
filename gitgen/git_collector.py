"""변경 사항 수집 — git status 와 git diff 만 쓴다 (과제 §7: 수집 범위 제한).

git log · rev-parse 같은 다른 명령은 쓰지 않는다. 브랜치 이름도 status 에서 얻는다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from gitgen.context import ChangeContext, Collected, FileChange

# 사용자 전역 설정(색상·외부 diff 도구·경로 이스케이프)이 출력 형식을 바꾸지 못하게 고정한다.
STATUS_CMD = ["status", "--porcelain=v1", "-z", "--branch"]
DIFF_CMD = ["-c", "core.quotepath=false", "diff", "--no-color", "--no-ext-diff"]


class GitError(Exception):
    """git 실행 실패. 메시지에 git 이 알려준 원인을 담는다."""


class NoChanges(Exception):
    """보낼 변경이 없다. 오류가 아니라 정상 종료 사유이며, 메시지가 곧 안내문이다."""


@dataclass
class StatusEntry:
    xy: str                          # 두 글자 상태 — X: index(staged), Y: 작업 트리(unstaged)
    path: str
    orig_path: str | None = None


@dataclass
class Status:
    branch: str | None
    entries: list[StatusEntry]       # 추적 중인 파일의 변경
    untracked: list[str]

    def staged(self) -> list[FileChange]:
        return [FileChange(e.xy[0], e.path, e.orig_path) for e in self.entries if e.xy[0] != " "]

    def unstaged(self) -> list[FileChange]:
        return [FileChange(e.xy[1], e.path) for e in self.entries if e.xy[1] != " "]


def run_git(args: list[str], cwd: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    except FileNotFoundError as e:
        raise GitError("git 명령을 찾을 수 없습니다. Git 이 설치돼 있는지 확인하세요.") from e
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"종료 코드 {result.returncode}"
        raise GitError(f"git {_command_label(args)} 실패: {detail}")
    return result.stdout.decode("utf-8", "replace")


def _command_label(args: list[str]) -> str:
    """오류 메시지용 — 앞쪽의 `-c 설정=값` 은 사용자에게 의미가 없어 뺀다."""
    i = 0
    while i < len(args) and args[i] == "-c":
        i += 2
    return " ".join(args[i:])


def parse_status(raw: str) -> Status:
    """`git status --porcelain=v1 -z --branch` 출력을 해석한다.

    -z 에서는 항목이 NUL 로 끝나고 경로를 따옴표로 감싸지 않는다.
    이름 변경(R)·복사(C)는 `XY 새경로` 다음 토큰이 원래 경로다.
    """
    tokens = raw.split("\0")
    branch: str | None = None
    entries: list[StatusEntry] = []
    untracked: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if not token:
            continue
        if token.startswith("## "):
            branch = _parse_branch(token[3:])
            continue
        xy, path = token[:2], token[3:]
        if xy == "??":
            untracked.append(path)
        elif xy != "!!":
            orig = None
            if "R" in xy or "C" in xy:
                orig = tokens[i]
                i += 1
            entries.append(StatusEntry(xy, path, orig))
    return Status(branch, entries, untracked)


def _parse_branch(header: str) -> str | None:
    for prefix in ("No commits yet on ", "Initial commit on "):
        if header.startswith(prefix):
            return header[len(prefix):]
    if header.startswith("HEAD (no branch)"):
        return None
    # "main...origin/main [ahead 1]" → "main"
    return header.split("...")[0].split(" ")[0]


def parse_name_status(raw: str) -> list[FileChange]:
    """`git diff --name-status -z` 출력 — 상태 다음 경로, R/C 는 원래 경로·새 경로 순."""
    tokens = raw.split("\0")
    files: list[FileChange] = []
    i = 0
    while i < len(tokens):
        code = tokens[i]
        i += 1
        if not code:
            continue
        letter = code[0]                 # R100 → R (유사도 점수는 버린다)
        if letter in "RC":
            files.append(FileChange(letter, tokens[i + 1], tokens[i]))
            i += 2
        else:
            files.append(FileChange(letter, tokens[i]))
            i += 1
    return files


def collect_commit(cwd: str, hint: str | None = None) -> Collected:
    """커밋할 변경을 모은다. staged 를 우선하고, 없으면 스테이징 안 된 변경으로 대신한다."""
    status = parse_status(run_git(STATUS_CMD, cwd))
    staged, unstaged = status.staged(), status.unstaged()
    notes: list[str] = []

    if staged:
        source, files = "staged", staged
        diff = run_git([*DIFF_CMD, "--cached"], cwd)
        if unstaged:
            notes.append(f"staged 변경만 사용합니다 — 스테이징되지 않은 {len(unstaged)}개 파일은 제외")
    elif unstaged:
        source, files = "unstaged", unstaged
        diff = run_git(DIFF_CMD, cwd)
        notes.append("staged 변경이 없어 스테이징되지 않은 변경으로 생성합니다")
    elif status.untracked:
        raise NoChanges(
            f"추적되지 않은 새 파일만 있습니다({len(status.untracked)}개). "
            "git add 로 스테이징한 뒤 다시 실행하세요."
        )
    else:
        raise NoChanges("변경 사항이 없습니다. 커밋 메시지를 생성하지 않고 종료합니다.")

    if not diff.strip():
        raise NoChanges("diff 로 보여줄 변경 내용이 없습니다. 커밋 메시지를 생성하지 않고 종료합니다.")
    if status.untracked:
        notes.append(f"미추적 파일 {len(status.untracked)}개는 diff 에 나오지 않아 이름만 전달합니다")

    context = ChangeContext(
        mode="commit", source=source, branch=status.branch, base=None,
        files=files, untracked=status.untracked, diff=diff, hint=hint,
    )
    return Collected(context, notes)


def collect_pr(cwd: str, base: str, hint: str | None = None) -> Collected:
    """PR 로 올릴 변경 — base 와 갈라진 지점부터 HEAD 까지 커밋된 내용(`base...HEAD`)."""
    status = parse_status(run_git(STATUS_CMD, cwd))
    if status.branch == base:
        raise GitError(
            f"현재 브랜치가 비교 기준({base})과 같습니다. "
            "작업 브랜치에서 실행하거나 --base 로 기준을 바꾸세요."
        )

    revision_range = f"{base}...HEAD"
    try:
        files = parse_name_status(run_git(["diff", "--name-status", "-z", revision_range], cwd))
    except GitError as e:
        raise GitError(f"{e}\n비교 기준 브랜치 '{base}' 가 있는지 확인하세요 (--base 로 변경 가능).") from e
    diff = run_git([*DIFF_CMD, revision_range], cwd)
    if not diff.strip():
        raise NoChanges(f"{base} 대비 커밋된 변경 사항이 없습니다. PR 초안을 생성하지 않고 종료합니다.")

    notes: list[str] = []
    if status.entries or status.untracked:
        notes.append("커밋되지 않은 변경은 PR 초안에 포함되지 않습니다 (커밋된 내용만 비교)")

    context = ChangeContext(
        mode="pr", source="branch", branch=status.branch, base=base,
        files=files, untracked=[], diff=diff, hint=hint,
    )
    return Collected(context, notes)
