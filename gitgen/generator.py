"""생성 흐름: 1회 호출 → 해석·후처리 → 검증 → (필요하면) 1회 재생성 → 최후 후처리.

재생성은 1차 응답과 위반 목록을 대화에 이어 붙여 보낸다. 같은 요청을 그대로 다시 보내면
같은 답이 나오기 쉽기 때문이다. 다음 경우에는 재생성하지 않는다.
- 1차 응답이 max_tokens 에서 끊김: 같은 상한으로 다시 불러도 또 끊긴다
- 호출 예산을 다 씀
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from gitgen.ai_client import AIError, CallBudget, Completion
from gitgen.context import ChangeContext, Prompt
from gitgen.rules import Draft, OutputSpec

Log = Callable[[str, str], None]


class Client(Protocol):
    model: str
    budget: CallBudget

    def complete(self, system: str, messages: list[dict[str, str]]) -> Completion: ...


@dataclass
class Result:
    draft: Draft
    warnings: list[str]
    calls: int
    input_tokens: int
    output_tokens: int
    regenerated: bool


def generate(ctx: ChangeContext, client: Client, spec: OutputSpec, prompt: Prompt,
             build_retry_message: Callable[[list[str]], str], log: Log) -> Result:
    messages = [{"role": "user", "content": prompt.user}]
    first = _request(client, prompt.system, messages, log)
    completions = [first]
    last = first
    draft = spec.parse(first.text)
    findings = spec.validate(draft, ctx)
    regenerated = False

    if findings.errors:
        skip = _why_not_regenerate(first, client)
        if skip:
            log("WARN", f"출력 규칙 위반 {len(findings.errors)}건 — {skip}")
        else:
            log("INFO", f"출력 규칙 위반 {len(findings.errors)}건 → 1회 재생성합니다\n"
                        + "\n".join(f"- {error}" for error in findings.errors))
            retry = messages + [
                {"role": "assistant", "content": first.text},
                {"role": "user", "content": build_retry_message(findings.errors)},
            ]
            try:
                second = _request(client, prompt.system, retry, log)
            except AIError as e:
                log("WARN", f"재생성 호출이 실패해 1차 응답을 씁니다.\n{e}")
            else:
                completions.append(second)
                second_draft = spec.parse(second.text)
                second_findings = spec.validate(second_draft, ctx)
                if len(second_findings.errors) <= len(findings.errors):
                    draft, findings, last, regenerated = second_draft, second_findings, second, True
                else:
                    log("WARN", "재생성 결과가 규칙을 더 많이 어겨 1차 응답을 씁니다.")

    warnings = list(findings.warnings)
    if findings.errors:
        draft, fixes = spec.finalize(draft, ctx)
        warnings.extend(fixes)
    if last.truncated:
        warnings.append("응답이 max_tokens 에서 끊겼습니다. 내용이 잘렸을 수 있으니 --max-tokens 를 늘려 보세요.")

    return Result(
        draft=draft, warnings=warnings, calls=len(completions),
        input_tokens=sum(c.input_tokens for c in completions),
        output_tokens=sum(c.output_tokens for c in completions),
        regenerated=regenerated,
    )


def _why_not_regenerate(first: Completion, client: Client) -> str | None:
    if first.truncated:
        return "응답이 max_tokens 에서 끊겨 재생성하지 않습니다 (--max-tokens 를 늘려 보세요)."
    if client.budget.remaining <= 0:
        return "호출 한도를 다 써서 재생성하지 않습니다."
    return None


def _request(client: Client, system: str, messages: list[dict[str, str]], log: Log) -> Completion:
    budget = client.budget
    log("INFO", f"AI API 요청 중... (호출 {budget.used + 1}/{budget.limit}, 모델 {client.model})")
    completion = client.complete(system, messages)
    log("INFO", f"응답 수신: 입력 {completion.input_tokens:,} / 출력 {completion.output_tokens:,} 토큰 "
                f"(stop_reason={completion.stop_reason})")
    return completion
