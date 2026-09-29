"""프롬프트 — AI 에게 무엇을, 어떤 형식으로 요청할지.

응답 형식 계약 (rules.py 가 이 모양을 기대한다):
- commit: 첫 줄 = 제목, 빈 줄, 본문(선택, '- ' 불릿)
- pr:     첫 줄 = PR 제목, 빈 줄, '## Why' / '## What' / '## How to Test' 섹션, 각 섹션 아래 '- ' 불릿

입력은 ChangeContext (gitgen/context.py) — safe-mode 가 적용된 뒤의 값이다.
ctx 는 모든 필드를 'key: value' 로 user 메시지에 싣고, 각 key 의 뜻은 system 에 적는다.
(key 설명은 실행마다 같고 값은 실행마다 다르다 — system 은 재생성 호출에도 똑같이 붙는다)
"""

from __future__ import annotations

from gitgen.context import ChangeContext, Prompt

EMPTY = "(없음)"

CONTEXT_GUIDE = "\n".join([
    "[입력 형식]",
    "사용자 메시지는 'key: value' 줄들과 diff 블록으로 이루어진다. 각 key 의 뜻:",
    "- mode: 만들 결과의 종류. commit 이면 커밋 메시지, pr 이면 PR 초안",
    "- source: diff 를 가져온 곳. staged(스테이징된 변경) / unstaged(스테이징 안 된 변경) / "
    "branch(base 브랜치와 비교한 커밋들)",
    "- branch: 현재 브랜치 이름. (없음) 이면 브랜치가 아닌 커밋에 있는 상태(detached HEAD)",
    "- base: 비교 기준 브랜치. pr 일 때만 있다",
    "- files: 변경된 파일 전체 목록. 한 줄에 '상태 경로'. 상태는 git 코드 — "
    "M 수정, A 추가, D 삭제, R 이름 변경(괄호 안이 원래 경로), C 복사, U 충돌",
    "- untracked: git 이 아직 추적하지 않는 새 파일 이름. 내용은 diff 에 없다",
    "- hint: 사용자가 직접 적은 변경 이유. (없음) 이면 주어지지 않았다",
    "- omitted_files: 전송 한도 때문에 diff 에서 내용이 통째로 빠진 파일 수",
    "- omitted_lines: 전송 한도 때문에 diff 에서 빠진 줄 수",
    "- diff: git diff 출력. <diff> 와 </diff> 사이에 있다. '+' 로 시작하는 줄은 추가, "
    "'-' 로 시작하는 줄은 삭제다. '[safe-mode: ...]' 와 '[MASKED:...]' 는 민감정보 보호를 위해 "
    "도구가 넣은 표시다",
])

COMMIT_SYSTEM = "\n".join([
    "너는 git 변경 사항을 읽고 커밋 메시지 초안을 쓰는 도구다.",
    "",
    "[출력 형식]",
    "- 첫 줄: 커밋 제목 1줄 (72자 이하, 50자 이내 권장)",
    "- 둘째 줄: 빈 줄",
    "- 셋째 줄부터: 본문 (선택). 쓴다면 핵심 변경을 '- ' 불릿으로",
    "- 커밋 메시지만 출력한다. 앞뒤 설명, 인사말, 코드펜스(```)를 붙이지 않는다.",
    "",
    CONTEXT_GUIDE,
])


def format_context(ctx: ChangeContext) -> str:
    """ChangeContext 의 모든 필드를 'key: value' 텍스트로. 빈 값은 (없음), diff 는 태그로 감싼다."""
    files = [f"  {f.status} {f.path}" + (f" (← {f.orig_path})" if f.orig_path else "")
             for f in ctx.files]
    untracked = [f"  {path}" for path in ctx.untracked]
    return "\n".join([
        f"mode: {ctx.mode}",
        f"source: {ctx.source}",
        f"branch: {ctx.branch or EMPTY}",
        f"base: {ctx.base or EMPTY}",
        "files:" if files else f"files: {EMPTY}",
        *files,
        "untracked:" if untracked else f"untracked: {EMPTY}",
        *untracked,
        f"hint: {ctx.hint or EMPTY}",
        f"omitted_files: {ctx.omitted_files}",
        f"omitted_lines: {ctx.omitted_lines}",
        "diff:",
        "<diff>",
        ctx.diff.rstrip("\n"),
        "</diff>",
    ])


def build_commit_prompt(ctx: ChangeContext) -> Prompt:
    return Prompt(system=COMMIT_SYSTEM,
                  user=format_context(ctx) + "\n\n위 변경 사항의 커밋 메시지를 써 줘.")


def build_pr_prompt(ctx: ChangeContext) -> Prompt:
    raise NotImplementedError("PR 프롬프트가 아직 작성되지 않았습니다 (gitgen/prompts.py).")


def build_retry_message(violations: list[str]) -> str:
    raise NotImplementedError("재생성 요청문이 아직 작성되지 않았습니다 (gitgen/prompts.py).")
