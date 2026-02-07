import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import installer


class InstallerScopeTests(unittest.TestCase):
    def test_prompt_install_scope_defaults_to_user_when_non_interactive(self):
        fake_stdin = SimpleNamespace(isatty=lambda: False)
        with patch.object(installer.sys, "stdin", fake_stdin):
            with redirect_stdout(io.StringIO()):
                scope = installer._prompt_install_scope()

        self.assertEqual(scope, "user")

    def test_prompt_install_scope_respects_default_override_when_non_interactive(self):
        fake_stdin = SimpleNamespace(isatty=lambda: False)
        with patch.object(installer.sys, "stdin", fake_stdin):
            with redirect_stdout(io.StringIO()):
                scope = installer._prompt_install_scope(default_scope="system")

        self.assertEqual(scope, "system")

    def test_main_passes_scope_override_to_install_all(self):
        with patch("installer.install_all", return_value=True) as mocked_install:
            with patch("sys.argv", ["installer.py", "--scope", "user"]):
                exit_code = installer.main()

        self.assertEqual(exit_code, 0)
        mocked_install.assert_called_once_with(scope="user")


if __name__ == "__main__":
    unittest.main()
