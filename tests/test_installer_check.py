import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import installer


class InstallerCheckTests(unittest.TestCase):
    def test_check_fails_when_udev_rule_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_rule_path = temp_path / "50-hyperxalpha.rules"
            launcher_path = temp_path / "hyperxalpha"
            launcher_path.write_text("#!/bin/sh\n", encoding="utf-8")

            original_udev_rule_path = installer.UDEV_RULE_PATH
            original_launcher_path = installer.LAUNCHER_PATH
            try:
                installer.UDEV_RULE_PATH = str(fake_rule_path)
                installer.LAUNCHER_PATH = launcher_path
                with patch("installer._check_qt", return_value=(True, None)):
                    with patch("installer._check_hidraw_lib", return_value=True):
                        with patch("sys.argv", ["installer.py", "--check"]):
                            with redirect_stdout(io.StringIO()):
                                exit_code = installer.main()
            finally:
                installer.UDEV_RULE_PATH = original_udev_rule_path
                installer.LAUNCHER_PATH = original_launcher_path

            self.assertEqual(exit_code, 1)

    def test_check_succeeds_when_all_requirements_are_present(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_rule_path = temp_path / "50-hyperxalpha.rules"
            fake_rule_path.write_text("rule", encoding="utf-8")
            launcher_path = temp_path / "hyperxalpha"
            launcher_path.write_text("#!/bin/sh\n", encoding="utf-8")

            original_udev_rule_path = installer.UDEV_RULE_PATH
            original_launcher_path = installer.LAUNCHER_PATH
            try:
                installer.UDEV_RULE_PATH = str(fake_rule_path)
                installer.LAUNCHER_PATH = launcher_path
                with patch("installer._check_qt", return_value=(True, None)):
                    with patch("installer._check_hidraw_lib", return_value=True):
                        with patch("sys.argv", ["installer.py", "--check"]):
                            with redirect_stdout(io.StringIO()):
                                exit_code = installer.main()
            finally:
                installer.UDEV_RULE_PATH = original_udev_rule_path
                installer.LAUNCHER_PATH = original_launcher_path

            self.assertEqual(exit_code, 0)

    def test_install_user_home_is_derived_from_user_desktop_path(self):
        home = installer._install_user_home(
            "user",
            Path("/home/alice/.local/share/applications/hyperxalpha.desktop"),
        )
        self.assertEqual(home, "/home/alice")

    def test_install_user_home_is_none_for_system_scope(self):
        home = installer._install_user_home(
            "system",
            Path("/usr/share/applications/hyperxalpha.desktop"),
        )
        self.assertIsNone(home)


if __name__ == "__main__":
    unittest.main()
