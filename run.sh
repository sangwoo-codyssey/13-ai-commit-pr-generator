#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 표준 라이브러리만 쓴다 — 별도 설치 없이 바로 실행된다.
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

# API Key 를 셸 히스토리에 남기지 않으려면 이 디렉터리의 .env 에 두고 run.sh 로 실행한다.
# 변수마다 이미 export 된 값이 우선이다 (AI_API_KEY, AI_API_BASE_URL). 프로그램은 환경변수만 읽는다.
load_env() {
  [ -f "$SCRIPT_DIR/.env" ] || return 0
  local exported_key="${AI_API_KEY:-}" exported_url="${AI_API_BASE_URL:-}"
  set -a
  . "$SCRIPT_DIR/.env"
  set +a
  # set -e 아래라 `[ ... ] && ...` 를 마지막 줄에 두면 거짓일 때 스크립트가 끝난다 — if 로 쓴다.
  if [ -n "$exported_key" ]; then export AI_API_KEY="$exported_key"; fi
  if [ -n "$exported_url" ]; then export AI_API_BASE_URL="$exported_url"; fi
  return 0
}

# 호출한 위치(대상 Git 저장소의 루트)에서 실행한다 — cd 하지 않는다.
cmd_run() {
  check_python "$@"
  load_env
  exec "$PYTHON" "$SCRIPT_DIR/main.py" "$@"
}

# 테스트는 실제 API 를 부르지 않는다. 혹시 모를 실제 키도 넘기지 않는다.
cmd_test() {
  check_python
  cd "$SCRIPT_DIR"
  unset AI_API_KEY AI_API_BASE_URL AI_LOG_FILE
  exec "$PYTHON" -m unittest discover -s tests -v
}

SUB="${1:-}"
shift || true
case "$SUB" in
  run)  cmd_run "$@" ;;
  test) cmd_test ;;
  *)    echo "사용법: $0 {run {commit|pr} [옵션...]|test}"; exit 1 ;;
esac
