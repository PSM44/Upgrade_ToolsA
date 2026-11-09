from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_router import AllRoutesFailed, ModelRouter, NoRouteError, Usage


class ModelRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "router.db"
        self.config = {
            "roles": {
                "MORCH": [{"provider": "provider-a", "model": "alpha", "require_identity_receipt": True}],
                "RORCH": [
                    {"provider": "provider-fail", "model": "broken", "max_attempts": 2},
                    {"provider": "provider-a", "model": "alpha"},
                ],
                "QA": [{"provider": "provider-b", "model": "beta", "require_identity_receipt": True}],
            },
            "task_routes": {
                "RORCH:SECURITY_REVIEW": [
                    {"provider": "provider-b", "model": "beta", "route_id": "rorch-security", "require_identity_receipt": True}
                ]
            },
        }

        def backend_a(route, prompt, context):
            return {
                "ok": True,
                "response": f"A:{route.model}:{prompt}",
                "usage": {"input_tokens": 5, "output_tokens": 2, "api_calls": 1, "cost_status": "unknown", "cost_usd": 0},
                "receipt": {"provider": "provider-a", "model": "alpha", "request_id": "req-a", "cost_authority": "unknown"},
            }

        def backend_b(route, prompt, context):
            return {
                "ok": True,
                "response": f"B:{route.model}:{prompt}",
                "usage": {"input_tokens": 7, "output_tokens": 3, "api_calls": 1, "cost_status": "measured", "cost_usd": 0.002},
                "receipt": {"provider": "provider-b", "model": "beta", "request_id": "req-b", "cost_authority": "provider"},
            }

        def backend_fail(route, prompt, context):
            raise TimeoutError("synthetic timeout")

        self.router = ModelRouter(
            self.config,
            self.db,
            {"provider-a": backend_a, "provider-b": backend_b, "provider-fail": backend_fail},
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_two_distinct_role_routes_are_persisted(self):
        morch = self.router.invoke("MORCH", "coordinate")
        qa = self.router.invoke("qa", "review")
        self.assertEqual((morch["provider"], morch["model"]), ("provider-a", "alpha"))
        self.assertEqual((qa["provider"], qa["model"]), ("provider-b", "beta"))
        self.assertTrue(self.router.multimodel_verified())
        self.assertEqual(self.router.successful_routes(), {("provider-a", "alpha"), ("provider-b", "beta")})

    def test_bounded_retry_then_fallback(self):
        result = self.router.invoke("RORCH", "build")
        self.assertEqual(result["provider"], "provider-a")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["attempts"], 3)
        with closing(sqlite3.connect(self.db)) as conn:
            states = conn.execute("SELECT status FROM model_attempts ORDER BY ordinal").fetchall()
        self.assertEqual(states, [("FAILED",), ("FAILED",), ("SUCCESS",)])

    def test_unknown_cost_is_null_not_zero(self):
        result = self.router.invoke("MORCH", "cost")
        self.assertEqual(result["usage"]["cost_status"], "unknown")
        self.assertIsNone(result["usage"]["cost_usd"])
        with closing(sqlite3.connect(self.db)) as conn:
            stored = conn.execute("SELECT cost_status,cost_usd FROM model_attempts WHERE status='SUCCESS'").fetchone()
        self.assertEqual(stored, ("unknown", None))
        summary = self.router.usage_summary()
        self.assertFalse(summary["cost_complete"])
        self.assertIsNone(summary["cost_usd"])

    def test_config_file_changes_role_selection_without_code_change(self):
        path = self.root / "routes.json"
        path.write_text(json.dumps(self.config), encoding="utf-8")
        second = ModelRouter(path, self.root / "second.db", self.router.backends)
        self.assertEqual(second.invoke("QA", "x")["model"], "beta")

    def test_task_specific_route_and_correlated_receipt(self):
        result = self.router.invoke(
            "RORCH", "inspect", task_type="security_review", correlation_id="corr-123"
        )
        self.assertEqual(result["route_id"], "rorch-security")
        self.assertEqual(result["model"], "beta")
        receipts = self.router.invocation_receipts("corr-123")
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["actual_model"], "beta")
        self.assertEqual(receipts[0]["identity_status"], "VERIFIED_PROVIDER_RECEIPT")
        self.assertEqual(len(receipts[0]["receipt_sha256"]), 64)

    def test_required_identity_receipt_rejects_relabeling(self):
        def relabeled(route, prompt, context):
            return {
                "response": "fake",
                "usage": {"cost_status": "unknown"},
                "receipt": {"provider": "provider-a", "model": "different", "request_id": "req-x"},
            }
        router = ModelRouter(
            {"roles": {"QA": [{"provider": "provider-a", "model": "alpha", "require_identity_receipt": True}]}},
            self.root / "relabel.db", {"provider-a": relabeled},
        )
        with self.assertRaises(AllRoutesFailed):
            router.invoke("QA", "review")
        self.assertFalse(router.multimodel_verified())

    def test_unattested_declared_routes_do_not_count_as_multimodel(self):
        router = ModelRouter(
            {"roles": {"QA": [{"provider": "declared", "model": "label-only"}]}},
            self.root / "declared.db",
            {"declared": lambda route, prompt, context: {"response": "ok", "usage": {"cost_status": "unknown"}}},
        )
        result = router.invoke("QA", "review")
        self.assertEqual(result["identity"]["status"], "UNVERIFIED_DECLARED_ROUTE")
        self.assertFalse(router.multimodel_verified())

    def test_missing_role_is_fail_closed(self):
        with self.assertRaises(NoRouteError):
            self.router.invoke("UNAUTHORIZED", "x")

    def test_all_routes_failed_records_terminal_failure(self):
        router = ModelRouter(
            {"roles": {"QA": [{"provider": "provider-fail", "model": "broken", "max_attempts": 2}]}},
            self.root / "failed.db",
            {"provider-fail": self.router.backends["provider-fail"]},
        )
        with self.assertRaises(AllRoutesFailed) as raised:
            router.invoke("QA", "x")
        self.assertEqual(len(raised.exception.failures), 2)
        with closing(sqlite3.connect(self.root / "failed.db")) as conn:
            self.assertEqual(conn.execute("SELECT status FROM model_invocations").fetchone()[0], "FAILED")

    def test_usage_validation_requires_known_cost_value(self):
        with self.assertRaises(ValueError):
            Usage.from_dict({"cost_status": "measured", "cost_usd": None})


if __name__ == "__main__":
    unittest.main(verbosity=2)

