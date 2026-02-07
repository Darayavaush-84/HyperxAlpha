import unittest
from dataclasses import dataclass
from typing import Optional

try:
    from hyperxalpha.controller import HyperxWindow
    from hyperxalpha.constants import ConnectionStatus
except (ImportError, RuntimeError):
    HyperxWindow = None
    ConnectionStatus = None


class _FakeSwitch:
    def __init__(self, checked=False):
        self._checked = bool(checked)

    def isChecked(self):
        return self._checked

    def setChecked(self, value):
        self._checked = bool(value)


class _FakeCombo:
    def __init__(self, values, current):
        self._values = list(values)
        self._current = current

    def currentData(self):
        return self._current

    def findData(self, value):
        try:
            return self._values.index(value)
        except ValueError:
            return -1

    def setCurrentIndex(self, index):
        self._current = self._values[index]


class _FakeDeviceCombo:
    def __init__(self, values, current):
        self._values = list(values)
        self._current = current

    def currentData(self):
        return self._current

    def findData(self, value):
        try:
            return self._values.index(value)
        except ValueError:
            return -1

    def setCurrentIndex(self, index):
        self._current = self._values[index]


@dataclass
class _FakeSettings:
    start_in_tray: bool = False
    tray_notifications: bool = True
    theme_mode: str = "system"
    selected_device_key: Optional[str] = None


class _FakeSettingsService:
    def __init__(self, save_results=None, autostart_result=True):
        self._save_results = list(save_results or [])
        self.autostart_result = bool(autostart_result)
        self.save_calls = 0
        self.autostart_calls = []

    def save(self, _settings):
        self.save_calls += 1
        if self._save_results:
            return self._save_results.pop(0)
        return True

    def set_autostart(self, enabled):
        self.autostart_calls.append(bool(enabled))
        return self.autostart_result


class _FakeWindow:
    def __init__(self):
        self._verbose_io_logs = False
        self._updating_settings = False
        self._updating_device_selection = False
        self.settings = _FakeSettings()
        self.tray_switch = _FakeSwitch()
        self.notify_switch = _FakeSwitch()
        self.theme_combo = _FakeCombo(["system", "light", "dark"], "system")
        self.device_combo = _FakeDeviceCombo(["dev-a", "dev-b"], "dev-a")
        self._selected_device_key = "dev-a"
        self._settings_service = _FakeSettingsService()
        self.status = ConnectionStatus.DISCONNECTED
        self.battery = None
        self.errors = []
        self.logs = []
        self.applied_theme = 0
        self.battery_notifications = 0
        self.apply_selection_calls = []
        self.status_text_calls = 0
        self.tray_icon_calls = 0

    def _show_error(self, title, message):
        self.errors.append((title, message))

    def _log(self, message):
        self.logs.append(message)

    def _apply_theme(self):
        self.applied_theme += 1

    def _maybe_notify_battery(self):
        self.battery_notifications += 1

    def _apply_selected_device(self, reconnect=False):
        self.apply_selection_calls.append(bool(reconnect))

    def _set_status_text(self):
        self.status_text_calls += 1

    def _update_tray_icon(self):
        self.tray_icon_calls += 1


@unittest.skipIf(HyperxWindow is None, "PySide6 unavailable")
class ControllerSettingsRollbackTests(unittest.TestCase):
    def test_tray_toggle_rolls_back_when_save_fails(self):
        window = _FakeWindow()
        window.settings.start_in_tray = False
        window.tray_switch.setChecked(True)
        window._settings_service = _FakeSettingsService(save_results=[False])

        HyperxWindow._on_tray_toggle(window, None)

        self.assertFalse(window.settings.start_in_tray)
        self.assertFalse(window.tray_switch.isChecked())
        self.assertEqual(window._settings_service.autostart_calls, [])
        self.assertEqual(window.errors[0][0], "Settings Error")

    def test_tray_toggle_rolls_back_when_autostart_update_fails(self):
        window = _FakeWindow()
        window.settings.start_in_tray = False
        window.tray_switch.setChecked(True)
        window._settings_service = _FakeSettingsService(
            save_results=[True, True],
            autostart_result=False,
        )

        HyperxWindow._on_tray_toggle(window, None)

        self.assertFalse(window.settings.start_in_tray)
        self.assertFalse(window.tray_switch.isChecked())
        self.assertEqual(window._settings_service.autostart_calls, [True])
        self.assertEqual(window._settings_service.save_calls, 2)
        self.assertEqual(window.errors[0][0], "Autostart Error")

    def test_theme_change_rolls_back_when_save_fails(self):
        window = _FakeWindow()
        window.settings.theme_mode = "system"
        window.theme_combo = _FakeCombo(["system", "light", "dark"], "dark")
        window._settings_service = _FakeSettingsService(save_results=[False])

        HyperxWindow._on_theme_changed(window, None)

        self.assertEqual(window.settings.theme_mode, "system")
        self.assertEqual(window.theme_combo.currentData(), "system")
        self.assertEqual(window.applied_theme, 0)
        self.assertEqual(window.errors[0][0], "Settings Error")

    def test_notification_toggle_rolls_back_when_save_fails(self):
        window = _FakeWindow()
        window.settings.tray_notifications = True
        window.notify_switch.setChecked(False)
        window._settings_service = _FakeSettingsService(save_results=[False])

        HyperxWindow._on_notifications_toggle(window, None)

        self.assertTrue(window.settings.tray_notifications)
        self.assertTrue(window.notify_switch.isChecked())
        self.assertEqual(window.battery_notifications, 0)
        self.assertEqual(window.errors[0][0], "Settings Error")

    def test_device_selection_does_not_apply_when_save_fails(self):
        window = _FakeWindow()
        window.settings.selected_device_key = "dev-a"
        window.device_combo = _FakeDeviceCombo(["dev-a", "dev-b"], "dev-b")
        window._settings_service = _FakeSettingsService(save_results=[False])

        HyperxWindow._on_device_selection_changed(window, None)

        self.assertEqual(window.settings.selected_device_key, "dev-a")
        self.assertEqual(window._selected_device_key, "dev-a")
        self.assertEqual(window.device_combo.currentData(), "dev-a")
        self.assertEqual(window.apply_selection_calls, [])
        self.assertEqual(window.errors[0][0], "Settings Error")

    def test_device_selection_rolls_back_from_runtime_key_when_setting_was_none(self):
        window = _FakeWindow()
        window.settings.selected_device_key = None
        window._selected_device_key = "dev-a"
        window.device_combo = _FakeDeviceCombo(["dev-a", "dev-b"], "dev-b")
        window._settings_service = _FakeSettingsService(save_results=[False])

        HyperxWindow._on_device_selection_changed(window, None)

        self.assertEqual(window.settings.selected_device_key, "dev-a")
        self.assertEqual(window._selected_device_key, "dev-a")
        self.assertEqual(window.device_combo.currentData(), "dev-a")
        self.assertEqual(window.apply_selection_calls, [])
        self.assertEqual(window.errors[0][0], "Settings Error")

    def test_packet_battery_value_out_of_range_is_ignored(self):
        window = _FakeWindow()
        window.status = ConnectionStatus.CONNECTED
        window.battery = 55

        HyperxWindow._handle_packet(window, [0x21, 0xBB, 0x0B, 0xFF])

        self.assertEqual(window.battery, 55)
        self.assertEqual(window.status_text_calls, 0)
        self.assertEqual(window.tray_icon_calls, 0)
        self.assertEqual(window.battery_notifications, 0)
        self.assertTrue(any("Ignoring invalid battery value" in line for line in window.logs))

    def test_packet_battery_value_in_range_updates_state(self):
        window = _FakeWindow()
        window.status = ConnectionStatus.CONNECTED

        HyperxWindow._handle_packet(window, [0x21, 0xBB, 0x0B, 77])

        self.assertEqual(window.battery, 77)
        self.assertEqual(window.status_text_calls, 1)
        self.assertEqual(window.tray_icon_calls, 1)
        self.assertEqual(window.battery_notifications, 1)


if __name__ == "__main__":
    unittest.main()
