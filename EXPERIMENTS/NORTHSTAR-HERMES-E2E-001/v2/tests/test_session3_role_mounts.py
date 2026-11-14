import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from persistent_control_plane import ControlPlane


class RoleMountPolicyTest(unittest.TestCase):
    def test_rorch_cannot_mount_qa_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            cp = ControlPlane(Path(td))
            command = cp.worker_cmd("RORCH", "m1", "o1")
            self.assertNotIn("/role/qa", command)
            self.assertIn("/role/dev", command)

    def test_qa_sees_app_and_dev_read_only_and_owns_qa(self):
        with tempfile.TemporaryDirectory() as td:
            cp = ControlPlane(Path(td))
            command = cp.worker_cmd("QA", "m1", "o1")
            qa_index = command.index("/role/qa")
            self.assertEqual(command[qa_index - 2], "--bind")
            for mount in ("/role/app", "/role/dev"):
                index = command.index(mount)
                self.assertEqual(command[index - 2], "--ro-bind")


if __name__ == "__main__":
    unittest.main()

