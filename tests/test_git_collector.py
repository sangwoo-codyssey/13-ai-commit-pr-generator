import unittest

from gitgen.context import FileChange
from gitgen.git_collector import (
    GitError, NoChanges, collect_commit, collect_pr, parse_name_status, parse_status,
)
from helpers import GitRepoTestCase


class ParseStatusTest(unittest.TestCase):
    def test_branch_with_upstream_and_ahead(self):
        self.assertEqual(parse_status("## main...origin/main [ahead 1]\0").branch, "main")

    def test_branch_without_upstream(self):
        self.assertEqual(parse_status("## feature/x\0").branch, "feature/x")

    def test_branch_before_first_commit(self):
        self.assertEqual(parse_status("## No commits yet on main\0").branch, "main")

    def test_detached_head_has_no_branch(self):
        self.assertIsNone(parse_status("## HEAD (no branch)\0").branch)

    def test_rename_consumes_next_token_as_original_path(self):
        status = parse_status("## main\0R  new.py\0old.py\0 M other.py\0?? notes.txt\0")
        self.assertEqual(status.staged(), [FileChange("R", "new.py", "old.py")])
        self.assertEqual(status.unstaged(), [FileChange("M", "other.py")])
        self.assertEqual(status.untracked, ["notes.txt"])

    def test_file_both_staged_and_modified_appears_in_both_lists(self):
        status = parse_status("## main\0MM a.py\0")
        self.assertEqual(status.staged(), [FileChange("M", "a.py")])
        self.assertEqual(status.unstaged(), [FileChange("M", "a.py")])

    def test_path_with_spaces_and_korean_is_kept_as_is(self):
        status = parse_status("## main\0 M 내 문서.md\0")
        self.assertEqual(status.unstaged(), [FileChange("M", "내 문서.md")])


class ParseNameStatusTest(unittest.TestCase):
    def test_rename_lists_original_then_new_path(self):
        files = parse_name_status("M\0a.py\0R100\0old.py\0new.py\0A\0b.py\0")
        self.assertEqual(files, [
            FileChange("M", "a.py"),
            FileChange("R", "new.py", "old.py"),
            FileChange("A", "b.py"),
        ])


class CollectCommitTest(GitRepoTestCase):
    def setUp(self):
        super().setUp()
        self.repo.write("a.py", "print(1)\n")
        self.repo.write("b.py", "print(2)\n")
        self.repo.commit_all("init")

    def test_clean_repo_has_no_changes(self):
        with self.assertRaisesRegex(NoChanges, "변경 사항이 없습니다"):
            collect_commit(self.cwd)

    def test_only_untracked_files_asks_to_stage_first(self):
        self.repo.write("new.txt", "hello\n")
        with self.assertRaisesRegex(NoChanges, "git add"):
            collect_commit(self.cwd)

    def test_staged_changes_win_over_unstaged(self):
        self.repo.write("a.py", "print(10)\n")
        self.repo.git("add", "a.py")
        self.repo.write("b.py", "print(20)\n")

        collected = collect_commit(self.cwd)

        ctx = collected.context
        self.assertEqual(ctx.source, "staged")
        self.assertEqual(ctx.files, [FileChange("M", "a.py")])
        self.assertIn("+print(10)", ctx.diff)
        self.assertNotIn("print(20)", ctx.diff)
        self.assertTrue(any("제외" in note for note in collected.notes))

    def test_falls_back_to_unstaged_when_nothing_staged(self):
        self.repo.write("a.py", "print(10)\n")

        collected = collect_commit(self.cwd)

        self.assertEqual(collected.context.source, "unstaged")
        self.assertIn("+print(10)", collected.context.diff)
        self.assertTrue(any("스테이징되지 않은" in note for note in collected.notes))

    def test_untracked_files_are_passed_by_name_only(self):
        self.repo.write("a.py", "print(10)\n")
        self.repo.git("add", "a.py")
        self.repo.write("memo.txt", "secret-looking content\n")

        ctx = collect_commit(self.cwd).context

        self.assertEqual(ctx.untracked, ["memo.txt"])
        self.assertNotIn("secret-looking", ctx.diff)

    def test_korean_path_is_not_escaped_in_diff(self):
        self.repo.write("문서.md", "첫 줄\n")
        self.repo.commit_all("add doc")
        self.repo.write("문서.md", "고친 줄\n")
        self.repo.git("add", "문서.md")

        ctx = collect_commit(self.cwd).context

        self.assertEqual(ctx.files, [FileChange("M", "문서.md")])
        self.assertIn("문서.md", ctx.diff)
        self.assertIn("+고친 줄", ctx.diff)

    def test_diff_prefixes_are_fixed_even_if_user_config_removes_them(self):
        self.repo.git("config", "diff.noprefix", "true")
        self.repo.write("a.py", "print(10)\n")

        diff = collect_commit(self.cwd).context.diff

        self.assertTrue(diff.startswith("diff --git a/a.py b/a.py\n"))

    def test_staged_rename_keeps_original_path(self):
        self.repo.git("mv", "a.py", "c.py")

        ctx = collect_commit(self.cwd).context

        self.assertEqual(ctx.files, [FileChange("R", "c.py", "a.py")])

    def test_branch_name_and_hint(self):
        self.repo.git("switch", "-q", "-c", "feature/x")
        self.repo.write("a.py", "print(10)\n")

        ctx = collect_commit(self.cwd, hint="출력 값을 바꿨다").context

        self.assertEqual(ctx.branch, "feature/x")
        self.assertEqual(ctx.hint, "출력 값을 바꿨다")
        self.assertEqual(ctx.mode, "commit")


class CollectPrTest(GitRepoTestCase):
    def setUp(self):
        super().setUp()
        self.repo.write("a.py", "print(1)\n")
        self.repo.commit_all("init")
        self.repo.git("branch", "develop")
        self.repo.git("switch", "-q", "-c", "feature/x")

    def test_compares_committed_changes_against_base(self):
        self.repo.write("a.py", "print(10)\n")
        self.repo.commit_all("change")

        collected = collect_pr(self.cwd, "develop")

        ctx = collected.context
        self.assertEqual((ctx.mode, ctx.source, ctx.base, ctx.branch),
                         ("pr", "branch", "develop", "feature/x"))
        self.assertEqual(ctx.files, [FileChange("M", "a.py")])
        self.assertIn("+print(10)", ctx.diff)
        self.assertEqual(collected.notes, [])

    def test_rename_in_branch_is_detected(self):
        self.repo.git("mv", "a.py", "b.py")
        self.repo.commit_all("rename")

        ctx = collect_pr(self.cwd, "develop").context

        self.assertEqual(ctx.files, [FileChange("R", "b.py", "a.py")])

    def test_uncommitted_changes_are_excluded_with_note(self):
        self.repo.write("a.py", "print(10)\n")
        self.repo.commit_all("change")
        self.repo.write("a.py", "print(99)\n")

        collected = collect_pr(self.cwd, "develop")

        self.assertNotIn("print(99)", collected.context.diff)
        self.assertTrue(any("포함되지 않습니다" in note for note in collected.notes))

    def test_running_on_base_branch_is_an_error(self):
        self.repo.git("switch", "-q", "develop")
        with self.assertRaisesRegex(GitError, "같습니다"):
            collect_pr(self.cwd, "develop")

    def test_missing_base_branch_reports_git_reason_and_hint(self):
        with self.assertRaises(GitError) as caught:
            collect_pr(self.cwd, "nope")
        message = str(caught.exception)
        self.assertIn("nope", message)
        self.assertIn("--base", message)

    def test_no_commits_since_base(self):
        with self.assertRaisesRegex(NoChanges, "develop 대비"):
            collect_pr(self.cwd, "develop")


if __name__ == "__main__":
    unittest.main()
