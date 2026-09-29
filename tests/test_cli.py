import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from gitgen.cli import build_parser, main
from helpers import GitRepoTestCase

# 키 모양이 아닌, 누가 봐도 가짜인 값. API 는 이 단계에서 호출되지 않는다.
FAKE_ENV = {"AI_API_KEY": "not-a-real-key"}


class ParserTest(unittest.TestCase):
    def parse(self, *argv):
        with redirect_stderr(io.StringIO()):
            return build_parser().parse_args(list(argv))

    def test_defaults(self):
        args = self.parse("commit")
        self.assertEqual(args.model, "claude-haiku-4-5")
        self.assertEqual(args.temperature, 0.2)
        self.assertEqual(args.max_tokens, 1024)
        self.assertTrue(args.safe_mode)
        self.assertFalse(args.dry_run)
        self.assertIsNone(args.hint)

    def test_options_after_subcommand(self):
        args = self.parse("pr", "--model", "claude-sonnet-4-6", "--temperature", "0.7",
                          "--max-tokens", "300", "--no-safe-mode", "--base", "main")
        self.assertEqual((args.model, args.temperature, args.max_tokens, args.safe_mode, args.base),
                         ("claude-sonnet-4-6", 0.7, 300, False, "main"))

    def test_temperature_none_means_do_not_send(self):
        self.assertIsNone(self.parse("commit", "--temperature", "none").temperature)

    def test_invalid_values_are_usage_errors(self):
        for argv in (["commit", "--temperature", "1.5"],
                     ["commit", "--temperature", "hot"],
                     ["commit", "--max-tokens", "0"],
                     ["commit", "--base", "main"],      # --base 는 pr 전용
                     []):                               # 서브커맨드 필수
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                self.parse(*argv)
            self.assertEqual(caught.exception.code, 2)


class CliRunTest(GitRepoTestCase):
    def setUp(self):
        super().setUp()
        self.repo.write("a.py", "print(1)\n")
        self.repo.commit_all("init")

    def run_cli(self, *argv, env=None, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv), cwd=cwd or self.cwd, env={} if env is None else env)
        return code, out.getvalue(), err.getvalue()

    def test_must_run_at_repository_root(self):
        (self.repo.path / "sub").mkdir()
        code, _, err = self.run_cli("commit", "--dry-run", cwd=str(self.repo.path / "sub"))
        self.assertEqual(code, 1)
        self.assertIn("루트 디렉토리에서 실행하세요", err)

    def test_missing_api_key_explains_how_to_set_it(self):
        self.repo.write("a.py", "print(10)\n")
        code, out, err = self.run_cli("commit")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("[ERROR] AI_API_KEY 환경변수가 설정되지 않았습니다.\n"
                      '        예) export AI_API_KEY="YOUR_KEY"', err)

    def test_blank_api_key_counts_as_missing(self):
        code, _, err = self.run_cli("commit", env={"AI_API_KEY": "   "})
        self.assertEqual(code, 1)
        self.assertIn("AI_API_KEY", err)

    def test_no_changes_is_a_normal_exit(self):
        code, out, err = self.run_cli("commit", env=FAKE_ENV)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("[INFO] 변경 사항이 없습니다", err)

    def test_dry_run_needs_no_key_and_prints_to_stdout(self):
        self.repo.write("a.py", "print(10)\n")
        self.repo.git("add", "a.py")

        code, out, err = self.run_cli("commit", "--dry-run", "--hint", "값 수정")

        self.assertEqual(code, 0)
        self.assertIn("[INFO] Git status 수집 완료: 1개 파일 변경 감지", err)
        self.assertIn("[INFO] Git diff 수집 완료:", err)
        self.assertIn("DRY RUN", out)
        self.assertIn("M  a.py", out)
        self.assertIn("변경 이유(--hint): 값 수정", out)
        self.assertIn("+print(10)", out)
        self.assertNotIn("[INFO]", out)        # 로그와 결과가 섞이지 않는다

    def test_dry_run_pr_reports_branch(self):
        self.repo.git("branch", "develop")
        self.repo.git("switch", "-q", "-c", "feature/x")
        self.repo.write("a.py", "print(10)\n")
        self.repo.commit_all("change")

        code, _, err = self.run_cli("pr", "--dry-run")

        self.assertEqual(code, 0)
        self.assertIn("[INFO] 현재 브랜치: feature/x → 비교 기준: develop", err)

    def test_git_failure_is_reported_as_error(self):
        code, _, err = self.run_cli("pr", "--dry-run", "--base", "nope")
        self.assertEqual(code, 1)
        self.assertIn("[ERROR] git diff", err)


if __name__ == "__main__":
    unittest.main()
