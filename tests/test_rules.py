import unittest

from gitgen.context import ChangeContext, FileChange
from gitgen.rules import (
    PLACEHOLDER_BULLET, clean_title, finalize_commit, finalize_pr, normalize_type_prefix,
    parse_commit, parse_pr, tidy_lines, truncate_title, validate_commit, validate_pr,
)

CTX = ChangeContext(mode="commit", source="staged", branch="main", base=None,
                    files=[FileChange("M", "gitgen/cli.py")], untracked=["notes.md"], diff="")


class TidyTest(unittest.TestCase):
    def test_wrapping_fence_separators_and_ai_traces_are_removed(self):
        text = ("```text\n--- Commit Message ---\nFeat: 추가\n\n\n\n* 하나\n• 둘\n1. 셋\n"
                "Co-authored-by: Someone <a@b.c>\n🤖 Generated with a tool\n----------\n```")
        self.assertEqual(tidy_lines(text), ["Feat: 추가", "", "- 하나", "- 둘", "- 셋"])

    def test_code_block_inside_body_is_kept_untouched(self):
        text = "## How to Test\n- 실행:\n```bash\n* not a bullet\n./run.sh test\n```"
        self.assertEqual(tidy_lines(text), [
            "## How to Test", "- 실행:", "```bash", "* not a bullet", "./run.sh test", "```"])

    def test_title_cleanup(self):
        cases = {
            "# 제목: Feat: 추가.": "Feat: 추가",
            "**커밋 메시지:** Fix: 오류 수정": "Fix: 오류 수정",
            '"Docs: README 보완"': "Docs: README 보완",
            "`feat: x`.": "feat: x",
            "Title: Add parser": "Add parser",
            "- Chore: 정리": "Chore: 정리",
            "Refactor: 흐름 정리...": "Refactor: 흐름 정리...",       # 말줄임표는 둔다
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_title(raw), expected)

    def test_truncate_title_at_word_boundary(self):
        title = "Feat: 아주 긴 제목이 계속 이어져서 제한을 넘어가는 경우를 확인합니다"
        cut = truncate_title(title, 30)
        self.assertLessEqual(len(cut), 30)
        self.assertTrue(cut.endswith("…"))
        self.assertTrue(title.startswith(cut[:-1]))
        self.assertEqual(truncate_title("짧음", 30), "짧음")


class CommitRulesTest(unittest.TestCase):
    def test_parse_title_and_body(self):
        draft = parse_commit("커밋 메시지:\nFeat: 추가\n본문:\n- gitgen/cli.py 수정\n")
        self.assertEqual(draft.title, "feat: 추가")
        self.assertEqual(draft.body, ["- gitgen/cli.py 수정"])
        self.assertEqual(draft.text(), "feat: 추가\n\n- gitgen/cli.py 수정")

    def test_type_prefix_case_and_spacing_are_normalized(self):
        cases = {
            "Feat : 추가": "feat: 추가",
            "FIX: 오류 수정": "fix: 오류 수정",
            "refactor:구조 정리": "refactor: 구조 정리",
            "Docs(readme)： 설명 보강": "docs(readme): 설명 보강",
            "feat(api)!: 응답 형식 변경": "feat(api)!: 응답 형식 변경",
            "Update: 모르는 type 은 그대로": "Update: 모르는 type 은 그대로",
            "API base URL 설정으로 변경": "API base URL 설정으로 변경",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_type_prefix(raw), expected)

    def test_title_needs_a_known_type_prefix(self):
        for title in ("API base URL 설정으로 변경", "Update: 설정 변경", "feat 추가"):
            with self.subTest(title=title):
                errors = validate_commit(parse_commit(title), CTX).errors
                self.assertEqual(len(errors), 1)
                self.assertIn("'<type>: <요약>' 형식이 아닙니다", errors[0])
        for title in ("feat(cli): 옵션 추가", "revert: 이전 변경 되돌리기", "Chore: 스크립트 정리"):
            with self.subTest(title=title):
                self.assertEqual(validate_commit(parse_commit(title), CTX).errors, [])

    def test_finalize_does_not_invent_a_type(self):
        fixed, warnings = finalize_commit(parse_commit("API base URL 설정으로 변경"), CTX)
        self.assertEqual(fixed.title, "API base URL 설정으로 변경")
        self.assertEqual(len(warnings), 1)
        self.assertIn("type 접두어", warnings[0])

    def test_title_only_is_valid(self):
        findings = validate_commit(parse_commit("Fix: 오류 수정"), CTX)
        self.assertEqual((findings.errors, findings.warnings), ([], []))

    def test_title_length(self):
        long_title = "Feat: " + "가" * 60                       # 66자 — 권장 초과, 최대 이내
        findings = validate_commit(parse_commit(long_title), CTX)
        self.assertEqual(findings.errors, [])
        self.assertIn("66자", findings.warnings[0])

        too_long = "Feat: " + "가" * 70                        # 76자
        self.assertIn("72자 이하로", validate_commit(parse_commit(too_long), CTX).errors[0])

    def test_body_needs_bullet_or_file_name(self):
        plain = parse_commit("Feat: 추가\n\n설명만 있는 문단입니다.")
        self.assertIn("불릿", validate_commit(plain, CTX).errors[0])
        for body in ("cli.py 에서 옵션을 받는다.", "notes.md 를 추가했다.", "- 옵션 추가"):
            with self.subTest(body=body):
                self.assertEqual(validate_commit(parse_commit(f"Feat: 추가\n\n{body}"), CTX).errors, [])

    def test_finalize_truncates_and_bulletizes(self):
        draft = parse_commit("Feat: " + "가나 " * 30 + "\n\n설명 한 줄\n설명 두 줄")
        fixed, warnings = finalize_commit(draft, CTX)
        self.assertLessEqual(len(fixed.title), 72)
        self.assertEqual(fixed.body, ["- 설명 한 줄", "- 설명 두 줄"])
        self.assertEqual(len(warnings), 2)
        self.assertEqual(validate_commit(fixed, CTX).errors, [])

    def test_finalize_fills_empty_title(self):
        fixed, warnings = finalize_commit(parse_commit(""), CTX)
        self.assertEqual(fixed.title, "(커밋 제목 직접 작성 필요)")
        self.assertIn("자리표시자", warnings[0])


GOOD_PR = """Feat: 옵션 추가

## Why
- 이유

## What
- 변경

## How to Test
- 실행"""


class PrRulesTest(unittest.TestCase):
    def test_parse_good_pr(self):
        draft = parse_pr(GOOD_PR)
        self.assertEqual(draft.title, "Feat: 옵션 추가")
        self.assertEqual([s.header for s in draft.sections], ["Why", "What", "How to Test"])
        self.assertEqual(draft.text(), GOOD_PR)
        self.assertEqual(validate_pr(draft, CTX).errors, [])

    def test_header_variants_are_normalized_and_reordered(self):
        text = ("PR 제목: Fix: 수정\n\n**What**\n* 변경\n\n### how to test:\n1. 실행\n\n"
                "## Why (변경 배경)\n- 이유\n\n## Notes\n- 참고")
        draft = parse_pr(text)
        self.assertEqual(draft.title, "Fix: 수정")
        self.assertEqual([s.header for s in draft.sections], ["Why", "What", "How to Test", "Notes"])
        self.assertIn("## How to Test\n- 실행", draft.body_text)
        self.assertEqual(validate_pr(draft, CTX).errors, [])

    def test_missing_title_section_and_bullets(self):
        draft = parse_pr("## Why\n- 이유\n\n## What\n변경 설명만 있음")
        errors = validate_pr(draft, CTX).errors
        self.assertEqual(len(errors), 3)
        self.assertIn("PR 제목 줄이 비어", errors[0])
        self.assertIn("'## What' 섹션에 '- ' 불릿이 없습니다", errors[1])
        self.assertIn("'## How to Test' 섹션이 없습니다", errors[2])

    def test_pr_title_limit(self):
        draft = parse_pr("Feat: " + "가" * 80 + "\n\n" + GOOD_PR.split("\n\n", 1)[1])
        self.assertIn("80자 이하로", validate_pr(draft, CTX).errors[0])

    def test_finalize_adds_placeholders_in_order(self):
        draft = parse_pr("Feat: 추가\n\n## What\n변경 설명만 있음\n\n## Notes\n- 참고")
        fixed, warnings = finalize_pr(draft, CTX)
        self.assertEqual([s.header for s in fixed.sections], ["Why", "What", "How to Test", "Notes"])
        self.assertEqual(fixed.section("Why").lines, [PLACEHOLDER_BULLET])
        self.assertEqual(fixed.section("What").lines, ["- 변경 설명만 있음"])
        self.assertEqual(len(warnings), 3)
        self.assertEqual(validate_pr(fixed, CTX).errors, [])


if __name__ == "__main__":
    unittest.main()
