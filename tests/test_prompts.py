import dataclasses
import unittest

from gitgen.context import ChangeContext, FileChange
from gitgen.prompts import (
    COMMIT_TYPE_GUIDE, CONTEXT_GUIDE, EMPTY, LANGUAGE_RULE, TYPE_LINES, build_commit_prompt,
    build_pr_prompt, build_retry_message, format_context,
)
from gitgen.rules import COMMIT_TYPES, PR_SECTIONS

DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-print(1)\n+print(2)\n"


def context(**overrides):
    values = dict(mode="commit", source="staged", branch="main", base=None,
                  files=[FileChange("M", "a.py")], untracked=[], diff=DIFF)
    values.update(overrides)
    return ChangeContext(**values)


class FormatContextTest(unittest.TestCase):
    def test_every_context_field_is_a_key_and_is_explained(self):
        text = format_context(context())
        for field in dataclasses.fields(ChangeContext):
            with self.subTest(field=field.name):
                self.assertIn(f"\n{field.name}:", "\n" + text)
                self.assertIn(f"- {field.name}:", CONTEXT_GUIDE)

    def test_empty_values_are_written_as_none_marker(self):
        text = format_context(context())
        for line in ("base: (없음)", "untracked: (없음)", "hint: (없음)", "omitted_files: 0"):
            with self.subTest(line=line):
                self.assertIn(line, text)
        self.assertIn(f"files: {EMPTY}", format_context(context(files=[])))

    def test_lists_are_one_item_per_line(self):
        text = format_context(context(
            files=[FileChange("M", "a.py"), FileChange("R", "new.py", "old.py")],
            untracked=["memo.txt"], hint="값 수정", branch=None))
        self.assertIn("files:\n  M a.py\n  R new.py (← old.py)\n", text)
        self.assertIn("untracked:\n  memo.txt\n", text)
        self.assertIn("hint: 값 수정", text)
        self.assertIn("branch: (없음)", text)

    def test_diff_keeps_real_newlines_inside_tags(self):
        text = format_context(context())
        self.assertIn("diff:\n<diff>\n" + DIFF.rstrip("\n") + "\n</diff>", text)
        self.assertNotIn("\\n", text)            # repr 처럼 줄바꿈이 두 글자로 바뀌지 않는다


class CommitPromptTest(unittest.TestCase):
    def test_system_states_output_contract_and_user_carries_the_values(self):
        prompt = build_commit_prompt(context(hint="값 수정"))
        self.assertIn("형식의 커밋 제목 1줄 (72자 이하", prompt.system)
        self.assertIn("코드펜스", prompt.system)
        self.assertIn(CONTEXT_GUIDE, prompt.system)
        self.assertTrue(prompt.user.startswith("mode: commit\n"))
        self.assertIn("hint: 값 수정", prompt.user)
        self.assertTrue(prompt.user.endswith("커밋 메시지를 써 줘."))

    def test_convention_korean_and_conventional_commit_types(self):
        system = build_commit_prompt(context()).system
        self.assertIn("한국어로 쓴다", system)
        self.assertEqual(tuple(COMMIT_TYPE_GUIDE), COMMIT_TYPES)     # 설명과 검증 규칙이 같은 목록
        for kind in COMMIT_TYPES:
            with self.subTest(kind=kind):
                self.assertIn(f"  - {kind}: ", system)


class PrPromptTest(unittest.TestCase):
    def pr_context(self, **overrides):
        values = dict(mode="pr", source="branch", branch="feature/x", base="develop")
        values.update(overrides)
        return context(**values)

    def test_system_states_output_contract_and_user_carries_the_values(self):
        prompt = build_pr_prompt(self.pr_context(hint="게이트웨이 사용"))
        self.assertIn("형식의 PR 제목 1줄 (80자 이하)", prompt.system)
        self.assertIn("'## " + "', '## ".join(PR_SECTIONS) + "'", prompt.system)   # 검증기와 같은 섹션·순서
        self.assertIn("코드펜스", prompt.system)
        self.assertIn(CONTEXT_GUIDE, prompt.system)
        self.assertTrue(prompt.user.startswith("mode: pr\nsource: branch\nbranch: feature/x\nbase: develop\n"))
        self.assertIn("hint: 게이트웨이 사용", prompt.user)
        self.assertTrue(prompt.user.endswith("PR 제목과 본문을 써 줘."))

    def test_shares_language_and_type_rules_with_commit_prompt(self):
        commit, pr = build_commit_prompt(context()).system, build_pr_prompt(self.pr_context()).system
        for piece in (LANGUAGE_RULE, *TYPE_LINES):
            with self.subTest(piece=piece):
                self.assertIn(piece, commit)
                self.assertIn(piece, pr)

    def test_grounding_rules_for_why_and_how_to_test(self):
        system = build_pr_prompt(self.pr_context()).system
        self.assertIn("입력에 없는 사실·이유·명령을 지어내지 않는다", system)
        self.assertIn("hint 가 있으면 그것을 배경으로 먼저", system)
        self.assertIn("명령·도구 이름은 입력에 실제로 나오는 것만", system)
        self.assertIn("바뀐 동작 기준으로", system)


class RetryMessageTest(unittest.TestCase):
    VIOLATIONS = ["커밋 제목이 86자입니다. 72자 이하로 줄이세요.",
                  "'## How to Test' 섹션이 없습니다. 이 헤더를 그대로 쓰고 아래에 '- ' 불릿을 쓰세요."]

    def test_violations_are_listed_as_bullets_in_order(self):
        message = build_retry_message(self.VIOLATIONS)
        self.assertIn("\n".join(f"- {v}" for v in self.VIOLATIONS), message)

    def test_asks_to_fix_only_violations_and_output_whole_answer_without_apology(self):
        message = build_retry_message(self.VIOLATIONS[:1])
        self.assertIn("나머지는 방금 쓴 답 그대로", message)
        self.assertIn("전체를 처음부터 끝까지 다시 출력", message)
        self.assertIn("사과나 설명 없이", message)

    def test_fixed_wording_is_shared_by_commit_and_pr_and_does_not_repeat_format_rules(self):
        fixed = build_retry_message([])                  # 위반 문장을 뺀 고정 문구만
        for word in ("커밋", "PR", "[출력 형식]"):
            with self.subTest(word=word):
                self.assertNotIn(word, fixed)


if __name__ == "__main__":
    unittest.main()
