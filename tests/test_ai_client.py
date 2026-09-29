import unittest

from fake_api import FakeApiServer, closed_port_url, error_body, message_body
from gitgen.ai_client import API_VERSION, AIError, CallBudget, ClaudeClient

FAKE_KEY = "not-a-real-key"          # 키 모양이 아닌, 누가 봐도 가짜인 값
SYSTEM = "시스템 지시문"
MESSAGES = [{"role": "user", "content": "변경 요약해 줘"}]


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self.server = FakeApiServer()
        self.addCleanup(self.server.close)

    def client(self, **overrides):
        options = dict(model="fake-model", temperature=0.2, max_tokens=300,
                       timeout=5.0, url=self.server.url)
        options.update(overrides)
        return ClaudeClient(FAKE_KEY, **options)

    def fail(self, client=None) -> AIError:
        with self.assertRaises(AIError) as caught:
            (client or self.client()).complete(SYSTEM, MESSAGES)
        return caught.exception


class RequestTest(ClientTestCase):
    def test_request_has_headers_and_json_body(self):
        self.server.reply(body=message_body("ok"))

        self.client().complete(SYSTEM, MESSAGES)

        received = self.server.received[0]
        self.assertEqual(received.path, "/v1/messages")
        self.assertEqual(received.headers["x-api-key"], FAKE_KEY)
        self.assertEqual(received.headers["anthropic-version"], API_VERSION)
        self.assertEqual(received.headers["content-type"], "application/json")
        self.assertEqual(received.json(), {
            "model": "fake-model", "max_tokens": 300, "temperature": 0.2,
            "system": SYSTEM, "messages": MESSAGES,
        })

    def test_temperature_none_is_not_sent(self):
        self.server.reply(body=message_body("ok"))

        self.client(temperature=None).complete(SYSTEM, MESSAGES)

        self.assertNotIn("temperature", self.server.received[0].json())

    def test_korean_text_survives_utf8_round_trip(self):
        self.server.reply(body=message_body("한글 응답"))

        completion = self.client().complete(SYSTEM, MESSAGES)

        self.assertEqual(self.server.received[0].json()["messages"][0]["content"], "변경 요약해 줘")
        self.assertEqual(completion.text, "한글 응답")


class ResponseTest(ClientTestCase):
    def test_text_blocks_are_joined_and_usage_read(self):
        self.server.reply(body=message_body("첫 줄\n", "둘째 줄", input_tokens=1234, output_tokens=56,
                                             extra_blocks=[{"type": "thinking", "thinking": ""}]))

        completion = self.client().complete(SYSTEM, MESSAGES)

        self.assertEqual(completion.text, "첫 줄\n둘째 줄")
        self.assertEqual((completion.input_tokens, completion.output_tokens), (1234, 56))
        self.assertEqual(completion.stop_reason, "end_turn")
        self.assertFalse(completion.truncated)

    def test_max_tokens_stop_is_reported_as_truncated(self):
        self.server.reply(body=message_body("잘린 응", stop_reason="max_tokens"))
        self.assertTrue(self.client().complete(SYSTEM, MESSAGES).truncated)

    def test_refusal(self):
        self.server.reply(body=message_body("", stop_reason="refusal"))
        self.assertEqual(self.fail().kind, "refusal")

    def test_empty_text_and_broken_json(self):
        for body in (message_body("  "), b"{not json", b'{"type": "error"}'):
            with self.subTest(body=body):
                self.server.reply(body=body)
                self.assertEqual(self.fail().kind, "bad_response")


class HttpErrorTest(ClientTestCase):
    def test_status_codes_are_classified_with_cause_and_hint(self):
        cases = [
            (400, "invalid_request_error", "temperature: not supported", "bad_request", "--temperature none"),
            (401, "authentication_error", "invalid x-api-key", "auth", "API Key"),
            (402, "billing_error", "credit balance too low", "billing", "크레딧"),
            (403, "permission_error", "not allowed", "permission", "권한"),
            (404, "not_found_error", "model: nope", "not_found", "--model"),
            (413, "request_too_large", "too large", "too_large", "--safe-mode"),
            (500, "api_error", "internal", "server", "잠시 후"),
            (529, "overloaded_error", "Overloaded", "overloaded", "과부하"),
        ]
        for status, api_type, api_message, kind, hint_word in cases:
            with self.subTest(status=status):
                self.server.reply(status, error_body(api_type, api_message, f"req_{status}"))
                error = self.fail()
                self.assertEqual((error.kind, error.status), (kind, status))
                message = str(error)
                self.assertIn(f"HTTP {status} {api_type}", message)
                self.assertIn(api_message, message)
                self.assertIn(hint_word, message)
                self.assertIn(f"request-id: req_{status}", message)
                self.assertNotIn(FAKE_KEY, message)

    def test_rate_limit_says_how_long_to_wait(self):
        self.server.reply(429, error_body("rate_limit_error", "slow down"), {"retry-after": "7"})
        error = self.fail()
        self.assertEqual(error.kind, "rate_limit")
        self.assertIn("7초 뒤에", str(error))

    def test_error_body_that_is_not_json(self):
        self.server.reply(502, b"<html>Bad Gateway</html>", {"request-id": "req_hdr"})
        error = self.fail()
        self.assertEqual((error.kind, error.status), ("server", 502))
        self.assertIn("HTTP 502", str(error))
        self.assertIn("request-id: req_hdr", str(error))


class TransportFailureTest(ClientTestCase):
    def test_slow_response_times_out(self):
        self.server.reply(body=message_body("늦음"), delay=1.0)
        error = self.fail(self.client(timeout=0.2))
        self.assertEqual(error.kind, "timeout")
        self.assertIn("0.2초", str(error))

    def test_connection_refused_is_a_network_error(self):
        error = self.fail(self.client(url=closed_port_url()))
        self.assertEqual(error.kind, "network")
        self.assertIn("네트워크 오류", str(error))


class CallBudgetTest(ClientTestCase):
    def test_third_call_is_refused_before_sending(self):
        client = self.client(budget=CallBudget(2))
        self.server.reply(body=message_body("1"))
        self.server.reply(body=message_body("2"))

        client.complete(SYSTEM, MESSAGES)
        client.complete(SYSTEM, MESSAGES)
        error = self.fail(client)

        self.assertEqual(error.kind, "budget")
        self.assertEqual(len(self.server.received), 2)

    def test_failed_call_still_counts(self):
        budget = CallBudget(2)
        self.server.reply(500, error_body("api_error", "boom"))
        self.fail(self.client(budget=budget))
        self.assertEqual((budget.used, budget.remaining), (1, 1))


if __name__ == "__main__":
    unittest.main()
