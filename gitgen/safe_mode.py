"""--safe-mode: AI 로 보내기 전에 diff 에서 민감정보를 가리고 전송량을 줄인다.

순서: 민감 파일 제외 → 마스킹 → 전송량 제한.
마스킹을 제한보다 먼저 하는 이유 — 제한이 키 블록 한가운데를 자르면 BEGIN/END 로
묶던 패턴이 깨져 본문이 새어 나갈 수 있다. 파일(chunk) 단위로 전부 가린 뒤 자른다.
"""

from __future__ import annotations

import fnmatch
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath

from gitgen.context import ChangeContext

MAX_FILES = 10          # 과제 §7 (B) 예시 수치
MAX_LINES = 200

# 내용을 통째로 빼는 파일 — 파일명(대소문자 무시)으로 판단한다.
SENSITIVE_FILE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "id_rsa*", "id_ecdsa*", "id_ed25519*",
)
EXCLUDED_MARK = "[safe-mode: 민감 파일 — 내용 제외]"


def mask_label(kind: str) -> str:
    return f"[MASKED:{kind}]"


@dataclass(frozen=True)
class MaskRule:
    """`secret` 이름 그룹이 있으면 그 부분만, 없으면 일치 전체를 가린다."""

    kind: str
    pattern: re.Pattern[str]

    def apply(self, text: str, counts: Counter[str]) -> str:
        def substitute(match: re.Match[str]) -> str:
            counts[self.kind] += 1
            if "secret" not in match.re.groupindex:
                return mask_label(self.kind)
            start, end = match.start("secret") - match.start(), match.end("secret") - match.start()
            whole = match.group(0)
            return whole[:start] + mask_label(self.kind) + whole[end:]

        return self.pattern.sub(substitute, text)


_KNOWN_KEY_PREFIXES = "|".join([
    r"sk-ant-[A-Za-z0-9_\-]{20,}",           # Anthropic
    r"sk-[A-Za-z0-9_\-]{20,}",               # OpenAI 등
    r"(?:AKIA|ASIA)[0-9A-Z]{16}",            # AWS 액세스 키 ID
    r"gh[pousr]_[A-Za-z0-9]{36,}",           # GitHub 토큰
    r"github_pat_[A-Za-z0-9_]{22,}",
    r"xox[abprs]-[A-Za-z0-9\-]{10,}",        # Slack
    r"AIza[0-9A-Za-z_\-]{35}",               # Google API
])

# 구체적인 규칙이 먼저다 — 앞 규칙이 가린 자리표시자는 뒤 규칙에 다시 걸리지 않는다.
MASK_RULES = (
    # END 가 없으면(diff 가 블록 일부만 보여줄 때) chunk 끝까지 가린다.
    MaskRule("PRIVATE_KEY", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
        re.S)),
    MaskRule("API_KEY", re.compile(rf"(?<![A-Za-z0-9])(?:{_KNOWN_KEY_PREFIXES})")),
    MaskRule("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    # 공백은 [ \t] 만 — 줄바꿈을 넘어 다음 줄과 엮이지 않게 한다.
    MaskRule("TOKEN", re.compile(r"(?i)\bbearer[ \t]+(?P<secret>[A-Za-z0-9._~+/\-]{16,}=*)")),
    # 이름은 두고 값만. 값은 8자 이상 + 영문·숫자 둘 다 — `get_token()`·"AI_API_KEY" 같은 식별자는 두기 위해.
    MaskRule("SECRET", re.compile(
        r"(?i)\b[A-Za-z0-9_]*(?:api[_\-]?key|secret|token|passw(?:or)?d|pwd|access[_\-]?key)"
        r"[A-Za-z0-9_]*[ \t]*[:=][ \t]*['\"]?"
        r"(?P<secret>(?=[^\s'\"]*\d)(?=[^\s'\"]*[A-Za-z])[^\s'\"]{8,})")),
    MaskRule("EMAIL", re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}\b")),
    MaskRule("PHONE", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    # 하이픈 필수 + 생년월일 형태 — 13자리 타임스탬프 같은 숫자를 오탐하지 않게.
    MaskRule("RRN", re.compile(
        r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-[1-4]\d{6}(?!\d)")),
)


def mask_text(text: str, counts: Counter[str]) -> str:
    for rule in MASK_RULES:
        text = rule.apply(text, counts)
    return text


@dataclass
class FileChunk:
    """diff 에서 `diff --git` 한 줄부터 다음 `diff --git` 직전까지 — 파일 하나."""

    path: str
    lines: list[str]


def split_diff(diff: str) -> tuple[list[str], list[FileChunk]]:
    preamble: list[str] = []
    chunks: list[FileChunk] = []
    for line in diff.splitlines():
        # 내용 줄은 ' ', '+', '-', '\' 로 시작하므로 이 머리줄과 헷갈리지 않는다.
        if line.startswith("diff --git "):
            chunks.append(FileChunk("", [line]))
        elif chunks:
            chunks[-1].lines.append(line)
        else:
            preamble.append(line)
    for chunk in chunks:
        chunk.path = chunk_path(chunk.lines)
    return preamble, chunks


def chunk_path(lines: list[str]) -> str:
    """파일 경로를 머리 부분(첫 `@@` 앞)에서 찾는다: rename to → +++ → --- → 머리줄."""
    header: list[str] = []
    for line in lines[1:]:
        if line.startswith("@@"):
            break
        header.append(line)
    for prefix in ("rename to ", "copy to "):
        for line in header:
            if line.startswith(prefix):
                return line[len(prefix):]
    for marker in ("+++ ", "--- "):
        for line in header:
            if line.startswith(marker) and line != marker + "/dev/null":
                return _strip_prefix(line[len(marker):])
    # 바이너리 등 ---/+++ 가 없는 경우: "a/P b/P" 는 이름이 같아 가운데에서 나뉜다.
    names = lines[0][len("diff --git "):]
    n = (len(names) - 5) // 2
    candidate = names[2:2 + n]
    return candidate if names == f"a/{candidate} b/{candidate}" else names


def _strip_prefix(path: str) -> str:
    path = path.rstrip("\t")          # git 은 공백이 든 경로 뒤에 탭을 붙인다
    if len(path) >= 2 and path[0] == path[-1] == '"':
        path = path[1:-1]
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def is_sensitive(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in SENSITIVE_FILE_PATTERNS)


@dataclass
class SafeModeReport:
    masked: Counter[str] = field(default_factory=Counter)
    excluded: list[str] = field(default_factory=list)
    total_files: int = 0
    sent_files: int = 0              # 내용이 한 줄이라도 전송된 파일
    total_lines: int = 0
    sent_lines: int = 0              # 생략 표시 줄은 세지 않는다
    omitted_files: int = 0           # 전송 한도로 내용이 통째로 빠진 파일
    omitted_lines: int = 0           # 전송 한도로 빠진 줄 (마스킹 후 기준)

    def summary(self) -> str:
        if self.masked:
            detail = ", ".join(f"{kind} {count}" for kind, count in self.masked.most_common())
            # 마스킹은 제한보다 먼저라 건수는 한도로 빠진 부분까지 센 값이다. 빠진 부분에 실제로
            # 있었는지는 따지지 않고 세는 기준만 밝힌다 (Phase 6: 전부 전송된 파일에 있어도 "포함"이라 했다).
            if self.omitted_files or self.omitted_lines:
                detail += ", 전송 한도 적용 전 기준"
            parts = [f"마스킹 {sum(self.masked.values())}건({detail})"]
        else:
            parts = ["마스킹 0건"]
        if self.excluded:
            parts.append(f"민감 파일 {len(self.excluded)}개 내용 제외")
        parts.append(f"전송 {self.sent_files}/{self.total_files}파일 · "
                     f"{self.sent_lines}/{self.total_lines}줄")
        return "safe-mode: " + " · ".join(parts)


def apply_safe_mode(ctx: ChangeContext, max_files: int = MAX_FILES,
                    max_lines: int = MAX_LINES) -> tuple[ChangeContext, SafeModeReport]:
    """민감 파일 제외 → 마스킹 → 전송량 제한을 적용한 새 컨텍스트를 돌려준다.

    파일 목록(ctx.files)은 그대로 둔다 — 내용이 잘려도 무엇이 바뀌었는지는 AI 가 알아야 한다.
    """
    report = SafeModeReport(total_lines=ctx.diff_line_count)
    preamble, chunks = split_diff(ctx.diff)
    report.total_files = len(chunks)

    for chunk in chunks:
        if is_sensitive(chunk.path):
            report.excluded.append(chunk.path)
            chunk.lines = [chunk.lines[0], EXCLUDED_MARK]
        else:
            chunk.lines = mask_text("\n".join(chunk.lines), report.masked).split("\n")

    sent: list[str] = [mask_text(line, report.masked) for line in preamble]
    for index, chunk in enumerate(chunks):
        room = max_lines - report.sent_lines
        if index >= max_files or room <= 0:
            report.omitted_files += 1
            report.omitted_lines += len(chunk.lines)
            continue
        report.sent_files += 1
        taken = chunk.lines[:room]
        sent.extend(taken)
        report.sent_lines += len(taken)
        if len(chunk.lines) > room:
            rest = len(chunk.lines) - room
            report.omitted_lines += rest
            sent.append(f"[safe-mode: 이 파일의 나머지 {rest}줄 생략]")
    if report.omitted_files:
        sent.append(f"[safe-mode: 전송 한도({max_files}파일·{max_lines}줄)를 넘어 "
                    f"파일 {report.omitted_files}개의 내용을 생략]")

    hint = mask_text(ctx.hint, report.masked) if ctx.hint else ctx.hint
    limited = replace(ctx, diff="\n".join(sent) + "\n", hint=hint,
                      omitted_files=report.omitted_files, omitted_lines=report.omitted_lines)
    return limited, report
