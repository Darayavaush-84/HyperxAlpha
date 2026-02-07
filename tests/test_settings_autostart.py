import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hyperxalpha import settings


class SettingsAutostartTests(unittest.TestCase):
    def test_resolve_exec_command_creates_user_launcher_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            user_launcher = temp_path / ".local" / "bin" / "hyperxalpha"
            source_root = temp_path / "source-root"
            source_root.mkdir(parents=True, exist_ok=True)

            original_system_launcher = settings.SYSTEM_LAUNCHER_PATH
            original_user_launcher = settings.USER_LAUNCHER_PATH
            original_source_root = settings.SOURCE_ROOT
            try:
                settings.SYSTEM_LAUNCHER_PATH = temp_path / "missing-system-launcher"
                settings.USER_LAUNCHER_PATH = user_launcher
                settings.SOURCE_ROOT = source_root

                with patch("hyperxalpha.settings.shutil.which", return_value=None):
                    command = settings._resolve_exec_command(start_hidden=True)
            finally:
                settings.SYSTEM_LAUNCHER_PATH = original_system_launcher
                settings.USER_LAUNCHER_PATH = original_user_launcher
                settings.SOURCE_ROOT = original_source_root

            self.assertTrue(user_launcher.is_file())
            self.assertTrue(os.access(user_launcher, os.X_OK))
            launcher_content = user_launcher.read_text(encoding="utf-8")
            self.assertIn("runpy.run_module('hyperxalpha', run_name='__main__')", launcher_content)
            self.assertIn(str(source_root), launcher_content)
            self.assertEqual(
                command,
                f"{settings._escape_desktop_value(str(user_launcher))} --start-hidden",
            )

    def test_load_settings_accepts_legacy_notification_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_dir = temp_path / "config"
            config_path = config_dir / "settings.json"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps({"low_battery_notifications": False}),
                encoding="utf-8",
            )

            original_config_dir = settings.CONFIG_DIR
            original_config_path = settings.CONFIG_PATH
            try:
                settings.CONFIG_DIR = config_dir
                settings.CONFIG_PATH = config_path
                loaded = settings.load_settings()
                settings.save_settings(loaded)
            finally:
                settings.CONFIG_DIR = original_config_dir
                settings.CONFIG_PATH = original_config_path

            self.assertFalse(loaded.tray_notifications)
            saved_payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertIn("tray_notifications", saved_payload)
            self.assertNotIn("low_battery_notifications", saved_payload)

    def test_load_settings_normalizes_boolean_and_theme_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_dir = temp_path / "config"
            config_path = config_dir / "settings.json"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "start_in_tray": "false",
                        "mic_monitor_state": "1",
                        "tray_notifications": "off",
                        "theme_mode": "invalid",
                    }
                ),
                encoding="utf-8",
            )

            original_config_dir = settings.CONFIG_DIR
            original_config_path = settings.CONFIG_PATH
            try:
                settings.CONFIG_DIR = config_dir
                settings.CONFIG_PATH = config_path
                loaded = settings.load_settings()
            finally:
                settings.CONFIG_DIR = original_config_dir
                settings.CONFIG_PATH = original_config_path

            self.assertFalse(loaded.start_in_tray)
            self.assertTrue(loaded.mic_monitor_state)
            self.assertFalse(loaded.tray_notifications)
            self.assertEqual(loaded.theme_mode, "system")

    def test_save_settings_is_atomic_and_cleans_temp_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_dir = temp_path / "config"
            config_path = config_dir / "settings.json"
            config_dir.mkdir(parents=True, exist_ok=True)
            settings_obj = settings.AppSettings(
                start_in_tray=True,
                mic_monitor_state=False,
                selected_device_key="dev-a",
                tray_notifications=True,
                theme_mode="dark",
            )

            original_config_dir = settings.CONFIG_DIR
            original_config_path = settings.CONFIG_PATH
            try:
                settings.CONFIG_DIR = config_dir
                settings.CONFIG_PATH = config_path
                ok = settings.save_settings(settings_obj)
            finally:
                settings.CONFIG_DIR = original_config_dir
                settings.CONFIG_PATH = original_config_path

            self.assertTrue(ok)
            self.assertTrue(config_path.is_file())
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["start_in_tray"], True)
            self.assertEqual(payload["mic_monitor_state"], False)
            self.assertEqual(payload["selected_device_key"], "dev-a")
            self.assertEqual(payload["tray_notifications"], True)
            self.assertEqual(payload["theme_mode"], "dark")
            self.assertEqual(list(config_dir.glob("settings-*.json")), [])


if __name__ == "__main__":
    unittest.main()
