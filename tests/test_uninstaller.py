import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import uninstaller


class UninstallerCmdlineTests(unittest.TestCase):
    def test_matches_launcher_path_from_receipt(self):
        receipt = {"launcher_path": "/usr/local/bin/hyperxalpha"}
        launcher_tokens = uninstaller._candidate_launcher_tokens(receipt)

        matched = uninstaller._is_hyperxalpha_cmdline(
            ["/usr/bin/python3", "/usr/local/bin/hyperxalpha"],
            launcher_tokens=launcher_tokens,
        )

        self.assertTrue(matched)

    def test_does_not_match_unrelated_command(self):
        receipt = {"launcher_path": "/usr/local/bin/hyperxalpha"}
        launcher_tokens = uninstaller._candidate_launcher_tokens(receipt)

        matched = uninstaller._is_hyperxalpha_cmdline(
            ["/usr/bin/python3", "/usr/local/bin/other-app"],
            launcher_tokens=launcher_tokens,
        )

        self.assertFalse(matched)

    def test_unsafe_paths_from_receipt_are_ignored(self):
        receipt = {
            "udev_rule_path": "/etc/passwd",
            "desktop_entry_path": "/etc/shadow",
            "launcher_path": "/usr/bin/python3",
        }

        with redirect_stdout(io.StringIO()):
            desktop_paths = {
                str(path) for path in uninstaller._candidate_desktop_paths(receipt)
            }
            launcher_paths = {
                str(path) for path in uninstaller._candidate_launcher_paths(receipt)
            }
            launcher_tokens = uninstaller._candidate_launcher_tokens(receipt)
            udev_path = uninstaller._candidate_udev_path(receipt)

        self.assertNotIn("/etc/shadow", desktop_paths)
        self.assertNotIn("/usr/bin/python3", launcher_paths)
        self.assertNotIn("/usr/bin/python3", launcher_tokens)
        self.assertEqual(udev_path, uninstaller.UDEV_RULE_PATH)

    def test_corrupted_receipt_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            receipt_path = Path(temp_dir) / "install-receipt.json"
            receipt_path.write_text("{invalid", encoding="utf-8")

            original = uninstaller.RECEIPT_PATH
            uninstaller.RECEIPT_PATH = receipt_path
            try:
                with redirect_stdout(io.StringIO()):
                    loaded = uninstaller._read_receipt()
            finally:
                uninstaller.RECEIPT_PATH = original

            self.assertIsNone(loaded)

    def test_candidate_homes_includes_regular_users_when_running_as_root(self):
        fake_users = [
            SimpleNamespace(pw_uid=0, pw_dir="/root", pw_shell="/bin/bash"),
            SimpleNamespace(pw_uid=1000, pw_dir="/home/alice", pw_shell="/bin/bash"),
            SimpleNamespace(pw_uid=1001, pw_dir="/home/bob", pw_shell="/usr/sbin/nologin"),
            SimpleNamespace(pw_uid=1002, pw_dir="/home/chris", pw_shell="/bin/false"),
        ]
        with patch.dict("os.environ", {}, clear=True):
            with patch("uninstaller.os.geteuid", return_value=0):
                with patch("uninstaller.Path.home", return_value=Path("/root")):
                    with patch("uninstaller.pwd.getpwall", return_value=fake_users):
                        homes = uninstaller._candidate_homes()

        self.assertIn(Path("/root"), homes)
        self.assertIn(Path("/home/alice"), homes)
        self.assertNotIn(Path("/home/bob"), homes)
        self.assertNotIn(Path("/home/chris"), homes)

    def test_path_validation_handles_runtime_error(self):
        with patch("uninstaller.Path.resolve", side_effect=RuntimeError("loop")):
            allowed = uninstaller._path_in_allowed_dirs(Path("/tmp/a"), [], "a")
            runtime_safe = uninstaller._safe_runtime_root(Path("/opt/hyperxalpha"))

        self.assertFalse(allowed)
        self.assertFalse(runtime_safe)

    def test_candidate_paths_respect_user_install_scope(self):
        receipt = {
            "install_scope": "user",
            "install_user_home": "/home/alice",
            "desktop_entry_path": "/home/alice/.local/share/applications/hyperxalpha.desktop",
        }
        with patch("uninstaller._candidate_homes", return_value=[Path("/home/alice"), Path("/home/bob")]):
            desktop_paths = {str(path) for path in uninstaller._candidate_desktop_paths(receipt)}
            autostart_paths = {str(path) for path in uninstaller._candidate_autostart_paths(receipt)}

        self.assertIn("/home/alice/.local/share/applications/hyperxalpha.desktop", desktop_paths)
        self.assertNotIn("/home/bob/.local/share/applications/hyperxalpha.desktop", desktop_paths)
        self.assertNotIn("/usr/share/applications/hyperxalpha.desktop", desktop_paths)
        self.assertIn("/home/alice/.config/autostart/hyperxalpha.desktop", autostart_paths)
        self.assertNotIn("/home/bob/.config/autostart/hyperxalpha.desktop", autostart_paths)
        self.assertNotIn("/etc/xdg/autostart/hyperxalpha.desktop", autostart_paths)

    def test_candidate_paths_respect_system_install_scope(self):
        receipt = {
            "install_scope": "system",
            "desktop_entry_path": "/usr/share/applications/hyperxalpha.desktop",
        }
        with patch("uninstaller._candidate_homes", return_value=[Path("/home/alice"), Path("/home/bob")]):
            desktop_paths = {str(path) for path in uninstaller._candidate_desktop_paths(receipt)}
            autostart_paths = {str(path) for path in uninstaller._candidate_autostart_paths(receipt)}

        self.assertIn("/usr/share/applications/hyperxalpha.desktop", desktop_paths)
        self.assertIn("/usr/local/share/applications/hyperxalpha.desktop", desktop_paths)
        self.assertNotIn("/home/alice/.local/share/applications/hyperxalpha.desktop", desktop_paths)
        self.assertIn("/etc/xdg/autostart/hyperxalpha.desktop", autostart_paths)
        self.assertNotIn("/home/alice/.config/autostart/hyperxalpha.desktop", autostart_paths)


if __name__ == "__main__":
    unittest.main()
