"""프롬프트 — AI 에게 무엇을, 어떤 형식으로 요청할지.

응답 형식 계약 (rules.py 가 이 모양을 기대한다):
- commit: 첫 줄 = 제목, 빈 줄, 본문(선택, '- ' 불릿)
- pr:     첫 줄 = PR 제목, 빈 줄, '## Why' / '## What' / '## How to Test' 섹션, 각 섹션 아래 '- ' 불릿

입력은 ChangeContext (gitgen/context.py) — safe-mode 가 적용된 뒤의 값이다.
"""

from __future__ import annotations

from gitgen.context import ChangeContext, Prompt


def build_commit_prompt(ctx: ChangeContext) -> Prompt:
    raise NotImplementedError("커밋 프롬프트가 아직 작성되지 않았습니다 (gitgen/prompts.py).")


def build_pr_prompt(ctx: ChangeContext) -> Prompt:
    raise NotImplementedError("PR 프롬프트가 아직 작성되지 않았습니다 (gitgen/prompts.py).")


def build_retry_message(violations: list[str]) -> str:
    raise NotImplementedError("재생성 요청문이 아직 작성되지 않았습니다 (gitgen/prompts.py).")
