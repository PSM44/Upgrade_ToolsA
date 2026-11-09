from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_route_probe import probe


class ModelRouteProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / "routes.json"
        self.config.write_text(json.dumps({
            "roles": {"MORCH": [{"provider": "p1", "model": "m1"}]},
            "task_routes": {"QA:SECURITY": [{"provider": "p2", "model": "m2"}]},
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_labels_alone_are_not_multimodel_evidence(self):
        result = probe(self.config)
        self.assertEqual(len(result["declared_identities"]), 2)
        self.assertEqual(result["verified_identities"], [])
        self.assertFalse(result["real_multimodel_verified"])

    def test_two_provider_receipts_are_required(self):
        db = self.root / "router.db"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE model_receipts(actual_provider TEXT, actual_model TEXT, identity_status TEXT)")
            conn.executemany("INSERT INTO model_receipts VALUES(?,?,?)", [
                ("p1", "m1", "VERIFIED_PROVIDER_RECEIPT"),
                ("p2", "m2", "UNVERIFIED_NO_REQUEST_ID"),
            ])
            conn.commit()
        self.assertFalse(probe(self.config, db)["real_multimodel_verified"])
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("UPDATE model_receipts SET identity_status='VERIFIED_PROVIDER_RECEIPT' WHERE actual_provider='p2'")
            conn.commit()
        self.assertTrue(probe(self.config, db)["real_multimodel_verified"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

