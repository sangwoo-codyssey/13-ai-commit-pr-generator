"""명령행 진입점: 인자 해석 → 전제조건 → 수집 → safe-mode → 프롬프트 → 생성 → 출력.

로그([INFO]/[WARN]/[ERROR]/[DONE])는 stderr, 결과는 stdout 으로 나눈다 —
결과만 파일로 받거나 다른 명령으로 넘길 수 있게.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from collections.abc import Mapping
from pathlib import Path

from gitgen import prompts, rules
from gitgen.ai_client import API_URL, MESSAGES_PATH, AIError, ClaudeClient
from gitgen.context import Collected
from gitgen.generator import generate
from gitgen.git_collector import GitError, NoChanges, collect_commit, collect_pr
from gitgen.render import render_draft, render_dry_run
from gitgen.safe_mode import apply_safe_mode

API_KEY_ENV = "AI_API_KEY"
BASE_URL_ENV = "AI_API_BASE_URL"    # 비우면 공식 도메인. 뒤에 /v1/messages 는 도구가 붙인다
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 1024
DEFAULT_BASE = "develop"

EXIT_OK = 0
EXIT_ERROR = 1          # 2 는 argparse 가 사용법 오류에 쓴다

SOURCE_LABEL = {
    "staged": "staged",
    "unstaged": "스테이징 안 된 변경",
    "branch": "브랜치 비교",
}


def log(level: str, message: str) -> None:
    """`[LEVEL] 첫 줄` 뒤의 줄은 접두어 폭만큼 들여 쓴다."""
    prefix = f"[{level}] "
    first, *rest = message.split("\n")
    lines = [prefix + first] + [" " * len(prefix) + line for line in rest]
    print("\n".join(lines), file=sys.stderr, flush=True)


def temperature_arg(text: str) -> float | None:
    """0.0~1.0, 또는 `none` — temperature 를 받지 않는 모델에 아예 보내지 않을 때."""
    if text.strip().lower() == "none":
        return None
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"숫자(0.0~1.0) 또는 none 이어야 합니다: {text!r}") from None
    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError(f"0.0~1.0 범위여야 합니다: {value}")
    return value


def resolve_api_url(env: Mapping[str, str]) -> str:
    """base URL(도메인, 필요하면 게이트웨이 접두 경로까지)에 /v1/messages 를 붙인다.

    바꾸면 API Key 가 그 주소로 가므로 https 만 받는다. 예외는 로컬 주소의 http 뿐이다
    (로컬 프록시·테스트용 가짜 서버).
    """
    base = env.get(BASE_URL_ENV, "").strip().rstrip("/")
    if not base:
        return API_URL
    parts = urllib.parse.urlsplit(base)
    if not parts.hostname:
        raise ValueError(f"{BASE_URL_ENV} 값이 URL 이 아닙니다: {base}")
    if not (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS)):
        raise ValueError(f"{BASE_URL_ENV} 는 https 주소여야 합니다 — API Key 가 평문으로 전송됩니다: {base}\n"
                         "(http 는 localhost·127.0.0.1·::1 만 허용)")
    if parts.path.endswith(MESSAGES_PATH) or parts.query or parts.fragment:
        raise ValueError(f"{BASE_URL_ENV} 에는 도메인까지만 적으세요 (예: https://api.example.com). "
                         f"{MESSAGES_PATH} 는 도구가 붙입니다: {base}")
    return base + MESSAGES_PATH


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"정수여야 합니다: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"1 이상이어야 합니다: {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    # 공통 옵션은 부모 파서에 두어 `commit --model ...` 처럼 서브커맨드 뒤에 쓰게 한다.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"호출할 모델 ID (기본 {DEFAULT_MODEL})")
    common.add_argument("--temperature", type=temperature_arg, default=DEFAULT_TEMPERATURE,
                        help=f"0.0~1.0, 낮을수록 일관적 (기본 {DEFAULT_TEMPERATURE}). "
                             "none 이면 보내지 않는다")
    common.add_argument("--max-tokens", type=positive_int, default=DEFAULT_MAX_TOKENS,
                        help=f"응답 길이 상한 (기본 {DEFAULT_MAX_TOKENS})")
    common.add_argument("--safe-mode", action=argparse.BooleanOptionalAction, default=True,
                        help="민감정보 마스킹 + diff 전송량 제한 (기본 켜짐)")
    common.add_argument("--hint", metavar="TEXT",
                        help="변경 이유 한 줄 — diff 에 없는 '왜'를 알려준다")
    common.add_argument("--dry-run", action="store_true",
                        help="API 를 호출하지 않고 보낼 프롬프트만 출력한다")

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="git status / git diff 를 읽어 커밋 메시지·PR 초안을 AI 로 생성한다.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="{commit,pr}")
    sub.add_parser("commit", parents=[common], help="커밋 메시지 초안 생성")
    pr = sub.add_parser("pr", parents=[common], help="PR 제목·본문 초안 생성")
    pr.add_argument("--base", default=DEFAULT_BASE,
                    help=f"비교 기준 브랜치 (기본 {DEFAULT_BASE}) — base...HEAD 를 비교한다")
    return parser


def main(argv: list[str] | None = None, cwd: str | None = None,
         env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cwd = cwd or os.getcwd()
    env = os.environ if env is None else env
    try:
        return run(args, cwd, env)
    except (GitError, AIError, NotImplementedError) as e:
        log("ERROR", str(e))
        return EXIT_ERROR
    except KeyboardInterrupt:
        log("ERROR", "사용자가 중단했습니다.")
        return 130


def run(args: argparse.Namespace, cwd: str, env: Mapping[str, str]) -> int:
    # 비용이 드는 일을 하기 전에 로컬 전제조건부터 확인한다.
    if not (Path(cwd) / ".git").exists():
        log("ERROR", "Git 저장소의 루트 디렉토리에서 실행하세요.\n"
                     f"현재 위치에 .git 이 없습니다: {cwd}")
        return EXIT_ERROR
    api_key = env.get(API_KEY_ENV, "").strip()
    if not args.dry_run and not api_key:
        log("ERROR", f"{API_KEY_ENV} 환경변수가 설정되지 않았습니다.\n"
                     f'예) export {API_KEY_ENV}="YOUR_KEY"')
        return EXIT_ERROR
    try:
        api_url = resolve_api_url(env)
    except ValueError as e:
        log("ERROR", str(e))
        return EXIT_ERROR
    if api_url != API_URL:
        log("INFO", f"API 엔드포인트: {api_url} ({BASE_URL_ENV})")

    try:
        collected = collect(args, cwd)
    except NoChanges as e:
        log("INFO", str(e))
        return EXIT_OK
    report_collected(collected)

    ctx = collected.context
    if args.safe_mode:
        ctx, report = apply_safe_mode(ctx)
        log("INFO", report.summary())
    else:
        log("WARN", f"safe-mode 꺼짐: diff {ctx.diff_line_count}줄을 마스킹·제한 없이 전송합니다")

    if args.command == "commit":
        spec, prompt = rules.COMMIT, prompts.build_commit_prompt(ctx)
    else:
        spec, prompt = rules.PR, prompts.build_pr_prompt(ctx)

    if args.dry_run:
        log("INFO", "--dry-run: AI API 를 호출하지 않습니다")
        print(render_dry_run(prompt, api_url, args.model, args.temperature, args.max_tokens))
        return EXIT_OK

    client = ClaudeClient(api_key, model=args.model, temperature=args.temperature,
                          max_tokens=args.max_tokens, url=api_url)
    result = generate(ctx, client, spec, prompt, prompts.build_retry_message, log)

    log("DONE", f"{spec.label} 생성 완료 (API 호출 {result.calls}회 · "
                f"입력 {result.input_tokens:,} / 출력 {result.output_tokens:,} 토큰)")
    for warning in result.warnings:
        log("WARN", warning)
    print(render_draft(result.draft))
    return EXIT_OK


def collect(args: argparse.Namespace, cwd: str) -> Collected:
    if args.command == "commit":
        return collect_commit(cwd, hint=args.hint)
    return collect_pr(cwd, base=args.base, hint=args.hint)


def report_collected(collected: Collected) -> None:
    ctx = collected.context
    if ctx.mode == "pr":
        log("INFO", f"현재 브랜치: {ctx.branch or '(detached HEAD)'} → 비교 기준: {ctx.base}")
        log("INFO", f"Git diff 수집 완료: {len(ctx.files)}개 파일, {ctx.diff_line_count}줄 "
                    f"({ctx.base}...HEAD)")
    else:
        extra = f" (미추적 {len(ctx.untracked)}개 별도)" if ctx.untracked else ""
        log("INFO", f"Git status 수집 완료: {len(ctx.files)}개 파일 변경 감지{extra}")
        log("INFO", f"Git diff 수집 완료: {ctx.diff_line_count}줄 ({SOURCE_LABEL[ctx.source]})")
    for note in collected.notes:
        log("INFO", note)
