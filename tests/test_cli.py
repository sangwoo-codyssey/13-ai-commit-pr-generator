import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from fake_api import FakeApiServer, error_body, message_body
from gitgen.ai_client import API_URL
from gitgen.cli import build_parser, main, resolve_api_url
from helpers import GitRepoTestCase, fake_secret, patch_prompts

# 키 모양이 아닌, 누가 봐도 가짜인 값. 요청은 127.0.0.1 가짜 서버로만 간다.
FAKE_ENV = {"AI_API_KEY": "not-a-real-key"}

GOOD_COMMIT = "Feat: 출력 값 변경\n\n- a.py 의 출력 값을 10으로 바꿈"
GOOD_PR = "Feat: 출력 값 변경\n\n## Why\n- 값이 틀렸다\n\n## What\n- a.py 수정\n\n## How to Test\n- 실행"


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


class ResolveApiUrlTest(unittest.TestCase):
    def test_default_when_unset_or_blank(self):
        for env in ({}, {"AI_API_BASE_URL": ""}, {"AI_API_BASE_URL": "   "}):
            with self.subTest(env=env):
                self.assertEqual(resolve_api_url(env), API_URL)

    def test_messages_path_is_appended_to_the_base(self):
        cases = {
            "https://gateway.example.com": "https://gateway.example.com/v1/messages",
            "https://gateway.example.com/": "https://gateway.example.com/v1/messages",
            "https://gateway.example.com/anthropic": "https://gateway.example.com/anthropic/v1/messages",
            "http://localhost:8080": "http://localhost:8080/v1/messages",
            "http://127.0.0.1:9000": "http://127.0.0.1:9000/v1/messages",
            "http://[::1]:9000": "http://[::1]:9000/v1/messages",
        }
        for base, expected in cases.items():
            with self.subTest(base=base):
                self.assertEqual(resolve_api_url({"AI_API_BASE_URL": base}), expected)

    def test_key_must_not_travel_in_plain_text(self):
        for base in ("http://gateway.example.com", "ftp://example.com", "api.anthropic.com", "https://"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                resolve_api_url({"AI_API_BASE_URL": base})

    def test_full_endpoint_in_base_is_refused_with_a_hint(self):
        for base in ("https://gateway.example.com/v1/messages", "https://gateway.example.com/v1/messages/",
                     "https://gateway.example.com?x=1"):
            with self.subTest(base=base), self.assertRaises(ValueError) as caught:
                resolve_api_url({"AI_API_BASE_URL": base})
            self.assertIn("도메인까지만", str(caught.exception))


class CliTestCase(GitRepoTestCase):
    def setUp(self):
        super().setUp()
        self.repo.write("a.py", "print(1)\n")
        self.repo.commit_all("init")

    def run_cli(self, *argv, env=None, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv), cwd=cwd or self.cwd, env={} if env is None else env)
        return code, out.getvalue(), err.getvalue()


class PreconditionAndDryRunTest(CliTestCase):
    def setUp(self):
        super().setUp()
        patch_prompts(self)

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

    def test_dry_run_needs_no_key_and_prints_the_prompt(self):
        self.repo.write("a.py", "print(10)\n")
        self.repo.git("add", "a.py")

        code, out, err = self.run_cli("commit", "--dry-run", "--hint", "값 수정",
                                      "--temperature", "none")

        self.assertEqual(code, 0)
        self.assertIn("[INFO] Git status 수집 완료: 1개 파일 변경 감지", err)
        self.assertIn("DRY RUN", out)
        self.assertIn("model claude-haiku-4-5 · temperature 보내지 않음 · max_tokens 1024", out)
        self.assertIn("ECHO SYSTEM", out)
        self.assertIn("M  a.py", out)
        self.assertIn("hint: 값 수정", out)
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

    def test_safe_mode_is_on_by_default(self):
        key = fake_secret("sk-" + "ant-", 30)
        self.repo.write("a.py", f'KEY = "{key}"\n')

        code, out, err = self.run_cli("commit", "--dry-run")

        self.assertEqual(code, 0)
        self.assertNotIn(key, out)
        self.assertIn('+KEY = "[MASKED:API_KEY]"', out)
        self.assertIn("[INFO] safe-mode: 마스킹 1건(API_KEY 1)", err)

    def test_no_safe_mode_warns_and_sends_as_is(self):
        key = fake_secret("sk-" + "ant-", 30)
        self.repo.write("a.py", f'KEY = "{key}"\n')

        code, out, err = self.run_cli("commit", "--dry-run", "--no-safe-mode")

        self.assertEqual(code, 0)
        self.assertIn(key, out)
        self.assertIn("[WARN] safe-mode 꺼짐", err)

    def test_limit_keeps_all_file_names_in_context(self):
        for i in range(12):
            self.repo.write(f"f{i:02}.py", "x\n")
        self.repo.git("add", "-A")

        code, out, err = self.run_cli("commit", "--dry-run")

        self.assertEqual(code, 0)
        self.assertIn("전송 10/12파일", err)
        self.assertIn("omitted_files=2", out)
        self.assertIn("A  f11.py", out)          # 이름 목록은 전부 남는다

    def test_git_failure_is_reported_as_error(self):
        code, _, err = self.run_cli("pr", "--dry-run", "--base", "nope")
        self.assertEqual(code, 1)
        self.assertIn("[ERROR] git diff", err)

    def test_default_endpoint_is_shown_only_in_dry_run(self):
        self.repo.write("a.py", "print(10)\n")
        code, out, err = self.run_cli("commit", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(f"POST {API_URL}", out)
        self.assertNotIn("API 엔드포인트", err)

    def test_custom_endpoint_is_announced(self):
        self.repo.write("a.py", "print(10)\n")
        env = {"AI_API_BASE_URL": "https://gateway.example.com"}
        code, out, err = self.run_cli("commit", "--dry-run", env=env)
        self.assertEqual(code, 0)
        self.assertIn("[INFO] API 엔드포인트: https://gateway.example.com/v1/messages (AI_API_BASE_URL)", err)
        self.assertIn("POST https://gateway.example.com/v1/messages", out)

    def test_plain_http_endpoint_is_refused_before_anything_runs(self):
        self.repo.write("a.py", "print(10)\n")
        env = {**FAKE_ENV, "AI_API_BASE_URL": "http://gateway.example.com"}
        code, out, err = self.run_cli("commit", env=env)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("[ERROR] AI_API_BASE_URL 는 https 주소여야 합니다", err)
        self.assertNotIn("Git status 수집", err)


class PromptNotWrittenYetTest(CliTestCase):
    def test_missing_prompt_is_reported_before_any_call(self):
        self.repo.write("a.py", "print(10)\n")
        code, out, err = self.run_cli("commit", env=FAKE_ENV)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("[ERROR] 커밋 프롬프트가 아직 작성되지 않았습니다", err)


class EndToEndTest(CliTestCase):
    """수집 → safe-mode → 프롬프트 → 호출(가짜 서버) → 검증 → 출력."""

    def setUp(self):
        super().setUp()
        patch_prompts(self)
        self.server = FakeApiServer()
        self.addCleanup(self.server.close)
        self.env = {**FAKE_ENV, "AI_API_BASE_URL": self.server.base_url}   # http://127.0.0.1:포트
        self.repo.write("a.py", "print(10)\n")

    def test_commit_message_in_one_call(self):
        self.server.reply(body=message_body(GOOD_COMMIT, input_tokens=1200, output_tokens=40))

        code, out, err = self.run_cli("commit", env=self.env)

        self.assertEqual(code, 0)
        self.assertEqual(out, "--- Commit Message ---\n" + GOOD_COMMIT + "\n" + "-" * 22 + "\n")
        self.assertIn("[INFO] AI API 요청 중... (호출 1/2, 모델 claude-haiku-4-5)", err)
        self.assertIn("[DONE] 커밋 메시지 생성 완료 (API 호출 1회 · 입력 1,200 / 출력 40 토큰)", err)
        self.assertNotIn("[WARN]", err)
        self.assertEqual(len(self.server.received), 1)

    def test_pr_regenerates_once_when_a_section_is_missing(self):
        self.repo.git("branch", "develop")
        self.repo.git("switch", "-q", "-c", "feature/x")
        self.repo.commit_all("change")
        self.server.reply(body=message_body("Feat: 출력 값 변경\n\n## Why\n- 값이 틀렸다\n\n## What\n- a.py 수정"))
        self.server.reply(body=message_body(GOOD_PR))

        code, out, err = self.run_cli("pr", env=self.env)

        self.assertEqual(code, 0)
        self.assertIn("출력 규칙 위반 1건 → 1회 재생성합니다", err)
        self.assertIn("## How to Test\n- 실행", out)
        self.assertIn("[DONE] PR 초안 생성 완료 (API 호출 2회", err)
        retry_messages = self.server.received[1].json()["messages"]
        self.assertEqual([m["role"] for m in retry_messages], ["user", "assistant", "user"])
        self.assertIn("'## How to Test' 섹션이 없습니다", retry_messages[2]["content"])

    def test_api_error_is_reported_with_cause(self):
        self.server.reply(401, error_body("authentication_error", "invalid x-api-key"))

        code, out, err = self.run_cli("commit", env=self.env)

        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("[ERROR] API 요청 실패 (HTTP 401 authentication_error): invalid x-api-key", err)
        self.assertNotIn("not-a-real-key", err)

    def test_dry_run_never_calls_the_api(self):
        code, _, _ = self.run_cli("commit", "--dry-run", env=self.env)
        self.assertEqual(code, 0)
        self.assertEqual(self.server.received, [])


if __name__ == "__main__":
    unittest.main()
