import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import installer


class InstallerReceiptTests(unittest.TestCase):
    def test_write_install_receipt_is_atomic_and_valid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            state_dir = temp_path / "state"
            receipt_path = state_dir / "install-receipt.json"

            original_state_dir = installer.STATE_DIR
            original_receipt_path = installer.RECEIPT_PATH
            try:
                installer.STATE_DIR = str(state_dir)
                installer.RECEIPT_PATH = str(receipt_path)

                payload = {"a": 1, "b": "value"}
                with redirect_stdout(io.StringIO()):
                    ok = installer._write_install_receipt(payload)
            finally:
                installer.STATE_DIR = original_state_dir
                installer.RECEIPT_PATH = original_receipt_path

            self.assertTrue(ok)
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")), payload)
            temp_files = list(state_dir.glob("install-receipt-*.json"))
            self.assertEqual(temp_files, [])


if __name__ == "__main__":
    unittest.main()
