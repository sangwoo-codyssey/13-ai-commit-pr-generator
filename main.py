"""AI 커밋/PR 초안 생성기. 사용법: python main.py {commit,pr} [옵션] — 자세히는 --help"""

import sys

# macOS 기본 python3 는 3.9 다. 문법 오류로 죽기 전에 이유를 알려준다.
if sys.version_info < (3, 10):
    sys.exit(f"Python 3.10 이상이 필요합니다. 현재: {sys.version.split()[0]}")

from gitgen.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
