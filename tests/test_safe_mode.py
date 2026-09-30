import unittest
from collections import Counter

from gitgen.context import ChangeContext, FileChange
from gitgen.safe_mode import (
    EXCLUDED_MARK, apply_safe_mode, chunk_path, is_sensitive, mask_text, split_diff,
)
from helpers import fake_secret

ANTHROPIC_KEY = fake_secret("sk-" + "ant-", 30)
OPENAI_KEY = fake_secret("sk-", 30)
AWS_KEY_ID = fake_secret("AKIA", 16)
GITHUB_TOKEN = fake_secret("ghp_", 36)
GOOGLE_KEY = fake_secret("AIza", 35)
JWT = ".".join(fake_secret("eyJ", 12) if i == 0 else fake_secret("", 12) for i in range(3))


def mask(text):
    counts = Counter()
    return mask_text(text, counts), counts


def file_diff(path, added):
    """수정된 파일 하나의 diff — 머리 5줄 + 추가 줄."""
    return "\n".join([
        f"diff --git a/{path} b/{path}",
        "index 1111111..2222222 100644",
        f"--- a/{path}",
        f"+++ b/{path}",
        f"@@ -0,0 +1,{len(added)} @@",
        *("+" + line for line in added),
    ]) + "\n"


def context(diff, hint=None):
    return ChangeContext(mode="commit", source="staged", branch="main", base=None,
                         files=[FileChange("M", "x")], untracked=[], diff=diff, hint=hint)


class MaskTextTest(unittest.TestCase):
    def test_each_kind_is_masked(self):
        cases = [
            (f"key = {ANTHROPIC_KEY}", "API_KEY", ANTHROPIC_KEY),
            (f"key = {OPENAI_KEY}", "API_KEY", OPENAI_KEY),
            (f"aws {AWS_KEY_ID}", "API_KEY", AWS_KEY_ID),
            (f"gh {GITHUB_TOKEN}", "API_KEY", GITHUB_TOKEN),
            (f"google {GOOGLE_KEY}", "API_KEY", GOOGLE_KEY),
            (f"jwt {JWT}", "JWT", JWT),
            ("문의: someone@example.com", "EMAIL", "someone@example.com"),
            ("연락처 010-1234-5678", "PHONE", "010-1234-5678"),
            ("주민번호 900101-1234567", "RRN", "900101-1234567"),
        ]
        for text, kind, secret in cases:
            with self.subTest(kind=kind, text=text):
                masked, counts = mask(text)
                self.assertNotIn(secret, masked)
                self.assertIn(f"[MASKED:{kind}]", masked)
                self.assertEqual(counts, Counter({kind: 1}))

    def test_assignment_keeps_name_and_masks_value(self):
        value = fake_secret("", 16) + "9"
        masked, counts = mask(f"+DB_PASSWORD={value}")
        self.assertEqual(masked, "+DB_PASSWORD=[MASKED:SECRET]")
        masked, _ = mask(f'api_secret: "{value}"')
        self.assertEqual(masked, 'api_secret: "[MASKED:SECRET]"')

    def test_identifiers_that_look_like_secret_names_are_left_alone(self):
        for text in ('API_KEY_ENV = "AI_API_KEY"', "token = get_token()", "password: str"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), (text, Counter()))

    def test_bearer_keeps_scheme(self):
        masked, _ = mask("Authorization: Bearer " + fake_secret("", 24))
        self.assertEqual(masked, "Authorization: Bearer [MASKED:TOKEN]")

    def test_private_key_block_is_masked_as_one(self):
        block = "\n".join(["+-----BEGIN RSA PRIVATE KEY-----",
                           *("+" + fake_secret("", 40) for _ in range(3)),
                           "+-----END RSA PRIVATE KEY-----",
                           "+after"])
        masked, counts = mask(block)
        self.assertEqual(masked, "+[MASKED:PRIVATE_KEY]\n+after")
        self.assertEqual(counts, Counter({"PRIVATE_KEY": 1}))

    def test_private_key_without_end_is_masked_to_the_end(self):
        masked, _ = mask("+-----BEGIN PRIVATE KEY-----\n+" + fake_secret("", 40))
        self.assertEqual(masked, "+[MASKED:PRIVATE_KEY]")

    def test_numbers_and_words_that_only_resemble_secrets(self):
        for text in ("ts = 1727601234567",                 # 13자리 타임스탬프
                     "risk-assessment-framework-version-two",
                     "@property"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), (text, Counter()))


class SplitDiffTest(unittest.TestCase):
    def test_paths_are_read_from_each_chunk(self):
        diff = (file_diff("a.py", ["x"])
                + "diff --git a/old.txt b/new.txt\nsimilarity index 100%\n"
                  "rename from old.txt\nrename to new.txt\n"
                + "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n"
                  "--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
                + "diff --git a/img.png b/img.png\nBinary files a/img.png and b/img.png differ\n")
        _, chunks = split_diff(diff)
        self.assertEqual([c.path for c in chunks], ["a.py", "new.txt", "gone.py", "img.png"])

    def test_path_with_space_has_trailing_tab_removed(self):
        lines = ["diff --git a/내 문서.md b/내 문서.md", "--- a/내 문서.md\t", "+++ b/내 문서.md\t"]
        self.assertEqual(chunk_path(lines), "내 문서.md")

    def test_added_line_that_looks_like_header_is_not_a_path(self):
        lines = ["diff --git a/a.py b/a.py", "--- a/a.py", "+++ b/a.py", "@@ -1 +1 @@", "+++ b/evil"]
        self.assertEqual(chunk_path(lines), "a.py")


class SensitiveFileTest(unittest.TestCase):
    def test_patterns(self):
        for path in (".env", "config/.env.production", "certs/server.PEM", "keys/id_rsa.pub"):
            with self.subTest(path=path):
                self.assertTrue(is_sensitive(path))
        for path in ("environment.py", "docs/key-points.md", "src/pem_utils.py"):
            with self.subTest(path=path):
                self.assertFalse(is_sensitive(path))


class ApplySafeModeTest(unittest.TestCase):
    def test_sensitive_file_content_is_excluded(self):
        diff = file_diff(".env", ["LOCAL_ONLY_VALUE=abc"]) + file_diff("a.py", ["print(1)"])

        limited, report = apply_safe_mode(context(diff))

        self.assertNotIn("LOCAL_ONLY_VALUE", limited.diff)
        self.assertIn("diff --git a/.env b/.env\n" + EXCLUDED_MARK, limited.diff)
        self.assertIn("+print(1)", limited.diff)
        self.assertEqual(report.excluded, [".env"])

    def test_file_limit_keeps_first_files_and_notes_the_rest(self):
        diff = "".join(file_diff(f"f{i}.py", ["x"]) for i in range(12))

        limited, report = apply_safe_mode(context(diff))

        self.assertIn("f9.py", limited.diff)
        self.assertNotIn("f10.py", limited.diff)
        self.assertEqual((limited.omitted_files, limited.omitted_lines), (2, 12))
        self.assertIn("파일 2개의 내용을 생략", limited.diff)
        self.assertEqual((report.sent_files, report.total_files), (10, 12))
        self.assertEqual(limited.files, [FileChange("M", "x")])    # 파일 목록은 그대로

    def test_line_limit_cuts_inside_a_file(self):
        diff = file_diff("big.txt", [f"line {i}" for i in range(300)])

        limited, report = apply_safe_mode(context(diff))

        self.assertEqual(report.sent_lines, 200)
        self.assertEqual(report.total_lines, 305)
        self.assertEqual(limited.omitted_lines, 105)
        self.assertIn("[safe-mode: 이 파일의 나머지 105줄 생략]", limited.diff)
        self.assertNotIn("+line 195", limited.diff)

    def test_key_block_across_the_line_limit_does_not_leak(self):
        body = [fake_secret("", 40) for _ in range(20)]
        added = [f"filler {i}" for i in range(190)] + [
            "-----BEGIN PRIVATE KEY-----", *body, "-----END PRIVATE KEY-----"]

        limited, report = apply_safe_mode(context(file_diff("notes.md", added)))

        for line in body:
            self.assertNotIn(line, limited.diff)
        self.assertEqual(report.masked, Counter({"PRIVATE_KEY": 1}))

    def test_hint_is_masked_too(self):
        limited, report = apply_safe_mode(context(file_diff("a.py", ["x"]), hint="담당 me@example.com"))
        self.assertEqual(limited.hint, "담당 [MASKED:EMAIL]")
        self.assertEqual(report.masked["EMAIL"], 1)

    def test_summary(self):
        diff = file_diff(".env", ["A=1"]) + file_diff("a.py", [ANTHROPIC_KEY, "a@example.com", ANTHROPIC_KEY])
        _, report = apply_safe_mode(context(diff))
        self.assertEqual(report.summary(),
                         "safe-mode: 마스킹 3건(API_KEY 2, EMAIL 1) · 민감 파일 1개 내용 제외 · "
                         "전송 2/2파일 · 10/14줄")

    def test_summary_states_masked_count_is_before_the_limit(self):
        diff = "".join(file_diff(f"f{i:02}.py", ["x"]) for i in range(10)) \
            + file_diff("late.py", ["late@example.com"])
        _, report = apply_safe_mode(context(diff))
        self.assertIn("마스킹 1건(EMAIL 1, 전송 한도 적용 전 기준)", report.summary())

    def test_bearer_and_assignment_do_not_reach_across_lines(self):
        value = fake_secret("", 24) + "9"
        for text in (f"bearer\n{value}", f"password\n= {value}"):
            with self.subTest(text=text):
                self.assertEqual(mask(text), (text, Counter()))


if __name__ == "__main__":
    unittest.main()
