import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from model_broker import Broker


class OpenCodeGoBrokerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temp.name)
        keys = root / "keys"
        keys.mkdir()
        for actor in ("morch", "rorch", "qa"):
            (keys / f"{actor}.key").write_bytes(actor.encode() * 16)
        routes = root / "routes.json"
        routes.write_text(
            json.dumps(
                {
                    "limits": {"max_calls": 2, "max_prompt_chars": 1000},
                    "roles": {
                        "MORCH": {"runtime": "opencode", "model": "opencode-go/glm-5.3-flash"},
                        "RORCH": {"runtime": "opencode", "model": "opencode-go/glm-5.3-flash"},
                        "QA": {"runtime": "opencode", "model": "opencode-go/mimo-v2.6-flash"},
                    },
                }
            ),
            encoding="utf-8",
        )
        self.root = root
        self.broker = Broker(root / "calls.db", keys, root / "work", routes)
    def tearDown(self):
        self.temp.cleanup()
    def request(self, rid="one", prompt="make it"):
        return {
            "op": "call",
            "actor": "RORCH",
            "key": self.broker.keys["RORCH"].hex(),
            "request_id": rid,
            "stage": "BUILD",
            "prompt": prompt,
        }

    @patch("model_broker.subprocess.run")
    def test_receipt_idempotency_and_deny_all_config(self, run):
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "text",
                        "sessionID": "go-session-1",
                        "part": {
                            "text": json.dumps(
                                {"files": {"app.py": "x", "test_app.py": "y"}}
                            )
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "step_finish",
                        "sessionID": "go-session-1",
                        "part": {
                            "cost": 0,
                            "tokens": {"input": 11, "output": 7, "reasoning": 3},
                        },
                    }
                ),
            ]
        )
        run.return_value = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
        first = self.broker.handle(self.request())
        second = self.broker.handle(self.request())
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(run.call_count, 1)
        receipt = first["receipt"]
        self.assertEqual(receipt["actual_provider"], "opencode-go")
        self.assertEqual(receipt["actual_model"], "opencode-go/glm-5.3-flash")
        self.assertEqual(receipt["total_tokens"], 18)
        self.assertEqual(receipt["cost_value"], 0)
        config = json.loads(
            (self.root / "work" / "one" / "opencode.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["permissions"][0]["effect"], "deny")
        self.assertEqual(config["permissions"][0]["action"], "*")

    @patch("model_broker.subprocess.run")
    def test_timeout_is_terminally_recorded(self, run):
        run.side_effect = subprocess.TimeoutExpired(["opencode"], 5)
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.broker.handle(self.request(rid="timeout"))
        with sqlite3.connect(self.root / "calls.db") as conn:
            status, exit_code = conn.execute(
                "select status,exit_code from calls where request_id='timeout'"
            ).fetchone()
        self.assertEqual((status, exit_code), ("FAILED", -9))


if __name__ == "__main__":
    unittest.main()
