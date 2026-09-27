#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 표준 라이브러리 위주로 간다. Docker 는 쓰지 않는다.
#
# 과제는 Python 3.10 이상을 요구하는데 macOS 기본 python3 는 3.9 다.
# PYTHON 환경변수가 있으면 그걸 존중하고, 없으면 3.10+ 인터프리터를 직접 찾는다.

version_ok() {
  command -v "$1" &>/dev/null && \
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' &>/dev/null
}

resolve_python() {
  [ -n "$PYTHON" ] && return
  for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
    if version_ok "$candidate"; then PYTHON="$candidate"; return; fi
  done
  PYTHON=python3
}

check_python() {
  resolve_python
  if ! version_ok "$PYTHON"; then
    echo "Python 3.10 이상이 필요합니다. 현재: $("$PYTHON" -V 2>&1)" >&2
    echo "PYTHON 환경변수로 지정하세요. 예) PYTHON=python3.12 $0 $*" >&2
    exit 1
  fi
}

cmd_run() {
  check_python "$@"
  if [ ! -f main.py ]; then
    echo "main.py 가 아직 없습니다. 먼저 CLI 를 구현해야 합니다." >&2
    exit 1
  fi
  exec "$PYTHON" main.py "$@"
}

cmd_test() {
  check_python
  if [ ! -d tests ]; then
    echo "tests/ 가 아직 없습니다 (미착수)."
    return 0
  fi
  exec "$PYTHON" -m unittest discover -s tests -v
}

SUB="${1:-}"
shift || true
case "$SUB" in
  run)  cmd_run "$@" ;;
  test) cmd_test ;;
  *)    echo "사용법: $0 {run [commit|pr] [옵션...]|test}"; exit 1 ;;
esac
