import json
import stat
import tempfile
import unittest
from pathlib import Path

from fake_api import FakeApiServer, closed_port_url, error_body, message_body
from gitgen.ai_client import AIError, ClaudeClient
from gitgen.call_log import JsonlCallLog

FAKE_KEY = "not-a-real-key"
MESSAGES = [{"role": "user", "content": "변경 요약"}]


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class JsonlCallLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "logs" / "calls.jsonl"
        self.warnings = []

    def test_appends_one_json_object_per_call_with_shared_run_id(self):
        log = JsonlCallLog(self.path, "commit", self.warnings.append)

        log({"call": 1, "text": "한글 그대로"})
        log({"call": 2})

        lines = read_lines(self.path)
        self.assertEqual([line["call"] for line in lines], [1, 2])
        self.assertEqual({line["run_id"] for line in lines}, {log.run_id})
        self.assertEqual(lines[0]["mode"], "commit")
        self.assertIn("한글 그대로", self.path.read_text(encoding="utf-8"))   # \uXXXX 로 바꾸지 않는다
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_write_failure_only_warns(self):
        blocker = Path(self.tmp.name) / "not-a-dir"
        blocker.write_text("")
        log = JsonlCallLog(blocker / "calls.jsonl", "commit", self.warnings.append)

        log({"call": 1})

        self.assertEqual(len(self.warnings), 1)
        self.assertIn("호출 기록을 쓰지 못했습니다", self.warnings[0])


class ClientRecordingTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeApiServer()
        self.addCleanup(self.server.close)
        self.records = []

    def client(self, **overrides):
        options = dict(model="fake-model", temperature=0.2, max_tokens=300, timeout=5.0,
                       url=self.server.url, recorder=self.records.append)
        options.update(overrides)
        return ClaudeClient(FAKE_KEY, **options)

    def test_success_records_exact_request_and_raw_response(self):
        self.server.reply(body=message_body("제목", input_tokens=77, output_tokens=5))

        self.client().complete("시스템", MESSAGES)

        record = self.records[0]
        self.assertEqual(record["request"], self.server.received[0].json())   # 실제로 보낸 바디
        self.assertEqual((record["call"], record["call_limit"], record["status"]), (1, 2, 200))
        self.assertEqual(record["response"]["usage"], {"input_tokens": 77, "output_tokens": 5})
        self.assertEqual(record["response"]["content"][0]["text"], "제목")
        self.assertIsNone(record["error"])
        self.assertIsInstance(record["elapsed_ms"], int)

    def test_http_error_is_recorded_too(self):
        self.server.reply(401, error_body("authentication_error", "invalid x-api-key"))

        with self.assertRaises(AIError):
            self.client().complete("시스템", MESSAGES)

        record = self.records[0]
        self.assertEqual((record["status"], record["error"]["kind"]), (401, "auth"))
        self.assertEqual(record["response"]["error"]["type"], "authentication_error")

    def test_network_error_has_no_response(self):
        with self.assertRaises(AIError):
            self.client(url=closed_port_url()).complete("시스템", MESSAGES)

        record = self.records[0]
        self.assertEqual((record["status"], record["response"]), (None, None))
        self.assertEqual(record["error"]["kind"], "network")

    def test_api_key_never_appears_in_records(self):
        self.server.reply(body=message_body("ok"))
        self.server.reply(401, error_body("authentication_error", "bad key"))
        client = self.client()

        client.complete("시스템", MESSAGES)
        with self.assertRaises(AIError):
            client.complete("시스템", MESSAGES)

        self.assertEqual(len(self.records), 2)
        self.assertNotIn(FAKE_KEY, json.dumps(self.records, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
