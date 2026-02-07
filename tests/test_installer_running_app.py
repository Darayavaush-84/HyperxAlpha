import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import installer


class InstallerRunningAppTests(unittest.TestCase):
    def test_is_hyperxalpha_cmdline_matches_launcher_exec(self):
        launcher_tokens = {"/usr/local/bin/hyperxalpha"}
        matched = installer._is_hyperxalpha_cmdline(
            ["/usr/bin/python3", "/usr/local/bin/hyperxalpha"],
            launcher_tokens=launcher_tokens,
        )
        self.assertTrue(matched)

    def test_is_hyperxalpha_cmdline_matches_module_exec(self):
        matched = installer._is_hyperxalpha_cmdline(
            ["/usr/bin/python3", "-m", "hyperxalpha"]
        )
        self.assertTrue(matched)

    def test_stop_running_app_reports_not_running_when_no_pid(self):
        with patch("installer.os.geteuid", return_value=0):
            with patch("installer._running_hyperxalpha_pids", return_value=[]):
                had_running, stopped = installer._stop_running_app()

        self.assertFalse(had_running)
        self.assertTrue(stopped)

    def test_stop_running_app_uses_sigterm_and_succeeds(self):
        with patch("installer.os.geteuid", return_value=0):
            with patch("installer._running_hyperxalpha_pids", return_value=[111, 222]):
                with patch("installer._wait_for_exit", return_value=set()):
                    with patch("installer.os.kill") as mocked_kill:
                        had_running, stopped = installer._stop_running_app()

        self.assertTrue(had_running)
        self.assertTrue(stopped)
        mocked_kill.assert_any_call(111, installer.signal.SIGTERM)
        mocked_kill.assert_any_call(222, installer.signal.SIGTERM)

    def test_install_all_aborts_when_app_cannot_be_stopped(self):
        with patch("installer.os.geteuid", return_value=0):
            with patch("installer._stop_running_app", return_value=(True, False)):
                with patch("installer._install_runtime_files") as mocked_runtime:
                    with redirect_stdout(io.StringIO()):
                        ok = installer.install_all(scope="user")

        self.assertFalse(ok)
        mocked_runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
