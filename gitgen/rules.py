"""출력 규칙 — AI 응답을 해석하고, 형식을 다듬고, 규칙 위반을 찾고, 최후에 고친다.

1. 해석 + 기계적 후처리: 내용을 만들지 않는 형식 정리만 한다 (코드펜스·라벨·불릿 기호·AI 흔적 줄).
2. 검증: 내용이 있어야 고칠 수 있는 위반은 errors(재생성 사유), 권장 위반은 warnings.
   errors 문장은 재생성 때 모델에게 그대로 되먹이므로 '무엇을 하라'가 드러나게 쓴다.
3. 최후 후처리: 재생성 뒤에도 남은 위반만 — 제목 자르기, 빈 섹션에 자리표시자. 경고를 남긴다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from gitgen.context import ChangeContext

COMMIT_TITLE_RECOMMENDED = 50
COMMIT_TITLE_MAX = 72
PR_TITLE_MAX = 80
PR_SECTIONS = ("Why", "What", "How to Test")
PLACEHOLDER_BULLET = "- (직접 작성 필요)"
# Conventional Commits type — 커밋·PR 제목은 '<type>: <요약>' (scope·! 는 표준이라 허용)
COMMIT_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore",
                "revert")

_FENCE = re.compile(r"^\s*```")
_SEPARATOR = re.compile(r"^\s*-{3,}(?:\s.*\s-{3,})?\s*$")          # ---, --- Commit Message ---
_AI_TRACE = re.compile(r"^\s*(?:co-authored-by:|generated (?:by|with)\b|🤖)", re.I)
_BULLET_MARK = re.compile(r"^(\s*)(?:[*•·+]|\d+[.)])\s+")
_TITLE_LABEL = re.compile(
    r"^(?:커밋\s*메시지|커밋\s*제목|PR\s*제목|제목|commit\s*message|title|subject)\s*[:：]\s*", re.I)
_BODY_LABELS = {"본문:", "본문", "body:", "커밋 본문:", "pr 본문:"}
_SECTION = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(why|what|how\s*to\s*test)\s*(?:\*\*)?\s*"
    r"(?:\([^)]*\))?\s*[:：]?\s*(?:\*\*)?\s*$", re.I)
_OTHER_HEADER = re.compile(r"^\s*#{1,6}\s+(\S.*)$")
_TYPE_PREFIX = re.compile(r"^([A-Za-z]+)(\([^)]*\))?(!)?\s*[:：]\s*(\S.*)$")
_CONVENTIONAL = re.compile(rf"^(?:{'|'.join(COMMIT_TYPES)})(?:\([^)]+\))?!?: \S")


@dataclass
class Findings:
    errors: list[str] = field(default_factory=list)      # 재생성 사유
    warnings: list[str] = field(default_factory=list)    # 알리기만


# ---------------------------------------------------------------- 공통 후처리

def tidy_lines(text: str) -> list[str]:
    """형식만 정리한다. 응답 전체를 감싼 코드펜스만 벗기고, 본문 안 코드 블록은 건드리지 않는다."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    if len(lines) >= 2 and _FENCE.match(lines[0]) and lines[-1].strip() == "```":
        lines = lines[1:-1]

    tidied: list[str] = []
    in_code = False
    for line in lines:
        if _FENCE.match(line):
            in_code = not in_code
        elif not in_code:
            if _AI_TRACE.match(line) or _SEPARATOR.match(line):
                continue                                  # 사용자 규칙: AI 작성 흔적을 남기지 않는다
            line = _BULLET_MARK.sub(r"\1- ", line)
        if not line.strip() and (not tidied or not tidied[-1].strip()):
            continue                                      # 연속 빈 줄 · 맨 앞 빈 줄
        tidied.append(line)
    while tidied and not tidied[-1].strip():
        tidied.pop()
    return tidied


def clean_title(line: str) -> str:
    title = line.strip()
    title = re.sub(r"^#+\s*", "", title)
    title = re.sub(r"^-\s+", "", title)
    if title.startswith("**"):                            # "**제목:** ..." 의 굵게 표시
        title = title.replace("**", "", 2)
    title = _TITLE_LABEL.sub("", title)
    for _ in range(2):                                    # "`feat: x`." 처럼 겹쳐 있을 수 있다
        if title.endswith(".") and not title.endswith(".."):
            title = title[:-1].rstrip()
        for left, right in (('"', '"'), ("'", "'"), ("`", "`"), ("“", "”"), ("**", "**")):
            if len(title) > len(left) + len(right) and title.startswith(left) and title.endswith(right):
                title = title[len(left):-len(right)].strip()
    return title


def is_bullet(line: str) -> bool:
    return line.lstrip().startswith("- ") and len(line.strip()) > 2


def bulletize(lines: list[str]) -> list[str]:
    return [f"- {line.strip()}" if line.strip() and not is_bullet(line) else line for line in lines]


def truncate_title(title: str, limit: int) -> str:
    """단어 경계에서 잘라 `…` 를 붙인다. 경계가 너무 앞이면 그냥 자른다."""
    if len(title) <= limit:
        return title
    cut = title[:limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def _strip_blank_edges(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _changed_names(ctx: ChangeContext) -> set[str]:
    paths = [f.path for f in ctx.files] + list(ctx.untracked)
    return {name for path in paths for name in (path, PurePosixPath(path).name) if name}


def _title_findings(title: str, limit: int, what: str, recommended: int | None = None) -> Findings:
    findings = Findings()
    if not title:
        findings.errors.append(f"{what} 줄이 비어 있습니다. 첫 줄에 {what} 1줄을 쓰세요.")
    elif len(title) > limit:
        findings.errors.append(f"{what}이 {len(title)}자입니다. {limit}자 이하로 줄이세요.")
    elif recommended and len(title) > recommended:
        findings.warnings.append(f"{what}이 {len(title)}자입니다 ({recommended}자 이내 권장, 최대 {limit}자).")
    return findings


def _type_prefix_error(title: str, what: str) -> list[str]:
    if not title or has_type_prefix(title):               # 빈 제목은 _title_findings 가 따로 알린다
        return []
    return [f"{what}이 '<type>: <요약>' 형식이 아닙니다. type 은 {', '.join(COMMIT_TYPES)} 중 하나를 소문자로 쓰세요."]


def _type_prefix_warning(original: str, fixed: str, what: str) -> list[str]:
    """어떤 type 인지는 내용 판단이라 지어내지 않고 알리기만 한다."""
    if not original or has_type_prefix(fixed):
        return []
    return [f"{what}에 type 접두어({', '.join(COMMIT_TYPES)})가 없습니다 — 직접 붙이세요."]


def _fix_title(title: str, limit: int, what: str, warnings: list[str]) -> str:
    if not title:
        warnings.append(f"{what}이 비어 있어 자리표시자를 넣었습니다 — 직접 작성하세요.")
        return f"({what} 직접 작성 필요)"
    if len(title) > limit:
        fixed = truncate_title(title, limit)
        warnings.append(f"{what}이 {len(title)}자라 {len(fixed)}자로 잘랐습니다 — 확인하세요.")
        return fixed
    return title


# ---------------------------------------------------------------- 커밋 메시지

@dataclass
class CommitDraft:
    title: str
    body: list[str]

    @property
    def body_text(self) -> str:
        return "\n".join(self.body)

    def text(self) -> str:
        return self.title + (f"\n\n{self.body_text}" if self.body else "")


def normalize_type_prefix(title: str) -> str:
    """알려진 type 이면 대소문자·공백만 고친다: 'Feat : 추가' → 'feat: 추가'. 모르는 type 은 그대로."""
    match = _TYPE_PREFIX.match(title)
    if not match or match.group(1).lower() not in COMMIT_TYPES:
        return title
    kind, scope, bang, summary = match.groups()
    return f"{kind.lower()}{scope or ''}{bang or ''}: {summary}"


def has_type_prefix(title: str) -> bool:
    return bool(_CONVENTIONAL.match(title))


def parse_commit(text: str) -> CommitDraft:
    lines = tidy_lines(text)
    title, i = "", 0
    while i < len(lines) and not title:                  # "커밋 메시지:" 처럼 라벨만 있는 줄은 건너뛴다
        title = clean_title(lines[i])
        i += 1
    body = [line for line in lines[i:] if line.strip().lower() not in _BODY_LABELS]
    return CommitDraft(normalize_type_prefix(title), _strip_blank_edges(body))


def validate_commit(draft: CommitDraft, ctx: ChangeContext) -> Findings:
    findings = _title_findings(draft.title, COMMIT_TITLE_MAX, "커밋 제목", COMMIT_TITLE_RECOMMENDED)
    findings.errors.extend(_type_prefix_error(draft.title, "커밋 제목"))
    if _body_lacks_summary(draft, ctx):
        findings.errors.append("본문에 '- ' 불릿도 변경된 파일 이름도 없습니다. "
                               "핵심 변경 1~2개를 '- ' 불릿으로 쓰거나 본문을 빼세요.")
    return findings


def _body_lacks_summary(draft: CommitDraft, ctx: ChangeContext) -> bool:
    """과제 최소 기준: 본문이 있으면 불릿 1개 이상 또는 변경 파일 1개 이상 언급."""
    content = [line for line in draft.body if line.strip()]
    if not content or any(is_bullet(line) for line in content):
        return False
    return not any(name in draft.body_text for name in _changed_names(ctx))


def finalize_commit(draft: CommitDraft, ctx: ChangeContext) -> tuple[CommitDraft, list[str]]:
    warnings: list[str] = []
    title = _fix_title(draft.title, COMMIT_TITLE_MAX, "커밋 제목", warnings)
    warnings.extend(_type_prefix_warning(draft.title, title, "커밋 제목"))
    body = draft.body
    if _body_lacks_summary(draft, ctx):
        body = bulletize(body)
        warnings.append("본문 줄을 '- ' 불릿으로 바꿨습니다 — 확인하세요.")
    return CommitDraft(title, body), warnings


# ---------------------------------------------------------------- PR

@dataclass
class Section:
    header: str
    lines: list[str]
    required: bool                                        # Why / What / How to Test


@dataclass
class PrDraft:
    title: str
    preamble: list[str]
    sections: list[Section]

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.required and s.header == name), None)

    @property
    def body_text(self) -> str:
        parts = ["\n".join(self.preamble)] if self.preamble else []
        for s in self.sections:
            parts.append("\n".join([f"## {s.header}", *s.lines]))
        return "\n\n".join(parts)

    def text(self) -> str:
        return f"{self.title}\n\n{self.body_text}"


def _canonical_section(name: str) -> str:
    key = re.sub(r"\s+", " ", name.strip().lower())
    return {"why": "Why", "what": "What"}.get(key, "How to Test")


def parse_pr(text: str) -> PrDraft:
    lines = tidy_lines(text)
    title, i = "", 0
    while i < len(lines) and not title and not _SECTION.match(lines[i]):
        title = clean_title(lines[i])
        i += 1

    preamble: list[str] = []
    sections: list[Section] = []
    current: Section | None = None
    for line in lines[i:]:
        if match := _SECTION.match(line):
            name = _canonical_section(match.group(1))
            current = next((s for s in sections if s.required and s.header == name), None)
            if current is None:
                current = Section(name, [], True)
                sections.append(current)
        elif match := _OTHER_HEADER.match(line):
            current = Section(match.group(1).strip(), [], False)
            sections.append(current)
        elif current is not None:
            current.lines.append(line)
        else:
            preamble.append(line)

    for s in sections:
        s.lines = _strip_blank_edges(s.lines)
    # 필수 섹션은 Why → What → How to Test 순서로, 나머지는 원래 순서대로 뒤에 둔다.
    order = {name: index for index, name in enumerate(PR_SECTIONS)}
    sections.sort(key=lambda s: order.get(s.header, len(order)) if s.required else len(order))
    return PrDraft(normalize_type_prefix(title), _strip_blank_edges(preamble), sections)


def validate_pr(draft: PrDraft, ctx: ChangeContext) -> Findings:
    findings = _title_findings(draft.title, PR_TITLE_MAX, "PR 제목")
    findings.errors.extend(_type_prefix_error(draft.title, "PR 제목"))
    for name in PR_SECTIONS:
        section = draft.section(name)
        if section is None:
            findings.errors.append(f"'## {name}' 섹션이 없습니다. 이 헤더를 그대로 쓰고 아래에 '- ' 불릿을 쓰세요.")
        elif not any(is_bullet(line) for line in section.lines):
            findings.errors.append(f"'## {name}' 섹션에 '- ' 불릿이 없습니다. 최소 1개 쓰세요.")
    return findings


def finalize_pr(draft: PrDraft, ctx: ChangeContext) -> tuple[PrDraft, list[str]]:
    warnings: list[str] = []
    title = _fix_title(draft.title, PR_TITLE_MAX, "PR 제목", warnings)
    warnings.extend(_type_prefix_warning(draft.title, title, "PR 제목"))
    sections = [Section(s.header, list(s.lines), s.required) for s in draft.sections]
    for index, name in enumerate(PR_SECTIONS):
        section = next((s for s in sections if s.required and s.header == name), None)
        if section is None:
            sections.insert(index, Section(name, [PLACEHOLDER_BULLET], True))
            warnings.append(f"'## {name}' 섹션이 없어 자리표시자를 넣었습니다 — 직접 작성하세요.")
        elif not any(is_bullet(line) for line in section.lines):
            if any(line.strip() for line in section.lines):
                section.lines = bulletize(section.lines)
                warnings.append(f"'## {name}' 섹션의 줄을 '- ' 불릿으로 바꿨습니다 — 확인하세요.")
            else:
                section.lines = [PLACEHOLDER_BULLET]
                warnings.append(f"'## {name}' 섹션이 비어 자리표시자를 넣었습니다 — 직접 작성하세요.")
    return PrDraft(title, draft.preamble, sections), warnings


# ---------------------------------------------------------------- 종류별 묶음

Draft = CommitDraft | PrDraft


@dataclass(frozen=True)
class OutputSpec:
    label: str
    parse: Callable[[str], Draft]
    validate: Callable[[Draft, ChangeContext], Findings]
    finalize: Callable[[Draft, ChangeContext], tuple[Draft, list[str]]]


COMMIT = OutputSpec("커밋 메시지", parse_commit, validate_commit, finalize_commit)
PR = OutputSpec("PR 초안", parse_pr, validate_pr, finalize_pr)
