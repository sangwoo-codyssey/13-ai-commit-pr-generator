"""터미널에 내보낼 텍스트 — 사용자가 검토·복사하기 쉽게 구획을 나눈다."""

from __future__ import annotations

from gitgen.context import Prompt
from gitgen.rules import CommitDraft, Draft, PrDraft

COMMIT_HEADER = "--- Commit Message ---"
PR_TITLE_HEADER = "--- PR Title ---"
PR_BODY_HEADER = "--- PR Body ---"


def render_draft(draft: Draft) -> str:
    if isinstance(draft, CommitDraft):
        return "\n".join([COMMIT_HEADER, draft.text(), "-" * len(COMMIT_HEADER)])
    assert isinstance(draft, PrDraft)
    return "\n".join([PR_TITLE_HEADER, draft.title, "", PR_BODY_HEADER, draft.body_text,
                      "-" * len(PR_BODY_HEADER)])


def render_dry_run(prompt: Prompt, model: str, temperature: float | None, max_tokens: int) -> str:
    """API 에 보낼 내용 그대로 — safe-mode 가 적용된 뒤의 프롬프트다."""
    temp = "보내지 않음" if temperature is None else f"{temperature:g}"
    return "\n".join([
        "===== DRY RUN — AI API 를 호출하지 않습니다 =====",
        f"model {model} · temperature {temp} · max_tokens {max_tokens}",
        "----- system -----",
        prompt.system,
        "----- user -----",
        prompt.user.rstrip("\n"),
        "=" * 44,
    ])
