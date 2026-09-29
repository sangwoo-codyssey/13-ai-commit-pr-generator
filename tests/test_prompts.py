import dataclasses
import unittest

from gitgen.context import ChangeContext, FileChange
from gitgen.prompts import (
    COMMIT_TYPE_GUIDE, CONTEXT_GUIDE, EMPTY, build_commit_prompt, format_context,
)
from gitgen.rules import COMMIT_TYPES

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


if __name__ == "__main__":
    unittest.main()
