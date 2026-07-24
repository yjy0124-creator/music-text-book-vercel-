import tempfile
import unittest
from pathlib import Path

from audit_server import latest_audit


class AuditServerTests(unittest.TestCase):
    def test_latest_audit_finds_document_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = root / "document"
            document.mkdir()
            expected = document / "audit.html"
            expected.write_text("ok", encoding="utf-8")
            self.assertEqual(latest_audit(root), expected.resolve())


if __name__ == "__main__":
    unittest.main()
