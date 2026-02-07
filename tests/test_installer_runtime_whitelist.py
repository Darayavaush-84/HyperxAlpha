import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import installer


class InstallerRuntimeWhitelistTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)

        self._source_package_dir = self._tmp_path / "source" / "hyperxalpha"
        self._source_package_dir.mkdir(parents=True, exist_ok=True)

        for module_name in installer.RUNTIME_MODULE_FILES:
            (self._source_package_dir / module_name).write_text(
                f"# {module_name}\n",
                encoding="utf-8",
            )

        assets_dir = self._source_package_dir / "assets" / "img"
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / "hyperx.png").write_text("png", encoding="utf-8")

        # Extra files that must never be copied by runtime whitelist.
        (self._source_package_dir / "README.md").write_text("docs", encoding="utf-8")
        (self._source_package_dir / ".git").mkdir()

        self._runtime_root = self._tmp_path / "opt" / "hyperxalpha"
        self._runtime_package_dir = self._runtime_root / "hyperxalpha"

        self._orig_source_package_dir = installer.SOURCE_PACKAGE_DIR
        self._orig_runtime_root = installer.RUNTIME_ROOT
        self._orig_runtime_package_dir = installer.RUNTIME_PACKAGE_DIR
        self._orig_runtime_module_files = installer.RUNTIME_MODULE_FILES

        installer.SOURCE_PACKAGE_DIR = self._source_package_dir
        installer.RUNTIME_ROOT = self._runtime_root
        installer.RUNTIME_PACKAGE_DIR = self._runtime_package_dir

    def tearDown(self):
        installer.SOURCE_PACKAGE_DIR = self._orig_source_package_dir
        installer.RUNTIME_ROOT = self._orig_runtime_root
        installer.RUNTIME_PACKAGE_DIR = self._orig_runtime_package_dir
        installer.RUNTIME_MODULE_FILES = self._orig_runtime_module_files
        self._tmp.cleanup()

    def test_installs_only_whitelisted_runtime_files(self):
        with redirect_stdout(io.StringIO()):
            installed = installer._install_runtime_files()
        self.assertTrue(installed)

        copied_files = sorted(
            path.relative_to(self._runtime_package_dir).as_posix()
            for path in self._runtime_package_dir.rglob("*")
            if path.is_file()
        )
        expected_files = sorted(
            list(installer.RUNTIME_MODULE_FILES) + ["assets/img/hyperx.png"]
        )
        self.assertEqual(copied_files, expected_files)

    def test_failed_staging_does_not_replace_existing_runtime(self):
        self._runtime_package_dir.mkdir(parents=True, exist_ok=True)
        sentinel_path = self._runtime_package_dir / "constants.py"
        sentinel_path.write_text("OLD_RUNTIME\n", encoding="utf-8")

        installer.RUNTIME_MODULE_FILES = installer.RUNTIME_MODULE_FILES + ("missing.py",)
        with redirect_stdout(io.StringIO()):
            installed = installer._install_runtime_files()

        self.assertFalse(installed)
        self.assertTrue(sentinel_path.is_file())
        self.assertEqual(sentinel_path.read_text(encoding="utf-8"), "OLD_RUNTIME\n")

    def test_install_fails_when_source_contains_unlisted_python_module(self):
        (self._source_package_dir / "future_module.py").write_text(
            "# new runtime module\n",
            encoding="utf-8",
        )

        with redirect_stdout(io.StringIO()):
            installed = installer._install_runtime_files()

        self.assertFalse(installed)
        self.assertFalse(self._runtime_package_dir.exists())

    def test_backup_cleanup_failure_is_warning_and_install_succeeds(self):
        self._runtime_package_dir.mkdir(parents=True, exist_ok=True)
        sentinel_path = self._runtime_package_dir / "constants.py"
        sentinel_path.write_text("OLD_RUNTIME\n", encoding="utf-8")
        backup_dir = self._runtime_root / "hyperxalpha-backup"
        original_rmtree = installer.shutil.rmtree

        def _rmtree_side_effect(path, *args, **kwargs):
            if Path(path) == backup_dir:
                raise OSError("cannot remove backup")
            return original_rmtree(path, *args, **kwargs)

        with patch("installer.shutil.rmtree", side_effect=_rmtree_side_effect):
            with redirect_stdout(io.StringIO()):
                installed = installer._install_runtime_files()

        self.assertTrue(installed)
        self.assertTrue((self._runtime_package_dir / "__init__.py").is_file())
        self.assertNotEqual(sentinel_path.read_text(encoding="utf-8"), "OLD_RUNTIME\n")


if __name__ == "__main__":
    unittest.main()
