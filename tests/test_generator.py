import unittest

from gitgen.ai_client import AIError, CallBudget, Completion
from gitgen.context import ChangeContext, FileChange, Prompt
from gitgen.generator import generate
from gitgen.rules import COMMIT, PR

CTX = ChangeContext(mode="commit", source="staged", branch="main", base=None,
                    files=[FileChange("M", "a.py")], untracked=[], diff="+x\n")
PROMPT = Prompt(system="SYS", user="USER")
GOOD = "Feat: 추가\n\n- a.py 수정"
BAD = "Feat: " + "가" * 80                  # 제목 초과 → 재생성 사유


def done(text, stop_reason="end_turn", tokens=(100, 10)):
    return Completion(text, stop_reason, *tokens)


class FakeClient:
    """정해 둔 결과를 차례로 돌려준다. AIError 를 넣으면 그 차례에 던진다."""

    model = "fake-model"

    def __init__(self, *results, limit=2):
        self.results = list(results)
        self.budget = CallBudget(limit)
        self.calls = []

    def complete(self, system, messages):
        self.budget.take()
        self.calls.append((system, [dict(m) for m in messages]))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def retry_message(violations):
    return "고쳐 주세요:\n" + "\n".join(violations)


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.logs = []

    def run_generate(self, client, spec=COMMIT):
        return generate(CTX, client, spec, PROMPT, retry_message,
                        lambda level, message: self.logs.append((level, message)))

    def test_valid_first_answer_uses_one_call(self):
        client = FakeClient(done(GOOD))
        result = self.run_generate(client)
        self.assertEqual((result.calls, result.regenerated, result.warnings), (1, False, []))
        self.assertEqual(result.draft.text(), GOOD)
        self.assertEqual(client.calls[0], ("SYS", [{"role": "user", "content": "USER"}]))

    def test_violation_triggers_one_regeneration_with_feedback(self):
        client = FakeClient(done(BAD), done(GOOD, tokens=(150, 12)))

        result = self.run_generate(client)

        self.assertEqual((result.calls, result.regenerated), (2, True))
        self.assertEqual(result.draft.text(), GOOD)
        self.assertEqual((result.input_tokens, result.output_tokens), (250, 22))
        _, messages = client.calls[1]
        self.assertEqual(messages[1], {"role": "assistant", "content": BAD})
        self.assertTrue(messages[2]["content"].startswith("고쳐 주세요:\n커밋 제목이 86자입니다"))
        self.assertIn(("INFO", "AI API 요청 중... (호출 2/2, 모델 fake-model)"), self.logs)

    def test_still_violating_after_regeneration_falls_back_to_last_resort(self):
        client = FakeClient(done(BAD), done(BAD))

        result = self.run_generate(client)

        self.assertEqual(result.calls, 2)
        self.assertLessEqual(len(result.draft.title), 72)
        self.assertIn("잘랐습니다", result.warnings[0])

    def test_truncated_answer_is_not_regenerated(self):
        client = FakeClient(done("## Why\n- 이유", stop_reason="max_tokens"))

        result = self.run_generate(client, PR)

        self.assertEqual(result.calls, 1)
        self.assertTrue(any("max_tokens 에서 끊겨 재생성하지 않습니다" in m for _, m in self.logs))
        self.assertTrue(any("--max-tokens 를 늘려" in w for w in result.warnings))
        self.assertEqual([s.header for s in result.draft.sections], ["Why", "What", "How to Test"])

    def test_no_budget_left_means_no_regeneration(self):
        result = self.run_generate(FakeClient(done(BAD), limit=1))
        self.assertEqual(result.calls, 1)
        self.assertTrue(any("호출 한도를 다 써서" in m for _, m in self.logs))

    def test_failed_regeneration_keeps_first_answer(self):
        client = FakeClient(done(BAD), AIError("overloaded", "API 과부하"))

        result = self.run_generate(client)

        self.assertEqual((result.calls, result.regenerated), (1, False))
        self.assertTrue(any(level == "WARN" and "재생성 호출이 실패" in m for level, m in self.logs))
        self.assertLessEqual(len(result.draft.title), 72)

    def test_worse_regeneration_is_discarded(self):
        worse = "## What\n변경"                  # 제목·Why·What 불릿·How to Test 모두 위반
        first = "Feat: 추가\n\n## Why\n- 이유\n\n## What\n- 변경"   # How to Test 만 없음
        client = FakeClient(done(first), done(worse))

        result = self.run_generate(client, PR)

        self.assertFalse(result.regenerated)
        self.assertEqual(result.draft.title, "Feat: 추가")
        self.assertTrue(any("더 많이 어겨" in m for _, m in self.logs))

    def test_first_call_error_propagates(self):
        with self.assertRaises(AIError):
            self.run_generate(FakeClient(AIError("auth", "키 오류")))


if __name__ == "__main__":
    unittest.main()
