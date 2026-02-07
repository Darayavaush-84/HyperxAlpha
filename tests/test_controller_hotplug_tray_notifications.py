import unittest
from collections import deque
from dataclasses import dataclass

from hyperxalpha.constants import ConnectionStatus
from hyperxalpha.device_service import DeviceDescriptor

try:
    from hyperxalpha.controller import HyperxWindow
except (ImportError, RuntimeError):
    HyperxWindow = None


class _FakeCombo:
    def __init__(self):
        self._items = []
        self._enabled = True
        self._index = -1

    def clear(self):
        self._items = []
        self._index = -1

    def addItem(self, _label, value):
        self._items.append(value)
        if self._index < 0:
            self._index = 0

    def setEnabled(self, enabled):
        self._enabled = bool(enabled)

    def findData(self, value):
        try:
            return self._items.index(value)
        except ValueError:
            return -1

    def setCurrentIndex(self, index):
        self._index = int(index)

    def currentData(self):
        if self._index < 0 or self._index >= len(self._items):
            return None
        return self._items[self._index]


class _FakeTray:
    def __init__(self):
        self.messages = []

    def showMessage(self, title, message, icon, timeout):
        self.messages.append((title, message, icon, timeout))


class _FakeTimer:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class _FakeSwitch:
    def __init__(self):
        self.checked = False

    def setChecked(self, checked):
        self.checked = bool(checked)

    def isChecked(self):
        return self.checked


@dataclass
class _FakeSettings:
    selected_device_key: str | None = None
    tray_notifications: bool = True


class _FakeSettingsService:
    def __init__(self):
        self.saved = []

    def save(self, settings):
        self.saved.append(settings.selected_device_key)
        return True


def _descriptor(key, path):
    return DeviceDescriptor(
        key=key,
        vendor_id=0x03F0,
        product_id=0x098D,
        model_name="HyperX Cloud Alpha Wireless",
        path=path,
        serial_number=None,
        manufacturer_string=None,
        product_string=None,
    )


@unittest.skipIf(HyperxWindow is None, "PySide6 unavailable")
class ControllerHotplugTrayNotificationTests(unittest.TestCase):
    def test_hotplug_reconnects_when_selected_device_changes(self):
        old = _descriptor("path:/dev/hidraw0", "/dev/hidraw0")
        new = _descriptor("path:/dev/hidraw1", "/dev/hidraw1")

        class _FakeWindow:
            _device_signature = staticmethod(HyperxWindow._device_signature)
            _populate_device_list = HyperxWindow._populate_device_list

            def __init__(self):
                self._shutting_down = False
                self._last_device_scan_error = None
                self._last_device_signature = self._device_signature([old])
                self._selected_device_key = old.key
                self._updating_device_selection = False
                self._device_by_key = {}
                self.settings = _FakeSettings(selected_device_key=old.key)
                self._settings_service = _FakeSettingsService()
                self.device_combo = _FakeCombo()
                self._apply_calls = []

            def _list_compatible_devices(self, log_failures=True):
                _ = log_failures
                return [new], None

            def _apply_selected_device(self, reconnect=False):
                self._apply_calls.append(bool(reconnect))

            def _log(self, _message):
                return None

        window = _FakeWindow()
        HyperxWindow._poll_device_hotplug(window)

        self.assertEqual(window._selected_device_key, new.key)
        self.assertEqual(window.settings.selected_device_key, new.key)
        self.assertEqual(window._apply_calls, [True])

    def test_hotplug_noop_when_signature_unchanged(self):
        same = _descriptor("path:/dev/hidraw2", "/dev/hidraw2")

        class _FakeWindow:
            _device_signature = staticmethod(HyperxWindow._device_signature)

            def __init__(self):
                self._shutting_down = False
                self._last_device_scan_error = None
                self._last_device_signature = self._device_signature([same])
                self._selected_device_key = same.key
                self.settings = _FakeSettings(selected_device_key=same.key)
                self._apply_calls = []

            def _list_compatible_devices(self, log_failures=True):
                _ = log_failures
                return [same], None

            def _populate_device_list(self, devices, preferred_key=None):
                _ = devices
                _ = preferred_key
                self._apply_calls.append("populate")

            def _log(self, _message):
                return None

        window = _FakeWindow()
        HyperxWindow._poll_device_hotplug(window)

        self.assertEqual(window._apply_calls, [])

    def test_tray_voice_toggle_routes_to_same_command_path(self):
        class _FakeWindow:
            def __init__(self):
                self._updating_tray_controls = False
                self.status = ConnectionStatus.CONNECTED
                self._device_ready = True
                self._updating_controls = False
                self.voice_switch = _FakeSwitch()
                self.calls = []

            def _on_voice_toggle(self, active):
                self.calls.append(bool(active))

            def _sync_tray_quick_controls_from_ui(self):
                self.calls.append("sync")

        window = _FakeWindow()
        HyperxWindow._on_tray_voice_action_toggled(window, True)

        self.assertTrue(window.voice_switch.isChecked())
        self.assertEqual(window.calls, [True])

    def test_connection_notifications_are_debounced_and_grouped(self):
        class _FakeWindow:
            def __init__(self):
                self._tray = _FakeTray()
                self.settings = _FakeSettings(tray_notifications=True)
                self._pending_connection_notification = None
                self._connection_notification_events = deque()
                self._connection_event_window_seconds = 20.0
                self._connection_notify_timer = _FakeTimer()

        window = _FakeWindow()
        HyperxWindow._send_connection_notification(window, True)
        HyperxWindow._send_connection_notification(window, False)
        HyperxWindow._send_connection_notification(window, True)
        HyperxWindow._flush_connection_notification(window)

        self.assertEqual(len(window._tray.messages), 1)
        title, _message, _icon, _timeout = window._tray.messages[0]
        self.assertIn("unstable", title.lower())

    def test_battery_notifications_are_grouped_and_rate_limited(self):
        class _FakeWindow:
            def __init__(self):
                self._tray = _FakeTray()
                self.settings = _FakeSettings(tray_notifications=True)
                self._pending_battery_notification = None
                self._battery_notification_last_sent = {}
                self._battery_notification_cooldown_seconds = 900.0
                self._battery_notify_timer = _FakeTimer()
                self.logs = []

            def _log(self, message):
                self.logs.append(message)

        window = _FakeWindow()
        HyperxWindow._queue_battery_notification(window, 10, 9)
        HyperxWindow._queue_battery_notification(window, 10, 8)
        HyperxWindow._flush_battery_notification(window)
        HyperxWindow._queue_battery_notification(window, 10, 7)
        HyperxWindow._flush_battery_notification(window)

        self.assertEqual(len(window._tray.messages), 1)
        self.assertIn("grouped alerts", window._tray.messages[0][1])

    def test_battery_threshold_prefers_most_severe_level(self):
        class _FakeWindow:
            _queue_battery_notification = HyperxWindow._queue_battery_notification

            def __init__(self):
                self._tray = _FakeTray()
                self.settings = _FakeSettings(tray_notifications=True)
                self.battery = 4
                self._battery_notified_levels = set()
                self._pending_battery_notification = None
                self._battery_notification_last_sent = {}
                self._battery_notification_cooldown_seconds = 900.0
                self._battery_notify_timer = _FakeTimer()
                self.logs = []

            def _log(self, message):
                self.logs.append(message)

        window = _FakeWindow()
        HyperxWindow._maybe_notify_battery(window)

        self.assertEqual(window._pending_battery_notification["threshold"], 5)
        self.assertEqual(window._pending_battery_notification["battery"], 4)


if __name__ == "__main__":
    unittest.main()
