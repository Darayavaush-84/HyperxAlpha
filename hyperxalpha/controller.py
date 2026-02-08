import os
import queue
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

from . import APP_NAME, app_display_name
from .constants import Command, ConnectionStatus
from .device import HidIoError, HidUnavailable
from .device_service import DeviceOpenSignals, DeviceReader, DeviceService
from .settings_service import SettingsService
from .view import HyperxViewMixin, LogDialog


OPEN_RETRY_INTERVAL_MS = 3000
POLL_INTERVAL_DISCONNECTED_MS = 5000
POLL_INTERVAL_CONNECTED_MS = 30000
MIC_STATE_PROBE_TIMEOUT_MS = 1200
DEVICE_HOTPLUG_INTERVAL_MS = 2500
CONNECTION_NOTIFY_DEBOUNCE_MS = 1800
BATTERY_NOTIFY_DEBOUNCE_MS = 1800
TRANSIENT_TX_FAILURE_LIMIT = 2
TX_TIMEOUT_BACKOFF_INITIAL_MS = 4000
TX_TIMEOUT_BACKOFF_MAX_MS = 60000
TX_QUEUE_MAX_PENDING = 64
LOG_DIALOG_FLUSH_INTERVAL_MS = 120
TX_TIMEOUT_LOG_MIN_INTERVAL_SECONDS = 20.0
TX_QUEUE_FULL_LOG_MIN_INTERVAL_SECONDS = 4.0
LOG_LEVEL_INFO = "INFO"
LOG_LEVEL_WARN = "WARN"
LOG_LEVEL_DEBUG = "DEBUG"
LOG_LEVELS = (LOG_LEVEL_INFO, LOG_LEVEL_WARN, LOG_LEVEL_DEBUG)
TX_TIMEOUT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "connection timed out",
    "operation timed out",
    "resource temporarily unavailable",
    "would block",
    "resource busy",
)


class DeviceTxSignals(QtCore.QObject):
    completed = QtCore.Signal(int, str, bool, bool, str)


class HyperxWindow(HyperxViewMixin, QtWidgets.QWidget):
    def __init__(self, start_hidden=False, use_tray=True):
        super().__init__()
        self.setObjectName("rootWindow")
        self.setAutoFillBackground(True)
        self.setWindowTitle(app_display_name())
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
        self._open_retry_timer.setInterval(OPEN_RETRY_INTERVAL_MS)
        self._open_retry_timer.timeout.connect(self._start_device_open)
        self._reader_timeout_ms = 100
        self._last_open_error = None
        self._last_io_error = None
        self._transient_tx_failures = 0
        self._tx_timeout_backoff_ms = 0
        self._tx_suspended_until = 0.0
        self._control_channel_busy = False
        self._tx_session_id = 0
        self._tx_queue = queue.Queue(maxsize=TX_QUEUE_MAX_PENDING)
        self._tx_worker_stop = threading.Event()
        self._tx_worker = None
        self._tx_signals = DeviceTxSignals(self)
        self._tx_signals.completed.connect(self._on_tx_command_completed)
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_DISCONNECTED_MS)
        self._poll_timer.timeout.connect(self._poll_headset)
        self._mic_state_probe_timer = QtCore.QTimer(self)
        self._mic_state_probe_timer.setSingleShot(True)
        self._mic_state_probe_timer.setInterval(MIC_STATE_PROBE_TIMEOUT_MS)
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
        self._device_hotplug_timer.setInterval(DEVICE_HOTPLUG_INTERVAL_MS)
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
        self._connection_notify_timer.setInterval(CONNECTION_NOTIFY_DEBOUNCE_MS)
        self._connection_notify_timer.timeout.connect(
            self._flush_connection_notification
        )
        self._pending_connection_notification = None
        self._connection_notification_events = deque()
        self._connection_event_window_seconds = 20.0

        self._battery_notify_timer = QtCore.QTimer(self)
        self._battery_notify_timer.setSingleShot(True)
        self._battery_notify_timer.setInterval(BATTERY_NOTIFY_DEBOUNCE_MS)
        self._battery_notify_timer.timeout.connect(self._flush_battery_notification)
        self._pending_battery_notification = None
        self._battery_notification_cooldown_seconds = 900.0
        self._battery_notification_last_sent = {}

        self._log_buffer_max = 1000
        self._log_entries = deque(maxlen=self._log_buffer_max)
        self._log_dialog = None
        self._log_pending_entries = deque()
        self._log_dialog_snapshot_needed = False
        self._log_flush_timer = QtCore.QTimer(self)
        self._log_flush_timer.setSingleShot(True)
        self._log_flush_timer.setInterval(LOG_DIALOG_FLUSH_INTERVAL_MS)
        self._log_flush_timer.timeout.connect(self._flush_log_dialog_updates)
        self._repeating_log_state = {}
        self._timeout_tx_failures = 0
        self._verbose_io_logs = os.environ.get("HYPERX_DEBUG_IO", "0") == "1"
        self._stdout_logs = os.environ.get("HYPERX_LOG_STDOUT", "0") == "1"

        self._theme_is_dark = False
        self._packet_handlers = {
            0x03: self._handle_connection_state_packet,
            0x07: self._handle_sleep_state_packet,
            0x09: self._handle_voice_state_packet,
            0x0A: self._handle_mic_monitor_state_packet,
            0x0B: self._handle_battery_state_packet,
            0x12: self._handle_sleep_state_packet,
            0x13: self._handle_voice_state_packet,
            0x22: self._handle_mic_monitor_feedback_packet,
            0x24: self._handle_connection_state_packet,
        }

        self.settings = self._settings_service.load()
        autostart_active = self._settings_service.autostart_enabled()
        if self.settings.start_on_login != autostart_active:
            self.settings.start_on_login = autostart_active
            self._settings_service.save(self.settings)

        self._start_tx_worker()
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

    @staticmethod
    def _normalize_log_level(level):
        normalized = str(level or LOG_LEVEL_INFO).strip().upper()
        if normalized in LOG_LEVELS:
            return normalized
        return LOG_LEVEL_INFO

    @staticmethod
    def _emit_log(target, message, level=LOG_LEVEL_INFO):
        log_fn = getattr(target, "_log", None)
        if log_fn is None:
            return
        try:
            log_fn(message, level=level)
        except TypeError:
            log_fn(message)

    def _log(self, message, level=LOG_LEVEL_INFO):
        level = self._normalize_log_level(level)
        text = str(message)
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{level}] {text}"
        buffer_was_full = len(self._log_entries) >= self._log_buffer_max
        self._log_entries.append(
            {
                "timestamp": timestamp,
                "level": level,
                "message": text,
            }
        )
        if self._stdout_logs:
            print(line, flush=True)
        if self._log_dialog is not None:
            if buffer_was_full:
                self._log_dialog_snapshot_needed = True
            else:
                self._log_pending_entries.append(
                    {
                        "timestamp": timestamp,
                        "level": level,
                        "message": text,
                    }
                )
            if not self._log_flush_timer.isActive():
                self._log_flush_timer.start()

    def _flush_log_dialog_updates(self):
        if self._log_dialog is None:
            self._log_pending_entries.clear()
            self._log_dialog_snapshot_needed = False
            return
        if self._log_dialog_snapshot_needed:
            self._log_dialog_snapshot_needed = False
            self._log_pending_entries.clear()
            self._log_dialog.set_entries(list(self._log_entries))
            return
        if not self._log_pending_entries:
            return
        entries = list(self._log_pending_entries)
        self._log_pending_entries.clear()
        self._log_dialog.append_entries(entries)

    def _consume_repeating_log_suppressed(self, key):
        state = self._repeating_log_state.get(str(key))
        if state is None:
            return 0
        suppressed = int(state.get("suppressed", 0))
        state["suppressed"] = 0
        state["last_emit"] = 0.0
        return suppressed

    def _log_repeating(
        self,
        key,
        message,
        min_interval_seconds,
        level=LOG_LEVEL_INFO,
    ):
        key = str(key)
        now = time.monotonic()
        state = self._repeating_log_state.setdefault(
            key,
            {"last_emit": 0.0, "suppressed": 0},
        )
        last_emit = float(state.get("last_emit", 0.0))
        min_interval_seconds = max(0.0, float(min_interval_seconds))
        if last_emit <= 0.0 or now - last_emit >= min_interval_seconds:
            suppressed = int(state.get("suppressed", 0))
            if suppressed > 0:
                HyperxWindow._emit_log(
                    self,
                    f"{message} (suppressed {suppressed} similar events)",
                    level=level,
                )
            else:
                HyperxWindow._emit_log(self, message, level=level)
            state["last_emit"] = now
            state["suppressed"] = 0
            return True
        state["suppressed"] = int(state.get("suppressed", 0)) + 1
        return False

    def _format_packet(self, data):
        return " ".join(f"{byte:02X}" for byte in data)

    def _start_tx_worker(self):
        worker = self._tx_worker
        if worker is not None and worker.is_alive():
            return
        self._tx_worker_stop.clear()
        self._tx_worker = threading.Thread(
            target=self._tx_worker_loop,
            daemon=True,
            name="hyperxalpha-device-tx",
        )
        self._tx_worker.start()

    def _stop_tx_worker(self):
        worker = self._tx_worker
        if worker is None:
            return
        self._tx_worker_stop.set()
        try:
            self._tx_queue.put_nowait(None)
        except queue.Full:
            self._drain_tx_queue()
            try:
                self._tx_queue.put_nowait(None)
            except queue.Full:
                pass
        if worker.is_alive():
            worker.join(0.2)
        self._tx_worker = None

    def _drain_tx_queue(self):
        queue_ref = getattr(self, "_tx_queue", None)
        if queue_ref is None:
            return
        while True:
            try:
                queue_ref.get_nowait()
            except queue.Empty:
                return

    def _invalidate_tx_session(self):
        self._tx_session_id += 1
        self._drain_tx_queue()

    def _tx_worker_loop(self):
        while not self._tx_worker_stop.is_set():
            try:
                item = self._tx_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            session_id, cmd, command_name, allow_transient_failure = item
            sent = False
            error_message = ""
            try:
                sent = bool(self._device_service.send_command(cmd))
            except HidIoError as exc:
                error_message = str(exc)
            except Exception as exc:
                error_message = f"Unexpected send error: {exc}"
            self._tx_signals.completed.emit(
                int(session_id),
                str(command_name),
                bool(allow_transient_failure),
                bool(sent),
                str(error_message),
            )

    @staticmethod
    def _is_timeout_io_error(error):
        text = str(error).strip().lower()
        if not text:
            return False
        return any(marker in text for marker in TX_TIMEOUT_ERROR_MARKERS)

    def _record_transient_tx_failure(self, message):
        self._transient_tx_failures += 1
        HyperxWindow._emit_log(
            self,
            "Device I/O transient error "
            f"({self._transient_tx_failures} consecutive, "
            f"threshold {TRANSIENT_TX_FAILURE_LIMIT}): {message}",
            level=LOG_LEVEL_DEBUG,
        )

    def _set_control_channel_busy(self, busy):
        busy = bool(busy)
        if self._control_channel_busy == busy:
            return
        self._control_channel_busy = busy
        self._sync_control_availability()
        self._set_status_text()
        self._update_tray_icon()

    def _clear_tx_timeout_backoff(self):
        suppressed = self._consume_repeating_log_suppressed("tx-timeout")
        was_busy = (
            self._control_channel_busy
            or self._tx_timeout_backoff_ms > 0
            or self._timeout_tx_failures > 0
        )
        self._tx_timeout_backoff_ms = 0
        self._tx_suspended_until = 0.0
        self._timeout_tx_failures = 0
        self._set_control_channel_busy(False)
        if was_busy and suppressed > 0:
            self._log(
                "Control channel recovered "
                f"(suppressed {suppressed} similar timeout events)."
            )

    def _apply_tx_timeout_backoff(self, command_name):
        if self._tx_timeout_backoff_ms <= 0:
            next_backoff_ms = TX_TIMEOUT_BACKOFF_INITIAL_MS
        else:
            next_backoff_ms = min(
                self._tx_timeout_backoff_ms * 2,
                TX_TIMEOUT_BACKOFF_MAX_MS,
            )
        self._tx_timeout_backoff_ms = int(next_backoff_ms)
        self._tx_suspended_until = (
            time.monotonic() + (float(self._tx_timeout_backoff_ms) / 1000.0)
        )
        self._set_control_channel_busy(True)
        return max(1, self._tx_timeout_backoff_ms // 1000)

    def _record_timeout_tx_failure(self, command_name, message):
        self._timeout_tx_failures += 1
        backoff_seconds = self._apply_tx_timeout_backoff(command_name)
        self._log_repeating(
            "tx-timeout",
            "Device I/O transient error "
            f"(timeout #{self._timeout_tx_failures}): {message}; "
            "control channel busy, "
            f"pausing telemetry polling for {backoff_seconds}s "
            f"(last command: {command_name}).",
            TX_TIMEOUT_LOG_MIN_INTERVAL_SECONDS,
            level=LOG_LEVEL_DEBUG,
        )

    def _process_tx_result(
        self,
        command_name,
        allow_transient_failure,
        *,
        sent,
        io_error=None,
    ):
        if io_error:
            message = f"TX {command_name} failed: {io_error}"
            timeout_error = HyperxWindow._is_timeout_io_error(io_error)
            if allow_transient_failure or timeout_error:
                if timeout_error:
                    self._transient_tx_failures = 0
                    self._record_timeout_tx_failure(command_name, message)
                    return False
                self._record_transient_tx_failure(message)
                if self._transient_tx_failures >= TRANSIENT_TX_FAILURE_LIMIT:
                    self._transient_tx_failures = 0
                    self._handle_device_io_error(message)
            else:
                self._transient_tx_failures = 0
                self._handle_device_io_error(message)
            return False

        if sent:
            self._transient_tx_failures = 0
            self._clear_tx_timeout_backoff()
            return True

        message = f"TX {command_name} failed: device handle unavailable."
        if allow_transient_failure:
            self._record_transient_tx_failure(message)
            if self._transient_tx_failures >= TRANSIENT_TX_FAILURE_LIMIT:
                self._transient_tx_failures = 0
                self._handle_device_io_error(message)
            return False

        self._transient_tx_failures = 0
        self._handle_device_io_error(message)
        return False

    def _send_command_sync(self, cmd, command_name, allow_transient_failure):
        try:
            sent = self._device_service.send_command(cmd)
        except HidIoError as exc:
            return HyperxWindow._process_tx_result(
                self,
                command_name,
                allow_transient_failure,
                sent=False,
                io_error=str(exc),
            )
        except Exception as exc:
            return HyperxWindow._process_tx_result(
                self,
                command_name,
                allow_transient_failure,
                sent=False,
                io_error=f"Unexpected send error: {exc}",
            )
        return HyperxWindow._process_tx_result(
            self,
            command_name,
            allow_transient_failure,
            sent=bool(sent),
            io_error=None,
        )

    def _on_tx_command_completed(
        self,
        session_id,
        command_name,
        allow_transient_failure,
        sent,
        error_message,
    ):
        if self._shutting_down:
            return
        if int(session_id) != int(self._tx_session_id):
            return
        HyperxWindow._process_tx_result(
            self,
            command_name,
            bool(allow_transient_failure),
            sent=bool(sent),
            io_error=(str(error_message) or None),
        )

    def _send_command(self, cmd, label=None, allow_transient_failure=False):
        command_value = int(cmd)
        command_name = getattr(cmd, "name", None) or label or f"CMD_0x{command_value:08X}"
        if not self._device_ready:
            if self._verbose_io_logs:
                HyperxWindow._emit_log(
                    self,
                    f"TX skipped (device not ready): {command_name}",
                    level=LOG_LEVEL_DEBUG,
                )
            return False
        if self._tx_suspended_until > 0.0 and time.monotonic() < self._tx_suspended_until:
            if self._verbose_io_logs:
                HyperxWindow._emit_log(
                    self,
                    f"TX skipped (control channel busy): {command_name}",
                    level=LOG_LEVEL_DEBUG,
                )
            return False
        if self._verbose_io_logs:
            HyperxWindow._emit_log(
                self,
                f"TX {command_name} (0x{command_value:08X})",
                level=LOG_LEVEL_DEBUG,
            )

        tx_queue = getattr(self, "_tx_queue", None)
        if tx_queue is None:
            return HyperxWindow._send_command_sync(
                self,
                cmd,
                command_name,
                bool(allow_transient_failure),
            )
        try:
            tx_queue.put_nowait(
                (
                    int(self._tx_session_id),
                    cmd,
                    command_name,
                    bool(allow_transient_failure),
                )
            )
            return True
        except queue.Full:
            self._log_repeating(
                "tx-queue-full",
                f"TX queue full, dropping command: {command_name}",
                TX_QUEUE_FULL_LOG_MIN_INTERVAL_SECONDS,
                level=LOG_LEVEL_WARN,
            )
            return False

    def _show_logs(self):
        if self._log_dialog is None:
            self._log_dialog = LogDialog(self)
        self._log_flush_timer.stop()
        self._log_pending_entries.clear()
        self._log_dialog_snapshot_needed = False
        self._log_dialog.set_entries(list(self._log_entries))
        self._log_dialog.show()
        self._log_dialog.raise_()

    def _show_error(self, title, message):
        HyperxWindow._emit_log(self, f"Error: {message}", level=LOG_LEVEL_WARN)
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
                HyperxWindow._emit_log(
                    self,
                    f"Device scan failed: {exc}",
                    level=LOG_LEVEL_WARN,
                )
            return [], str(exc)
        except (OSError, RuntimeError, ValueError) as exc:
            if log_failures:
                HyperxWindow._emit_log(
                    self,
                    f"Device scan failed unexpectedly: {exc}",
                    level=LOG_LEVEL_WARN,
                )
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
                HyperxWindow._emit_log(
                    self,
                    f"Hotplug scan unavailable: {error}",
                    level=LOG_LEVEL_WARN,
                )
            return
        if self._last_device_scan_error is not None:
            self._last_device_scan_error = None
            HyperxWindow._emit_log(
                self,
                "Hotplug scan restored.",
                level=LOG_LEVEL_INFO,
            )

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
        self._invalidate_tx_session()
        self._device_ready = False
        self._stop_reader()
        self._device_service.close()
        self._apply_disconnected_state(clear_battery_history=True)
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
        self._sync_control_availability()
        self._last_open_error = None
        self._last_io_error = None
        self._transient_tx_failures = 0
        self._clear_tx_timeout_backoff()
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
        self._send_command(Command.CONNECTION_STATE, allow_transient_failure=True)

    def _on_device_failed(self, generation, message):
        self._opener_thread = None
        if self._shutting_down or generation != self._open_generation:
            return
        self._invalidate_tx_session()
        self._device_ready = False
        self._apply_disconnected_state()
        if message != self._last_open_error:
            self._last_open_error = message
            HyperxWindow._emit_log(
                self,
                f"Device unavailable: {message}",
                level=LOG_LEVEL_WARN,
            )
        if not self._open_retry_timer.isActive():
            self._open_retry_timer.start()

    def _on_reader_io_failed(self, message):
        self._handle_device_io_error(f"RX failed: {message}")

    def _handle_device_io_error(self, message):
        if self._shutting_down:
            return
        if message != self._last_io_error:
            self._last_io_error = message
            HyperxWindow._emit_log(
                self,
                f"Device I/O error: {message}",
                level=LOG_LEVEL_WARN,
            )
        self._invalidate_tx_session()
        self._transient_tx_failures = 0
        self._clear_tx_timeout_backoff()
        self._device_ready = False
        self._stop_reader()
        self._device_service.close()
        self._apply_disconnected_state(
            notify_status_change=True,
            clear_battery_history=True,
            poll_interval_ms=POLL_INTERVAL_DISCONNECTED_MS,
        )
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

    def _set_tray_quick_controls_enabled(
        self,
        enabled=None,
        *,
        voice_enabled=None,
        mic_enabled=None,
        sleep_enabled=None,
    ):
        if enabled is not None:
            voice_state = bool(enabled)
            mic_state = bool(enabled)
            sleep_state = bool(enabled)
        else:
            voice_state = bool(voice_enabled)
            mic_state = bool(mic_enabled)
            sleep_state = bool(sleep_enabled)

        if self._tray_voice_action is not None:
            self._tray_voice_action.setEnabled(voice_state)
        if self._tray_mic_action is not None:
            self._tray_mic_action.setEnabled(mic_state)
        for action in self._tray_sleep_actions.values():
            action.setEnabled(sleep_state)

    def _can_use_realtime_controls(self):
        return bool(
            self._device_ready
            and self.status == ConnectionStatus.CONNECTED
            and not self._control_channel_busy
        )

    def _can_use_sleep_controls(self):
        return bool(
            self._device_ready
            and self.status == ConnectionStatus.CONNECTED
            and not self._control_channel_busy
        )

    def _sync_control_availability(self):
        realtime_enabled = self._can_use_realtime_controls()
        sleep_enabled = self._can_use_sleep_controls()
        self._set_controls_enabled(
            sleep_enabled=sleep_enabled,
            voice_enabled=realtime_enabled,
            mic_enabled=realtime_enabled,
        )
        self._set_tray_quick_controls_enabled(
            voice_enabled=realtime_enabled,
            mic_enabled=realtime_enabled,
            sleep_enabled=sleep_enabled,
        )

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
        if not self._can_use_realtime_controls():
            self._sync_tray_quick_controls_from_ui()
            return
        self._updating_controls = True
        self.voice_switch.setChecked(bool(active))
        self._updating_controls = False
        self._on_voice_toggle(bool(active))

    def _on_tray_mic_action_toggled(self, active):
        if self._updating_tray_controls:
            return
        if not self._can_use_realtime_controls():
            self._sync_tray_quick_controls_from_ui()
            return
        self._updating_controls = True
        self.mic_switch.setChecked(bool(active))
        self._updating_controls = False
        self._on_mic_toggle(bool(active))

    def _on_tray_sleep_selected(self, index, checked):
        if self._updating_tray_controls or not checked:
            return
        if not self._can_use_sleep_controls():
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

    def _on_start_on_login_toggle(self, _checked):
        if self._updating_settings:
            return
        previous = bool(self.settings.start_on_login)
        enabled = self.start_on_login_switch.isChecked()
        if enabled == previous:
            return
        self.settings.start_on_login = enabled
        if not self._settings_service.save(self.settings):
            self.settings.start_on_login = previous
            self._updating_settings = True
            self.start_on_login_switch.setChecked(previous)
            self._updating_settings = False
            self._show_error("Settings Error", "Unable to save preferences.")
            return
        if not self._settings_service.set_autostart(
            enabled,
            start_hidden=self.settings.start_hidden,
        ):
            self._updating_settings = True
            self.start_on_login_switch.setChecked(previous)
            self._updating_settings = False
            self.settings.start_on_login = previous
            if not self._settings_service.save(self.settings):
                self._log("Unable to roll back start-on-login setting after autostart error.")
            self._show_error("Autostart Error", "Unable to update autostart entry.")

    def _on_start_hidden_toggle(self, _checked):
        if self._updating_settings:
            return
        previous = bool(self.settings.start_hidden)
        enabled = self.start_hidden_switch.isChecked()
        if enabled == previous:
            return
        self.settings.start_hidden = enabled
        if not self._settings_service.save(self.settings):
            self.settings.start_hidden = previous
            self._updating_settings = True
            self.start_hidden_switch.setChecked(previous)
            self._updating_settings = False
            self._show_error("Settings Error", "Unable to save preferences.")
            return
        if not self.settings.start_on_login:
            return
        if not self._settings_service.set_autostart(
            True,
            start_hidden=enabled,
        ):
            self._updating_settings = True
            self.start_hidden_switch.setChecked(previous)
            self._updating_settings = False
            self.settings.start_hidden = previous
            if not self._settings_service.save(self.settings):
                self._log(
                    "Unable to roll back start-hidden setting after autostart update error."
                )
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

    def _apply_saved_mic_monitor_preference(self):
        cached = self.settings.mic_monitor_state
        if cached is None:
            return
        desired_active = bool(cached)
        self._set_mic_monitor_state(desired_active, persist=False)
        if desired_active:
            self._send_command(Command.MICROPHONE_MONITOR)
        else:
            self._send_command(Command.MICROPHONE_MONITOR_OFF)

    def _handle_reported_mic_monitor_state(self, reported_active):
        cached = self.settings.mic_monitor_state
        if cached is None:
            self._set_mic_monitor_state(bool(reported_active), persist=False)
            return
        desired_active = bool(cached)
        self._set_mic_monitor_state(desired_active, persist=False)
        if bool(reported_active) != desired_active:
            if desired_active:
                self._send_command(Command.MICROPHONE_MONITOR)
            else:
                self._send_command(Command.MICROPHONE_MONITOR_OFF)

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
        self._send_command(Command.STATUS_REQUEST, allow_transient_failure=True)
        self._send_command(Command.SLEEP_STATE, allow_transient_failure=True)
        self._send_command(Command.VOICE_STATE, allow_transient_failure=True)
        self._request_mic_monitor_state()

    def _request_mic_monitor_state(self):
        if not self._device_ready:
            return
        self._mic_state_reported = False
        self._send_command(Command.MIC_MONITOR_STATE, allow_transient_failure=True)
        self._mic_state_probe_timer.start()

    def _on_mic_state_probe_timeout(self):
        if not self._device_ready or self.status != ConnectionStatus.CONNECTED:
            return
        if self._mic_state_reported:
            return
        self._apply_saved_mic_monitor_preference()

    def _poll_headset(self):
        if not self._device_ready:
            return
        if self.status != ConnectionStatus.CONNECTED:
            self._send_command(Command.CONNECTION_STATE, allow_transient_failure=True)
            return
        self._send_command(Command.STATUS_REQUEST, allow_transient_failure=True)
        self._send_command(Command.PING, allow_transient_failure=True)

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
        if self._control_channel_busy and self._device_ready:
            return "Headset detected (control channel busy)"
        if self.status == ConnectionStatus.CONNECTED:
            if self.battery is None:
                return "Connected"
            hours = self.battery * 3
            return f"{hours} Hours Remaining ({self.battery}%)"
        return "Power Off"

    def _apply_disconnected_state(
        self,
        *,
        notify_status_change=False,
        log_status_change=False,
        clear_battery_history=False,
        poll_interval_ms=None,
    ):
        was_disconnected = self.status == ConnectionStatus.DISCONNECTED
        self.status = ConnectionStatus.DISCONNECTED
        self._set_control_channel_busy(False)
        self._sync_control_availability()
        self._set_status_text()
        self._update_tray_icon()
        if log_status_change and not was_disconnected:
            self._log("Status: DISCONNECTED")
        if notify_status_change and not was_disconnected:
            self._send_connection_notification(connected=False)
        self.battery = None
        if clear_battery_history:
            self._battery_notified_levels.clear()
        self._clear_pending_battery_notifications()
        self._mic_state_reported = False
        self._mic_state_probe_timer.stop()
        if poll_interval_ms is not None:
            self._poll_timer.setInterval(int(poll_interval_ms))
            if not self._poll_timer.isActive():
                self._poll_timer.start()

    def _on_connect(self):
        was_connected = self.status == ConnectionStatus.CONNECTED
        self.status = ConnectionStatus.CONNECTED
        self._mic_state_reported = False
        self._sync_control_availability()
        self._sync_tray_quick_controls_from_ui()
        self._set_status_text()
        self._update_tray_icon()
        if not was_connected:
            self._log("Status: CONNECTED")
            self._send_connection_notification(connected=True)
            self._poll_timer.setInterval(POLL_INTERVAL_CONNECTED_MS)
            if not self._poll_timer.isActive():
                self._poll_timer.start()
            self._apply_saved_mic_monitor_preference()
            self._request_feature_states()

    def _on_disconnect(self):
        self._apply_disconnected_state(
            notify_status_change=True,
            log_status_change=True,
            clear_battery_history=True,
            poll_interval_ms=POLL_INTERVAL_DISCONNECTED_MS,
        )

    def _handle_connection_state_packet(self, value):
        if value == 0x01:
            self._on_disconnect()
        elif value == 0x02:
            self._on_connect()

    def _handle_sleep_state_packet(self, value):
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

    def _handle_voice_state_packet(self, value):
        if self.status != ConnectionStatus.CONNECTED:
            return
        self._updating_controls = True
        self.voice_switch.setChecked(value == 0x01)
        self._updating_controls = False
        self._sync_tray_quick_controls_from_ui()

    def _handle_mic_monitor_state_packet(self, value):
        if self.status != ConnectionStatus.CONNECTED:
            return
        if value in (0x00, 0x01):
            self._mic_state_reported = True
            self._mic_state_probe_timer.stop()
            self._handle_reported_mic_monitor_state(value == 0x01)

    def _handle_battery_state_packet(self, value):
        if self.status != ConnectionStatus.CONNECTED:
            return
        if not 0 <= value <= 100:
            self._log(f"Ignoring invalid battery value from headset: {value}")
            return
        self.battery = value
        self._set_status_text()
        self._update_tray_icon()
        self._maybe_notify_battery()

    def _handle_mic_monitor_feedback_packet(self, value):
        if self.status != ConnectionStatus.CONNECTED:
            return
        self._mic_state_reported = True
        self._mic_state_probe_timer.stop()
        self._handle_reported_mic_monitor_state(value > 0)

    def _handle_packet(self, data):
        if self._verbose_io_logs:
            HyperxWindow._emit_log(
                self,
                f"RX {self._format_packet(data)}",
                level=LOG_LEVEL_DEBUG,
            )
        if len(data) < 4:
            return
        if data[0] != 0x21 or data[1] != 0xBB:
            return

        self._transient_tx_failures = 0
        self._clear_tx_timeout_backoff()
        code = data[2]
        value = data[3]
        handler = self._packet_handlers.get(code)
        if handler is not None:
            handler(value)

    def quit(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        self._invalidate_tx_session()
        self._poll_timer.stop()
        self._device_hotplug_timer.stop()
        self._mic_state_probe_timer.stop()
        self._open_retry_timer.stop()
        self._log_flush_timer.stop()
        self._clear_pending_connection_notifications()
        self._clear_pending_battery_notifications()
        self._stop_reader()
        self._stop_opener()
        self._stop_tx_worker()
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
    runtime_dir = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.RuntimeLocation)
    if not runtime_dir:
        runtime_dir = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.TempLocation)
    lock_dir = Path(runtime_dir) / "hyperxalpha"
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        lock_dir = Path("/tmp")
    lock = QtCore.QLockFile(str(lock_dir / "app.lock"))
    if not lock.tryLock(0):
        QtWidgets.QMessageBox.warning(
            None,
            APP_NAME,
            f"{APP_NAME} e' gia in esecuzione.",
        )
        return None
    app._single_instance_lock = lock
    app.aboutToQuit.connect(lock.unlock)
    app.setQuitOnLastWindowClosed(False if use_tray else True)
    win = HyperxWindow(start_hidden=start_hidden, use_tray=use_tray)
    app.exec()
    return win
