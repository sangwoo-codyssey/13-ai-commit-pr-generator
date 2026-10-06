#!/bin/bash
# 평가·시연용 데모 — Enter 를 누를 때마다 한 단계씩 실행하고 결과를 확인한다.
#
#   ./run.sh demo          (DEMO_KEEP=1 이면 끝난 뒤 예제 저장소를 남긴다)
#
# 임시 디렉터리에 예제 저장소(todo-app)를 만들어 그 안에서 실행한다 — 이 레포의 작업 트리·브랜치는 건드리지 않는다.
# 실제 AI API 를 부른다: 전부 실행하면 약 8회 (Haiku 4.5 기준 약 $0.05). 단계마다 s 로 건너뛸 수 있다.
#
#   0. 변경 없음        commit                         호출 0회
#   1. 커밋 메시지      commit                         호출 1회
#   2. PR 초안          pr --base develop              호출 1회
#   3. max_tokens       pr --max-tokens 200 / 0 / 999999   호출 2회 (0 은 도구가 막는다)
#   4. temperature      commit --temperature 0 ×2, 1.0 ×2  호출 4회

set -u
export GIT_PAGER=cat PAGER=cat          # git log·diff 가 less 를 띄우면 Enter 흐름이 끊긴다

TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP_BASE="${TMPDIR:-/tmp}"
DEMO_ROOT="$(mktemp -d "${TMP_BASE%/}/gitgen-demo.XXXXXX")"
REPO="$DEMO_ROOT/todo-app"              # 도구를 실행할 대상 저장소
OUT="$DEMO_ROOT/out"                    # 초안(stdout) 사본 — 저장소 밖에 둬야 미추적 파일로 잡히지 않는다

cleanup() {
  if [ "${DEMO_KEEP:-}" = 1 ]; then
    printf '\n예제 저장소를 남겼습니다: %s\n' "$REPO"
  else
    rm -rf "$DEMO_ROOT"
  fi
}
trap cleanup EXIT
trap 'echo; exit 130' INT

if [ -t 1 ]; then
  BOLD=$'\033[1m' DIM=$'\033[2m' CYAN=$'\033[36m' YELLOW=$'\033[33m' GREEN=$'\033[32m' RESET=$'\033[0m'
else
  BOLD='' DIM='' CYAN='' YELLOW='' GREEN='' RESET=''
fi

# --- 화면 도우미 ---

title() {   # title 번호 제목 호출수
  local line="════════════════════════════════════════════════════════════════"
  printf '\n%s%s\n %s. %s  %s(%s)%s\n%s%s\n' \
    "$BOLD" "$line" "$1" "$2" "$DIM" "$3" "$RESET$BOLD" "$line" "$RESET"
}

say() { printf '  %s\n' "$@"; }

show() {    # 명령을 보여 주고 실행한다
  printf '%s$ %s%s\n' "$CYAN" "$*" "$RESET"
  "$@"
}

ask() {     # Enter → 실행(0) · s → 건너뛰기(1) · q → 종료
  local reply
  printf '\n%s▶ %s%s  %s[Enter 실행 · s 건너뛰기 · q 종료]%s ' "$GREEN$BOLD" "$1" "$RESET" "$DIM" "$RESET"
  read -r reply || { echo; exit 0; }
  case "$reply" in
    q|Q) exit 0 ;;
    s|S) printf '%s  (건너뜀)%s\n' "$DIM" "$RESET"; return 1 ;;
  esac
  return 0
}

check() {   # 결과에서 볼 곳
  printf '\n%s확인 포인트%s\n' "$YELLOW$BOLD" "$RESET"
  local item
  for item in "$@"; do printf '  %s•%s %s\n' "$YELLOW" "$RESET" "$item"; done
}

gitgen() {  # gitgen 결과파일 인자... — 초안(stdout)은 화면과 파일에 함께, 로그(stderr)는 화면에만
  local out="$1"; shift
  printf '\n%s$ run.sh run %s%s\n' "$CYAN$BOLD" "$*" "$RESET"
  "$TOOL_DIR/run.sh" run "$@" | tee "$out"
  local code=${PIPESTATUS[0]}
  printf '%s(종료 코드 %d)%s\n' "$DIM" "$code" "$RESET"
}

compare() { # 같은 입력으로 두 번 뽑은 초안을 비교한다
  if [ ! -s "$1" ] || [ ! -s "$2" ]; then
    printf '%s→ 비교할 결과가 없습니다 (호출이 실패했거나 건너뜀)%s\n' "$YELLOW" "$RESET"
  elif cmp -s "$1" "$2"; then
    printf '%s→ 두 결과가 글자까지 같습니다%s\n' "$GREEN$BOLD" "$RESET"
  else
    printf '%s→ 두 결과가 다릅니다 (- 1회차 / + 2회차)%s\n' "$YELLOW$BOLD" "$RESET"
    diff -u "$1" "$2" | tail -n +3
  fi
}

draft_commit_message() {   # 1단계 초안에서 구분선 사이만 꺼낸다
  [ -s "$1" ] || return 1
  sed -n '/^--- Commit Message ---$/,/^----------------------$/p' "$1" | sed '1d;$d'
}

# --- 예제 저장소: 변경 단계별 파일 내용 ---

write_initial() {
  cat > README.md <<'EOF'
# todo-app

메모리에서 할 일 목록을 관리하는 작은 모듈.

## 테스트

    python3 -m unittest -v
EOF
  cat > todo.py <<'EOF'
"""할 일 목록을 메모리에서 관리한다."""


class TodoList:
    def __init__(self):
        self._items = []

    def add(self, title):
        """할 일을 추가하고 번호(1부터)를 돌려준다."""
        title = title.strip()
        if not title:
            raise ValueError("할 일 제목이 비어 있습니다")
        self._items.append({"title": title, "done": False})
        return len(self._items)

    def items(self):
        return [dict(item) for item in self._items]
EOF
  cat > test_todo.py <<'EOF'
import unittest

from todo import TodoList


class TodoListTest(unittest.TestCase):
    def test_add_returns_number(self):
        todos = TodoList()
        self.assertEqual(todos.add("장보기"), 1)
        self.assertEqual(todos.add("운동"), 2)

    def test_add_rejects_empty_title(self):
        with self.assertRaises(ValueError):
            TodoList().add("   ")
EOF
}

write_complete() {   # 변경 1 — 완료 처리
  cat >> todo.py <<'EOF'

    def complete(self, number):
        """번호로 할 일을 완료 처리한다."""
        self._item(number)["done"] = True

    def pending(self):
        """아직 끝나지 않은 할 일 제목만 돌려준다."""
        return [item["title"] for item in self._items if not item["done"]]

    def _item(self, number):
        if not 1 <= number <= len(self._items):
            raise IndexError(f"{number}번 할 일이 없습니다")
        return self._items[number - 1]
EOF
  cat >> test_todo.py <<'EOF'

    def test_complete_removes_from_pending(self):
        todos = TodoList()
        todos.add("장보기")
        todos.add("운동")
        todos.complete(1)
        self.assertEqual(todos.pending(), ["운동"])

    def test_complete_rejects_unknown_number(self):
        with self.assertRaises(IndexError):
            TodoList().complete(1)
EOF
}

write_storage() {    # 변경 2 — JSON 저장·불러오기
  cat > storage.py <<'EOF'
"""TodoList 를 JSON 파일로 저장하고 다시 불러온다."""

import json
from pathlib import Path

from todo import TodoList


def save(todos, path):
    data = [{"title": item["title"], "done": item["done"]} for item in todos.items()]
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path):
    todos = TodoList()
    for item in json.loads(Path(path).read_text(encoding="utf-8")):
        number = todos.add(item["title"])
        if item.get("done"):
            todos.complete(number)
    return todos
EOF
  cat > test_storage.py <<'EOF'
import tempfile
import unittest
from pathlib import Path

from storage import load, save
from todo import TodoList


class StorageTest(unittest.TestCase):
    def test_save_and_load_keep_done_state(self):
        todos = TodoList()
        todos.add("장보기")
        todos.add("운동")
        todos.complete(2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "todos.json"
            save(todos, path)
            self.assertEqual(load(path).items(), todos.items())
EOF
  cat >> README.md <<'EOF'

## 저장

`storage.save(todos, path)` 로 JSON 파일에 저장하고 `storage.load(path)` 로 다시 불러온다.
완료 여부도 함께 저장된다.
EOF
}

write_remove() {     # 변경 3 — 삭제
  cat >> todo.py <<'EOF'

    def remove(self, number):
        """번호로 할 일을 지우고 그 제목을 돌려준다. 뒤 번호는 하나씩 당겨진다."""
        self._item(number)
        return self._items.pop(number - 1)["title"]
EOF
  cat >> test_todo.py <<'EOF'

    def test_remove_shifts_numbers(self):
        todos = TodoList()
        todos.add("장보기")
        todos.add("운동")
        self.assertEqual(todos.remove(1), "장보기")
        self.assertEqual(todos.pending(), ["운동"])
EOF
}

# --- 시작 ---

if [ -z "${AI_API_KEY:-}" ] && [ ! -f "$TOOL_DIR/.env" ]; then
  printf '[ERROR] AI_API_KEY 가 없습니다. export 하거나 %s/.env 에 넣은 뒤 다시 실행하세요.\n' "$TOOL_DIR" >&2
  exit 1
fi

printf '\n%sgitgen 데모 — 커밋 · PR · max_tokens · temperature%s\n\n' "$BOLD" "$RESET"
say "도구        $TOOL_DIR/run.sh" \
    "예제 저장소  $REPO  (끝나면 삭제, DEMO_KEEP=1 이면 유지)" \
    "" \
    "0 변경 없음 (0회) → 1 commit (1회) → 2 pr (1회) → 3 max_tokens (2회) → 4 temperature (4회)" \
    "실제 API 호출 약 8회 · 형식 위반으로 재생성되면 그 단계에서 1회 더"

mkdir -p "$REPO" "$OUT"
cd "$REPO" || exit 1
git init -q -b main
git config user.name "demo"
git config user.email "demo@example.invalid"
git config commit.gpgsign false
write_initial
git add -A && git commit -q -m "feat: 할 일 추가·목록 조회"
git branch develop
git switch -q -c feature/complete-and-save develop

printf '\n예제 저장소를 만들었습니다 (main = develop 에 첫 커밋 1개, 지금은 작업 브랜치):\n'
show git log --oneline --graph --all
show ls

# 0. 변경 없음 ---------------------------------------------------------------

title 0 "변경 사항이 없을 때" "API 호출 0회"
say "작업 트리가 깨끗한 상태에서 commit 을 실행합니다."
show git status --short --branch
if ask "commit 실행"; then
  gitgen /dev/null commit
  check "[INFO] 변경 사항이 없습니다 … 로 끝나고 API 를 부르지 않는다 (종료 코드 0)"
fi

# 1. 커밋 메시지 -------------------------------------------------------------

title 1 "커밋 메시지 생성 — commit" "API 호출 1회"
write_complete
git add -A
say "todo.py 에 complete()·pending() 을, test_todo.py 에 테스트 2개를 추가하고 스테이징했습니다."
show git status --short
show git diff --cached --stat
if ask "commit 실행 (기본값: temperature 0.2 · max_tokens 2048)"; then
  gitgen "$OUT/commit.txt" commit
  check "[INFO] Git status·diff 수집 → safe-mode → 호출 1/2 → [DONE] 호출 횟수·토큰 수" \
        "제목 1줄이 '<type>: 요약' 이고 72자 이내인가 (50자 넘으면 [WARN])" \
        "본문 '- ' 불릿이 바뀐 파일·핵심 변경을 짚는가" \
        "로그는 stderr, '--- Commit Message ---' 블록만 stdout"
fi

# 2. PR 초안 -----------------------------------------------------------------

title 2 "PR 제목·본문 생성 — pr" "API 호출 1회"
if msg="$(draft_commit_message "$OUT/commit.txt")" && [ -n "$msg" ]; then
  printf '%s\n' "$msg" > "$DEMO_ROOT/commit-msg.txt"
  say "1단계 초안을 그대로 커밋 메시지로 씁니다 (실제로는 검토한 뒤 적용한다)."
else
  printf 'feat: 할 일 완료 처리 추가\n' > "$DEMO_ROOT/commit-msg.txt"
  say "1단계 초안이 없어 미리 정한 메시지로 커밋합니다."
fi
printf '%s$ git commit -F <초안>%s\n' "$CYAN" "$RESET"
git commit -q -F "$DEMO_ROOT/commit-msg.txt"
write_storage
git add -A
printf '%s$ git commit -m "feat: 할 일 목록 JSON 저장·불러오기"%s\n' "$CYAN" "$RESET"
git commit -q -m "feat: 할 일 목록 JSON 저장·불러오기"
say "" "develop 과 갈라진 뒤 커밋 2개 — pr 은 이 범위(develop...HEAD)를 비교합니다."
show git log --oneline develop..HEAD
show git diff --stat develop...HEAD
if ask "pr 실행"; then
  gitgen "$OUT/pr.txt" pr --base develop
  check "[INFO] 현재 브랜치 → 비교 기준, diff 수집 범위(develop...HEAD)" \
        "PR 제목 1줄 · 80자 이내" \
        "본문에 ## Why / ## What / ## How to Test 가 있고 섹션마다 '- ' 불릿이 1개 이상인가" \
        "How to Test 의 명령이 이 저장소에 실제로 있는가 (입력에 없는 명령이면 [WARN])" \
        "[DONE] 의 출력 토큰 수 — 다음 단계(max_tokens 200)와 비교한다"
fi

# 3. max_tokens --------------------------------------------------------------

title 3 "max_tokens 변경 — 응답 길이의 상한" "API 호출 2회"
say "같은 PR 을 --max-tokens 200 으로 다시 만듭니다." \
    "max_tokens 는 목표 길이가 아니라 상한이라, 모델이 짧게 줄여 쓰지 않고 쓰던 도중에 잘립니다."
if ask "pr --max-tokens 200 실행"; then
  gitgen "$OUT/pr-200.txt" pr --base develop --max-tokens 200
  check "[INFO] 응답 수신 … stop_reason=max_tokens (2단계는 end_turn)" \
        "잘린 응답은 재생성하지 않는다 — 다시 불러도 또 잘리므로 (호출 1회로 끝)" \
        "빠진 섹션에 '- (직접 작성 필요)' 자리표시자 + [WARN] 으로 알린다"
fi

say "" "범위 밖의 값: 0 은 도구가 호출 전에 막고, 999999 는 API 가 400 으로 거절합니다."
if ask "--max-tokens 0 / 999999 실행"; then
  gitgen /dev/null pr --base develop --max-tokens 0
  gitgen /dev/null pr --base develop --max-tokens 999999
  check "0 → argparse 오류, API 호출 없이 종료 코드 2" \
        "999999 → [ERROR] HTTP 400 + API 가 돌려준 원인(출력 상한) + 조치 안내, 종료 코드 1" \
        "자동 재시도하지 않는다"
fi

# 4. temperature -------------------------------------------------------------

title 4 "temperature 변경 — 같은 입력을 두 번씩" "API 호출 4회"
write_remove
git add -A
say "todo.py 에 remove() 와 테스트를 추가해 스테이징했습니다. 이 입력 그대로 temperature 만 바꿉니다."
show git diff --cached --stat
if ask "commit --temperature 0 을 두 번 실행"; then
  gitgen "$OUT/t0-1.txt" commit --temperature 0
  gitgen "$OUT/t0-2.txt" commit --temperature 0
  compare "$OUT/t0-1.txt" "$OUT/t0-2.txt"
  check "0 은 가장 확률 높은 토큰만 골라 같은 입력이면 거의 같은 결과가 나온다" \
        "다만 0 에서도 완전히 결정적이지는 않다 (공식 문서) — 다르게 나올 수도 있다"
fi
if ask "commit --temperature 1.0 을 두 번 실행"; then
  gitgen "$OUT/t1-1.txt" commit --temperature 1.0
  gitgen "$OUT/t1-2.txt" commit --temperature 1.0
  compare "$OUT/t1-1.txt" "$OUT/t1-2.txt"
  check "1.0 은 낮은 확률의 토큰도 뽑혀 같은 입력이어도 제목·불릿 표현이 달라지기 쉽다" \
        "형식(제목 1줄·불릿)은 그대로인가 — 형식은 temperature 가 아니라 프롬프트와 검증기가 지킨다" \
        "근거 없는 일반론('안정성 향상' 등)이 섞였는가 — 기본값을 0.2 로 둔 이유"
fi

printf '\n%s데모 끝.%s\n' "$BOLD" "$RESET"
