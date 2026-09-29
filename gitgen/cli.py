"""명령행 진입점: 인자 해석 → 전제조건 확인 → 변경 수집 → 출력.

로그([INFO]/[WARN]/[ERROR]/[DONE])는 stderr, 결과는 stdout 으로 나눈다 —
결과만 파일로 받거나 다른 명령으로 넘길 수 있게.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from gitgen.context import ChangeContext, Collected
from gitgen.git_collector import GitError, NoChanges, collect_commit, collect_pr
from gitgen.safe_mode import apply_safe_mode

API_KEY_ENV = "AI_API_KEY"
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
    print("\n".join(lines), file=sys.stderr)


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
                        help="API 를 호출하지 않고 보낼 내용만 출력한다")

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
    except GitError as e:
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
    if not args.dry_run and not env.get(API_KEY_ENV, "").strip():
        log("ERROR", f"{API_KEY_ENV} 환경변수가 설정되지 않았습니다.\n"
                     f'예) export {API_KEY_ENV}="YOUR_KEY"')
        return EXIT_ERROR

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

    if args.dry_run:
        log("INFO", "--dry-run: AI API 를 호출하지 않습니다")
        print(format_dry_run(ctx))
        return EXIT_OK

    log("ERROR", "AI API 호출 단계는 아직 구현되지 않았습니다.")
    return EXIT_ERROR


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


def format_dry_run(ctx: ChangeContext) -> str:
    lines = [
        "===== DRY RUN — AI API 를 호출하지 않습니다 =====",
        f"모드: {ctx.mode} / diff 출처: {SOURCE_LABEL[ctx.source]}",
        f"브랜치: {ctx.branch or '(detached HEAD)'}"
        + (f" → 비교 기준: {ctx.base}" if ctx.base else ""),
        f"변경 파일 ({len(ctx.files)}):",
    ]
    for f in ctx.files:
        renamed = f" (← {f.orig_path})" if f.orig_path else ""
        lines.append(f"  {f.status}  {f.path}{renamed}")
    if ctx.untracked:
        lines.append(f"미추적 ({len(ctx.untracked)}):")
        lines.extend(f"  {path}" for path in ctx.untracked)
    if ctx.hint:
        lines.append(f"변경 이유(--hint): {ctx.hint}")
    if ctx.omitted_files or ctx.omitted_lines:
        lines.append(f"생략(전송 한도): 파일 {ctx.omitted_files}개 · {ctx.omitted_lines}줄")
    lines.append(f"----- diff ({ctx.diff_line_count}줄) -----")
    lines.append(ctx.diff.rstrip("\n"))
    lines.append("=" * 40)
    return "\n".join(lines)
