import os
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    raise RuntimeError("PySide6 is required (python3-pyside6)") from exc

from .constants import Command, ConnectionStatus
from .device import HidIoError, HidUnavailable
from .device_service import DeviceOpenSignals, DeviceReader, DeviceService
from .settings_service import SettingsService
from .view import HyperxViewMixin, LogDialog


class HyperxWindow(HyperxViewMixin, QtWidgets.QWidget):
    def __init__(self, start_hidden=False, use_tray=True):
        super().__init__()
        self.setObjectName("rootWindow")
        self.setAutoFillBackground(True)
        self.setWindowTitle("HyperX Alpha v1.0.0")
        self.setMinimumSize(920, 640)
        self.resize(980, 700)

        self.base_dir = Path(__file__).resolve().parent
        self.icon_dir = self.base_dir / "assets" / "img"

        self._device_service = DeviceService()
        self._settings_service = SettingsService()
        self._reader = None
        self._opener_thread = None
        self._open_generation = 0
        self._open_signals = DeviceOpenSignals(self)
        self._open_signals.opened.connect(self._on_device_opened)
        self._open_signals.failed.connect(self._on_device_failed)
        self._device_ready = False
        self._shutting_down = False
        self._open_retry_timer = QtCore.QTimer(self)
        self._open_retry_timer.setInterval(3000)
        self._open_retry_timer.timeout.connect(self._start_device_open)
        self._reader_timeout_ms = 100
        self._last_open_error = None
        self._last_io_error = None
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(5000)
        self._poll_timer.timeout.connect(self._poll_headset)
        self._mic_state_probe_timer = QtCore.QTimer(self)
        self._mic_state_probe_timer.setSingleShot(True)
        self._mic_state_probe_timer.setInterval(1200)
        self._mic_state_probe_timer.timeout.connect(self._on_mic_state_probe_timeout)
        self._mic_state_reported = False

        self._updating_controls = False
        self._updating_settings = False
        self._updating_device_selection = False
        self._device_by_key = {}
        self._selected_device_key = None
        self._last_device_signature = ()
        self._last_device_scan_error = None
        self._device_hotplug_timer = QtCore.QTimer(self)
        self._device_hotplug_timer.setInterval(2500)
        self._device_hotplug_timer.timeout.connect(self._poll_device_hotplug)

        self.status = ConnectionStatus.DISCONNECTED
        self.battery = None
        self._battery_notified_levels = set()

        self._tray_available = False
        self._tray = None
        self._tray_menu = None
        self._tray_toggle_action = None
        self._tray_voice_action = None
        self._tray_mic_action = None
        self._tray_sleep_actions = {}
        self._updating_tray_controls = False
        self._tray_icons = {}

        self._connection_notify_timer = QtCore.QTimer(self)
        self._connection_notify_timer.setSingleShot(True)
        self._connection_notify_timer.setInterval(1800)
        self._connection_notify_timer.timeout.connect(
            self._flush_connection_notification
        )
        self._pending_connection_notification = None
        self._connection_notification_events = deque()
        self._connection_event_window_seconds = 20.0

        self._battery_notify_timer = QtCore.QTimer(self)
        self._battery_notify_timer.setSingleShot(True)
        self._battery_notify_timer.setInterval(1800)
        self._battery_notify_timer.timeout.connect(self._flush_battery_notification)
        self._pending_battery_notification = None
        self._battery_notification_cooldown_seconds = 900.0
        self._battery_notification_last_sent = {}

        self._log_buffer = []
        self._log_buffer_max = 1000
        self._log_dialog = None
        self._verbose_io_logs = os.environ.get("HYPERX_DEBUG_IO", "0") == "1"
        self._stdout_logs = os.environ.get("HYPERX_LOG_STDOUT", "0") == "1"

        self._theme_is_dark = False

        self.settings = self._settings_service.load()
        if self._settings_service.autostart_enabled() and not self.settings.start_in_tray:
            self.settings.start_in_tray = True
            self._settings_service.save(self.settings)

        self._build_ui()
        self._refresh_device_list(preferred_key=self.settings.selected_device_key)
        self._apply_selected_device(reconnect=False)
        self._apply_theme()
        self._device_hotplug_timer.start()

        if use_tray and QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_available = True
            self._init_tray()
        self._configure_minimize_action()

        if start_hidden and self._tray_available:
            self.hide()
        else:
            self.show()

        QtCore.QTimer.singleShot(0, self._start_device_open)

    def _log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self._log_buffer.append(line)
        if self._stdout_logs:
            print(line, flush=True)
        trimmed = False
        if len(self._log_buffer) > self._log_buffer_max:
            self._log_buffer = self._log_buffer[-self._log_buffer_max :]
            trimmed = True
        if self._log_dialog is not None:
            if trimmed:
                self._log_dialog.set_text("\n".join(self._log_buffer))
            else:
                self._log_dialog.append_line(line)

    def _format_packet(self, data):
        return " ".join(f"{byte:02X}" for byte in data)

    def _send_command(self, cmd, label=None):
        command_value = int(cmd)
        command_name = getattr(cmd, "name", None) or label or f"CMD_0x{command_value:08X}"
        if not self._device_ready:
            if self._verbose_io_logs:
                self._log(f"TX skipped (device not ready): {command_name}")
            return False
        if self._verbose_io_logs:
            self._log(f"TX {command_name} (0x{command_value:08X})")
        try:
            sent = self._device_service.send_command(cmd)
        except HidIoError as exc:
            self._handle_device_io_error(f"TX {command_name} failed: {exc}")
            return False
        if not sent:
            self._handle_device_io_error(
                f"TX {command_name} failed: device handle unavailable."
            )
            return False
        return True

    def _show_logs(self):
        if self._log_dialog is None:
            self._log_dialog = LogDialog(self)
        self._log_dialog.set_text("\n".join(self._log_buffer))
        self._log_dialog.show()
        self._log_dialog.raise_()

    def _show_error(self, title, message):
        self._log(f"Error: {message}")
        QtWidgets.QMessageBox.critical(self, title, message)

    def _on_scan_devices(self):
        preferred = self.device_combo.currentData()
        self._refresh_device_list(preferred_key=preferred)
        self._apply_selected_device(reconnect=True)

    @staticmethod
    def _device_signature(devices):
        return tuple(
            (
                item.key,
                item.path,
                item.vendor_id,
                item.product_id,
                item.serial_number,
            )
            for item in devices
        )

    def _list_compatible_devices(self, log_failures=True):
        try:
            devices = self._device_service.list_compatible_devices()
            return devices, None
        except HidUnavailable as exc:
            if log_failures:
                self._log(f"Device scan failed: {exc}")
            return [], str(exc)
        except (OSError, RuntimeError, ValueError) as exc:
            if log_failures:
                self._log(f"Device scan failed unexpectedly: {exc}")
            return [], str(exc)

    def _populate_device_list(self, devices, preferred_key=None):
        signature = self._device_signature(devices)
        self._last_device_signature = signature

        saved_key = self.settings.selected_device_key
        fallback_from_saved_key = False
        self._device_by_key = {item.key: item for item in devices}
        self._updating_device_selection = True
        self.device_combo.clear()
        if not devices:
            self.device_combo.addItem("No compatible headset found", None)
            self.device_combo.setEnabled(False)
            selected_key = None
        else:
            for item in devices:
                self.device_combo.addItem(item.display_name(), item.key)
            self.device_combo.setEnabled(True)
            target_key = (
                preferred_key
                or saved_key
                or (devices[0].key if devices else None)
            )
            index = self.device_combo.findData(target_key)
            if index < 0:
                fallback_from_saved_key = bool(saved_key) and target_key == saved_key
                index = 0
            self.device_combo.setCurrentIndex(index)
            selected_key = self.device_combo.currentData()
        self._updating_device_selection = False
        self._selected_device_key = selected_key
        if (
            fallback_from_saved_key
            and selected_key is not None
            and selected_key != saved_key
        ):
            self.settings.selected_device_key = selected_key
            if not self._settings_service.save(self.settings):
                self.settings.selected_device_key = saved_key
                self._log("Unable to persist fallback headset selection.")

    def _refresh_device_list(self, preferred_key=None):
        devices, _error = self._list_compatible_devices(log_failures=True)
        self._last_device_scan_error = None
        self._populate_device_list(devices, preferred_key=preferred_key)

    def _poll_device_hotplug(self):
        if self._shutting_down:
            return
        devices, error = self._list_compatible_devices(log_failures=False)
        if error is not None:
            if error != self._last_device_scan_error:
                self._last_device_scan_error = error
                self._log(f"Hotplug scan unavailable: {error}")
            return
        if self._last_device_scan_error is not None:
            self._last_device_scan_error = None
            self._log("Hotplug scan restored.")

        signature = self._device_signature(devices)
        if signature == self._last_device_signature:
            return

        previous_selected_key = self._selected_device_key
        preferred_key = self.settings.selected_device_key or previous_selected_key
        self._populate_device_list(devices, preferred_key=preferred_key)
        if self._selected_device_key != previous_selected_key:
            self._apply_selected_device(reconnect=True)

    def _on_device_selection_changed(self, _index):
        if self._updating_device_selection:
            return
        previous_runtime_key = self._selected_device_key
        selected_key = self.device_combo.currentData()
        if selected_key == previous_runtime_key:
            self._selected_device_key = selected_key
            return
        self._selected_device_key = selected_key
        self.settings.selected_device_key = selected_key
        if not self._settings_service.save(self.settings):
            self.settings.selected_device_key = previous_runtime_key
            rollback_index = self.device_combo.findData(previous_runtime_key)
            if rollback_index >= 0:
                self._updating_device_selection = True
                self.device_combo.setCurrentIndex(rollback_index)
                self._updating_device_selection = False
                self._selected_device_key = self.device_combo.currentData()
            else:
                self._selected_device_key = previous_runtime_key
            self._show_error("Settings Error", "Unable to save selected headset.")
            return
        self._apply_selected_device(reconnect=True)

    def _apply_selected_device(self, reconnect=False):
        selected = None
        if self._selected_device_key:
            selected = self._device_service.select_device(self._selected_device_key)
        if selected is None:
            self._device_service.set_default_target()
        if selected is not None:
            self._log(f"Selected headset: {selected.display_name()}")
        if reconnect:
            self._restart_device_connection("Reconnecting with selected headset.")

    def _restart_device_connection(self, reason=None):
        if reason:
            self._log(reason)
        self._device_ready = False
        self._mic_state_reported = False
        self._mic_state_probe_timer.stop()
        self._clear_pending_battery_notifications()
        self._stop_reader()
        self._device_service.close()
        self.status = ConnectionStatus.DISCONNECTED
        self.battery = None
        self._battery_notified_levels.clear()
        self._set_controls_enabled(False)
        self._set_tray_quick_controls_enabled(False)
        self._set_status_text()
        self._update_tray_icon()
        self._open_retry_timer.stop()
        QtCore.QTimer.singleShot(0, self._start_device_open)

    def _start_device_open(self):
        if self._device_ready or self._shutting_down:
            return
        if self._opener_thread is not None and self._opener_thread.is_alive():
            return
        self._open_generation += 1
        generation = self._open_generation
        self._opener_thread = threading.Thread(
            target=self._open_device_worker,
            args=(generation,),
            daemon=True,
            name="hyperxalpha-device-open",
        )
        self._opener_thread.start()

    def _open_device_worker(self, generation):
        try:
            self._device_service.open()
            self._open_signals.opened.emit(generation)
        except HidUnavailable as exc:
            self._open_signals.failed.emit(generation, str(exc))
        except (OSError, RuntimeError, ValueError) as exc:
            self._open_signals.failed.emit(generation, str(exc))
        except Exception as exc:
            self._open_signals.failed.emit(generation, f"Unexpected open error: {exc}")

    def _configure_minimize_action(self):
        if self._tray_available:
            self.min_button.setVisible(True)
            self.min_button.setEnabled(True)
            self.min_button.setToolTip("")
            return
        self.min_button.setVisible(False)
        self.min_button.setEnabled(False)
        self.min_button.setToolTip("System tray unavailable.")

    def _on_device_opened(self, generation):
        self._opener_thread = None
        if self._shutting_down or generation != self._open_generation:
            self._device_service.close()
            return
        self._device_ready = True
        self._last_open_error = None
        self._last_io_error = None
        self._open_retry_timer.stop()
        self._log("Device opened.")
        self._reader = DeviceReader(
            self._device_service,
            read_timeout_ms=self._reader_timeout_ms,
            parent=self,
        )
        self._reader.packet_received.connect(self._handle_packet)
        self._reader.io_failed.connect(self._on_reader_io_failed)
        self._reader.start()
        if not self._poll_timer.isActive():
            self._poll_timer.start()
        self._send_command(Command.CONNECTION_STATE)

    def _on_device_failed(self, generation, message):
        self._opener_thread = None
        if self._shutting_down or generation != self._open_generation:
            return
        self._device_ready = False
        self._mic_state_reported = False
        self._mic_state_probe_timer.stop()
        self._clear_pending_battery_notifications()
        self.status = ConnectionStatus.DISCONNECTED
        self.battery = None
        self._set_controls_enabled(False)
        self._set_tray_quick_controls_enabled(False)
        self._set_status_text()
        self._update_tray_icon()
        if message != self._last_open_error:
            self._last_open_error = message
            self._log(f"Device unavailable: {message}")
        if not self._open_retry_timer.isActive():
            self._open_retry_timer.start()

    def _on_reader_io_failed(self, message):
        self._handle_device_io_error(f"RX failed: {message}")

    def _handle_device_io_error(self, message):
        if self._shutting_down:
            return
        if message != self._last_io_error:
            self._last_io_error = message
            self._log(f"Device I/O error: {message}")

        was_connected = self.status == ConnectionStatus.CONNECTED
        self._device_ready = False
        self._mic_state_reported = False
        self._mic_state_probe_timer.stop()
        self._clear_pending_battery_notifications()
        self._stop_reader()
        self._device_service.close()
        self.status = ConnectionStatus.DISCONNECTED
        self.battery = None
        self._battery_notified_levels.clear()
        self._set_controls_enabled(False)
        self._set_tray_quick_controls_enabled(False)
        self._set_status_text()
        self._update_tray_icon()
        if was_connected:
            self._send_connection_notification(connected=False)
        self._poll_timer.setInterval(5000)
        if not self._poll_timer.isActive():
            self._poll_timer.start()
        if not self._open_retry_timer.isActive():
            self._open_retry_timer.start()

    def _init_tray(self):
        self._tray_icons = {
            name: QtGui.QIcon(str(self.icon_dir / f"{name}.png"))
            for name in ("traydc", "tray0", "tray20", "tray40", "tray60", "tray80", "tray100")
        }
        icon = self._tray_icons.get("traydc", QtGui.QIcon(str(self.icon_dir / "traydc.png")))
        self._tray = QtWidgets.QSystemTrayIcon(icon, self)
        self._tray_menu = QtWidgets.QMenu()
        self._tray_toggle_action = self._tray_menu.addAction("Hide")
        self._tray_toggle_action.triggered.connect(self._toggle_visible)

        self._tray_menu.addSeparator()
        self._tray_voice_action = self._tray_menu.addAction("Voice Prompt")
        self._tray_voice_action.setCheckable(True)
        self._tray_voice_action.toggled.connect(self._on_tray_voice_action_toggled)

        self._tray_mic_action = self._tray_menu.addAction("Mic Monitoring")
        self._tray_mic_action.setCheckable(True)
        self._tray_mic_action.toggled.connect(self._on_tray_mic_action_toggled)

        sleep_menu = self._tray_menu.addMenu("Sleep Timer")
        sleep_labels = ("10 Minutes", "20 Minutes", "30 Minutes")
        for index, label in enumerate(sleep_labels):
            action = sleep_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked, selected_index=index: self._on_tray_sleep_selected(
                    selected_index, checked
                )
            )
            self._tray_sleep_actions[index] = action

        self._tray_menu.addSeparator()
        self._tray_menu.addAction("Open Logs", self._show_logs)
        self._tray_menu.addSeparator()
        self._tray_menu.addAction("Quit", self.quit)
        self._tray.setContextMenu(self._tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setToolTip("HyperX Alpha")
        self._tray.show()
        self._set_tray_quick_controls_enabled(False)
        self._sync_tray_quick_controls_from_ui()

    def _set_tray_quick_controls_enabled(self, enabled):
        enabled = bool(enabled)
        for action in (self._tray_voice_action, self._tray_mic_action):
            if action is not None:
                action.setEnabled(enabled)
        for action in self._tray_sleep_actions.values():
            action.setEnabled(enabled)

    def _sync_tray_quick_controls_from_ui(self):
        if self._tray is None:
            return
        self._updating_tray_controls = True
        try:
            if self._tray_voice_action is not None:
                self._tray_voice_action.setChecked(self.voice_switch.isChecked())
            if self._tray_mic_action is not None:
                self._tray_mic_action.setChecked(self.mic_switch.isChecked())
            sleep_index = self.sleep_combo.currentIndex()
            for index, action in self._tray_sleep_actions.items():
                action.setChecked(index == sleep_index)
        finally:
            self._updating_tray_controls = False

    def _on_tray_voice_action_toggled(self, active):
        if self._updating_tray_controls:
            return
        if self.status != ConnectionStatus.CONNECTED or not self._device_ready:
            self._sync_tray_quick_controls_from_ui()
            return
        self._updating_controls = True
        self.voice_switch.setChecked(bool(active))
        self._updating_controls = False
        self._on_voice_toggle(bool(active))

    def _on_tray_mic_action_toggled(self, active):
        if self._updating_tray_controls:
            return
        if self.status != ConnectionStatus.CONNECTED or not self._device_ready:
            self._sync_tray_quick_controls_from_ui()
            return
        self._updating_controls = True
        self.mic_switch.setChecked(bool(active))
        self._updating_controls = False
        self._on_mic_toggle(bool(active))

    def _on_tray_sleep_selected(self, index, checked):
        if self._updating_tray_controls or not checked:
            return
        if self.status != ConnectionStatus.CONNECTED or not self._device_ready:
            self._sync_tray_quick_controls_from_ui()
            return
        if index not in (0, 1, 2):
            return
        self._updating_controls = True
        self.sleep_combo.setCurrentIndex(index)
        self._updating_controls = False
        self._on_sleep_changed(index)

    def _on_tray_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            self._toggle_visible()

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
        self._update_tray_menu_label()

    def _update_tray_menu_label(self):
        if self._tray_toggle_action is None:
            return
        self._tray_toggle_action.setText("Hide" if self.isVisible() else "Show")

    def closeEvent(self, event):
        if self._tray_available and not self._shutting_down:
            event.ignore()
            self.hide()
            self._update_tray_menu_label()
            return
        if not self._shutting_down:
            event.ignore()
            self.quit()
            return
        event.accept()

    def _stop_reader(self):
        if self._reader is None:
            return
        if self._reader.isRunning():
            self._reader.stop()
            if not self._reader.wait(1200):
                self._log("Reader thread did not stop before shutdown timeout.")
        self._reader = None

    def _stop_opener(self):
        self._open_generation += 1
        opener = self._opener_thread
        self._opener_thread = None
        if opener is not None and opener.is_alive():
            opener.join(0.05)

    def _on_minimize(self):
        if not self._tray_available:
            self.showMinimized()
            return
        self.hide()
        self._update_tray_menu_label()

    def _on_tray_toggle(self, _checked):
        if self._updating_settings:
            return
        previous = bool(self.settings.start_in_tray)
        enabled = self.tray_switch.isChecked()
        if enabled == previous:
            return
        self.settings.start_in_tray = enabled
        if not self._settings_service.save(self.settings):
            self.settings.start_in_tray = previous
            self._updating_settings = True
            self.tray_switch.setChecked(previous)
            self._updating_settings = False
            self._show_error("Settings Error", "Unable to save preferences.")
            return
        if not self._settings_service.set_autostart(enabled):
            self._updating_settings = True
            self.tray_switch.setChecked(previous)
            self._updating_settings = False
            self.settings.start_in_tray = previous
            if not self._settings_service.save(self.settings):
                self._log("Unable to roll back start-in-tray setting after autostart error.")
            self._show_error("Autostart Error", "Unable to update autostart entry.")

    def _on_theme_changed(self, _index):
        if self._updating_settings:
            return
        previous_mode = str(self.settings.theme_mode or "system")
        mode = self.theme_combo.currentData() or "system"
        if mode == previous_mode:
            return
        self.settings.theme_mode = mode
        if not self._settings_service.save(self.settings):
            self.settings.theme_mode = previous_mode
            previous_index = self.theme_combo.findData(previous_mode)
            if previous_index < 0:
                previous_index = self.theme_combo.findData("system")
            if previous_index >= 0:
                self._updating_settings = True
                self.theme_combo.setCurrentIndex(previous_index)
                self._updating_settings = False
            self._show_error("Settings Error", "Unable to save preferences.")
            return
        self._apply_theme()

    def _on_notifications_toggle(self, _checked):
        if self._updating_settings:
            return
        previous = bool(self.settings.tray_notifications)
        enabled = self.notify_switch.isChecked()
        if enabled == previous:
            return
        self.settings.tray_notifications = enabled
        if not self._settings_service.save(self.settings):
            self.settings.tray_notifications = previous
            self._updating_settings = True
            self.notify_switch.setChecked(previous)
            self._updating_settings = False
            self._show_error("Settings Error", "Unable to save preferences.")
            return
        if not enabled:
            self._clear_pending_connection_notifications()
            self._clear_pending_battery_notifications()
            return
        if enabled:
            self._maybe_notify_battery()

    def _on_sleep_changed(self, index):
        if self._updating_controls:
            return
        if index == 0:
            self._send_command(Command.SLEEP_TIMER_10)
        elif index == 1:
            self._send_command(Command.SLEEP_TIMER_20)
        elif index == 2:
            self._send_command(Command.SLEEP_TIMER_30)
        self._sync_tray_quick_controls_from_ui()

    def _on_voice_toggle(self, active):
        if self._updating_controls:
            return
        if active:
            self._send_command(Command.VOICE_PROMPTS)
        else:
            self._send_command(Command.VOICE_PROMPTS_OFF)
        self._sync_tray_quick_controls_from_ui()

    def _persist_mic_monitor_state(self, active):
        state = bool(active)
        if self.settings.mic_monitor_state == state:
            return
        self.settings.mic_monitor_state = state
        if not self._settings_service.save(self.settings):
            self._log("Unable to persist mic monitor state.")

    def _set_mic_monitor_state(self, active, persist=True):
        self._updating_controls = True
        self.mic_switch.setChecked(bool(active))
        self._updating_controls = False
        self._sync_tray_quick_controls_from_ui()
        if persist:
            self._persist_mic_monitor_state(active)

    def _on_mic_toggle(self, active):
        if self._updating_controls:
            return
        self._mic_state_reported = True
        self._mic_state_probe_timer.stop()
        if active:
            self._send_command(Command.MICROPHONE_MONITOR)
        else:
            self._send_command(Command.MICROPHONE_MONITOR_OFF)
        self._persist_mic_monitor_state(active)
        self._sync_tray_quick_controls_from_ui()

    def _request_feature_states(self):
        if not self._device_ready:
            return
        self._send_command(Command.STATUS_REQUEST)
        self._send_command(Command.SLEEP_STATE)
        self._send_command(Command.VOICE_STATE)
        self._request_mic_monitor_state()

    def _request_mic_monitor_state(self):
        if not self._device_ready:
            return
        self._mic_state_reported = False
        self._send_command(Command.MIC_MONITOR_STATE)
        self._mic_state_probe_timer.start()

    def _on_mic_state_probe_timeout(self):
        if not self._device_ready or self.status != ConnectionStatus.CONNECTED:
            return
        if self._mic_state_reported:
            return
        cached = self.settings.mic_monitor_state
        if cached is None:
            return
        self._set_mic_monitor_state(bool(cached), persist=False)

    def _poll_headset(self):
        if not self._device_ready:
            return
        if self.status != ConnectionStatus.CONNECTED:
            self._send_command(Command.CONNECTION_STATE)
            return
        self._send_command(Command.STATUS_REQUEST)
        self._send_command(Command.PING)

    def _maybe_notify_battery(self):
        if not self.settings.tray_notifications:
            return
        if self.battery is None:
            return
        thresholds = (20, 10, 5)
        for level in thresholds:
            if self.battery > level:
                self._battery_notified_levels.discard(level)
        threshold = None
        if self.battery <= 5:
            threshold = 5
        elif self.battery <= 10:
            threshold = 10
        elif self.battery <= 20:
            threshold = 20
        if threshold is None:
            return

        if threshold in self._battery_notified_levels:
            return
        for level in thresholds:
            if level >= threshold:
                self._battery_notified_levels.add(level)
        self._queue_battery_notification(threshold, self.battery)

    def _queue_battery_notification(self, threshold, battery_level):
        if self._tray is None:
            return
        if not self.settings.tray_notifications:
            return
        threshold = int(threshold)
        battery_level = int(battery_level)
        now = time.monotonic()
        last_sent = self._battery_notification_last_sent.get(threshold)
        if (
            last_sent is not None
            and now - last_sent < self._battery_notification_cooldown_seconds
        ):
            return

        if self._pending_battery_notification is None:
            self._pending_battery_notification = {
                "threshold": threshold,
                "battery": battery_level,
                "count": 1,
            }
        else:
            self._pending_battery_notification["threshold"] = min(
                int(self._pending_battery_notification["threshold"]),
                threshold,
            )
            self._pending_battery_notification["battery"] = min(
                int(self._pending_battery_notification["battery"]),
                battery_level,
            )
            self._pending_battery_notification["count"] = (
                int(self._pending_battery_notification["count"]) + 1
            )
        self._battery_notify_timer.start()

    def _flush_battery_notification(self):
        pending = self._pending_battery_notification
        self._pending_battery_notification = None
        if pending is None:
            return
        if self._tray is None or not self.settings.tray_notifications:
            return

        battery_level = int(pending["battery"])
        threshold = int(pending["threshold"])
        grouped_count = int(pending["count"])
        self._battery_notification_last_sent[threshold] = time.monotonic()
        message = f"Battery at {battery_level}%"
        if grouped_count > 1:
            message += " (grouped alerts)"
        self._tray.showMessage(
            "HyperX Alpha battery low",
            message,
            QtWidgets.QSystemTrayIcon.Warning,
            6000,
        )
        self._log(f"Battery notification sent ({battery_level}%).")

    def _send_connection_notification(self, connected):
        if self._tray is None:
            return
        if not self.settings.tray_notifications:
            return
        self._pending_connection_notification = bool(connected)
        now = time.monotonic()
        self._connection_notification_events.append((now, bool(connected)))
        cutoff = now - self._connection_event_window_seconds
        while (
            self._connection_notification_events
            and self._connection_notification_events[0][0] < cutoff
        ):
            self._connection_notification_events.popleft()
        self._connection_notify_timer.start()

    def _flush_connection_notification(self):
        connected = self._pending_connection_notification
        self._pending_connection_notification = None
        if connected is None:
            return
        if self._tray is None:
            return
        if not self.settings.tray_notifications:
            return
        now = time.monotonic()
        cutoff = now - self._connection_event_window_seconds
        while (
            self._connection_notification_events
            and self._connection_notification_events[0][0] < cutoff
        ):
            self._connection_notification_events.popleft()

        changes = len(self._connection_notification_events)
        if changes >= 3:
            title = "HyperX Alpha connection unstable"
            message = (
                f"{changes} connection changes in the last "
                f"{int(self._connection_event_window_seconds)}s. "
                f"Last state: {'connected' if connected else 'disconnected'}."
            )
            icon = QtWidgets.QSystemTrayIcon.Warning
            timeout_ms = 7000
        else:
            title = (
                "HyperX Alpha connected"
                if connected
                else "HyperX Alpha disconnected"
            )
            message = (
                "Headset connected and ready."
                if connected
                else "Headset disconnected."
            )
            icon = QtWidgets.QSystemTrayIcon.Information
            timeout_ms = 4500

        self._tray.showMessage(
            title,
            message,
            icon,
            timeout_ms,
        )

    def _clear_pending_connection_notifications(self):
        self._pending_connection_notification = None
        self._connection_notify_timer.stop()
        self._connection_notification_events.clear()

    def _clear_pending_battery_notifications(self):
        self._pending_battery_notification = None
        self._battery_notify_timer.stop()

    def _update_tray_icon(self):
        if self._tray is None:
            return
        if self.status != ConnectionStatus.CONNECTED or self.battery is None:
            icon_name = "traydc"
        elif self.battery <= 10:
            icon_name = "tray0"
        elif self.battery <= 30:
            icon_name = "tray20"
        elif self.battery <= 50:
            icon_name = "tray40"
        elif self.battery <= 70:
            icon_name = "tray60"
        elif self.battery <= 90:
            icon_name = "tray80"
        else:
            icon_name = "tray100"
        icon = self._tray_icons.get(icon_name)
        if icon is None:
            icon_path = self.icon_dir / f"{icon_name}.png"
            icon = QtGui.QIcon(str(icon_path))
            self._tray_icons[icon_name] = icon
        self._tray.setIcon(icon)
        self._tray.setToolTip(self._tray_tooltip())

    def _tray_tooltip(self):
        if self.status == ConnectionStatus.CONNECTED:
            if self.battery is None:
                return "Connected"
            hours = self.battery * 3
            return f"{hours} Hours Remaining ({self.battery}%)"
        return "Power Off"

    def _on_connect(self):
        was_connected = self.status == ConnectionStatus.CONNECTED
        self.status = ConnectionStatus.CONNECTED
        self._mic_state_reported = False
        self._set_controls_enabled(True)
        self._set_tray_quick_controls_enabled(True)
        self._sync_tray_quick_controls_from_ui()
        self._set_status_text()
        self._update_tray_icon()
        if not was_connected:
            self._log("Status: CONNECTED")
            self._send_connection_notification(connected=True)
            self._poll_timer.setInterval(30000)
            if not self._poll_timer.isActive():
                self._poll_timer.start()
            self._request_feature_states()

    def _on_disconnect(self):
        was_disconnected = self.status == ConnectionStatus.DISCONNECTED
        self.status = ConnectionStatus.DISCONNECTED
        self._set_controls_enabled(False)
        self._set_tray_quick_controls_enabled(False)
        self._set_status_text()
        self._update_tray_icon()
        if not was_disconnected:
            self._log("Status: DISCONNECTED")
            self._send_connection_notification(connected=False)
        self.battery = None
        self._battery_notified_levels.clear()
        self._clear_pending_battery_notifications()
        self._mic_state_reported = False
        self._mic_state_probe_timer.stop()
        self._poll_timer.setInterval(5000)
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    def _handle_packet(self, data):
        if self._verbose_io_logs:
            self._log(f"RX {self._format_packet(data)}")
        if len(data) < 4:
            return
        if data[0] != 0x21 or data[1] != 0xBB:
            return

        code = data[2]
        value = data[3]

        if code == 0x03:
            if value == 0x01:
                self._on_disconnect()
            elif value == 0x02:
                self._on_connect()
        elif code == 0x07:
            if self.status != ConnectionStatus.CONNECTED:
                return
            self._updating_controls = True
            if value == 0x0A:
                self.sleep_combo.setCurrentIndex(0)
            elif value == 0x14:
                self.sleep_combo.setCurrentIndex(1)
            elif value == 0x1E:
                self.sleep_combo.setCurrentIndex(2)
            self._updating_controls = False
            self._sync_tray_quick_controls_from_ui()
        elif code == 0x09:
            if self.status != ConnectionStatus.CONNECTED:
                return
            self._updating_controls = True
            self.voice_switch.setChecked(value == 0x01)
            self._updating_controls = False
            self._sync_tray_quick_controls_from_ui()
        elif code == 0x0A:
            if self.status != ConnectionStatus.CONNECTED:
                return
            if value in (0x00, 0x01):
                self._mic_state_reported = True
                self._mic_state_probe_timer.stop()
                self._set_mic_monitor_state(value == 0x01)
        elif code == 0x0B:
            if self.status != ConnectionStatus.CONNECTED:
                return
            if not 0 <= value <= 100:
                self._log(f"Ignoring invalid battery value from headset: {value}")
                return
            self.battery = value
            self._set_status_text()
            self._update_tray_icon()
            self._maybe_notify_battery()
        elif code == 0x12:
            if self.status != ConnectionStatus.CONNECTED:
                return
            self._updating_controls = True
            if value == 0x0A:
                self.sleep_combo.setCurrentIndex(0)
            elif value == 0x14:
                self.sleep_combo.setCurrentIndex(1)
            elif value == 0x1E:
                self.sleep_combo.setCurrentIndex(2)
            self._updating_controls = False
            self._sync_tray_quick_controls_from_ui()
        elif code == 0x13:
            if self.status != ConnectionStatus.CONNECTED:
                return
            self._updating_controls = True
            self.voice_switch.setChecked(value == 0x01)
            self._updating_controls = False
            self._sync_tray_quick_controls_from_ui()
        elif code == 0x22:
            if self.status != ConnectionStatus.CONNECTED:
                return
            self._mic_state_reported = True
            self._mic_state_probe_timer.stop()
            self._set_mic_monitor_state(value > 0)
        elif code == 0x24:
            if value == 0x01:
                self._on_disconnect()
            elif value == 0x02:
                self._on_connect()

    def quit(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        self._poll_timer.stop()
        self._device_hotplug_timer.stop()
        self._mic_state_probe_timer.stop()
        self._open_retry_timer.stop()
        self._clear_pending_connection_notifications()
        self._clear_pending_battery_notifications()
        self._stop_reader()
        self._stop_opener()
        self._device_service.close()
        if self._tray is not None:
            self._tray.hide()
        QtWidgets.QApplication.quit()


def run(start_hidden=False, use_tray=True):
    if sys.platform.startswith("linux") and os.environ.get("HYPERX_FORCE_SOFTWARE_OPENGL") == "1":
        os.environ.setdefault("QT_OPENGL", "software")
        os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseSoftwareOpenGL)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False if use_tray else True)
    win = HyperxWindow(start_hidden=start_hidden, use_tray=use_tray)
    app.exec()
    return win
