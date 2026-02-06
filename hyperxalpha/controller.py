import os
import sys
import threading
from datetime import datetime
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    raise RuntimeError("PySide6 is required (python3-pyside6)") from exc

from .constants import Command, ConnectionStatus
from .device import HidIoError
from .device_service import DeviceOpenSignals, DeviceReader, DeviceService
from .settings_service import SettingsService
from .view import LogDialog, ToggleSwitch


class HyperxWindow(QtWidgets.QWidget):
    def __init__(self, start_hidden=False, use_tray=True):
        super().__init__()
        self.setObjectName("rootWindow")
        self.setAutoFillBackground(True)
        self.setWindowTitle("HyperX Alpha")
        self.setFixedSize(380, 560)

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

        self.status = ConnectionStatus.DISCONNECTED
        self.battery = None
        self._battery_notified_levels = set()

        self._tray_available = False
        self._tray = None
        self._tray_menu = None
        self._tray_toggle_action = None
        self._tray_icons = {}

        self._log_buffer = []
        self._log_buffer_max = 1000
        self._log_dialog = None
        self._verbose_io_logs = os.environ.get("HYPERX_DEBUG_IO", "0") == "1"

        self._theme_is_dark = False

        self.settings = self._settings_service.load()
        if self._settings_service.autostart_enabled() and not self.settings.start_in_tray:
            self.settings.start_in_tray = True
            self._settings_service.save(self.settings)

        self._build_ui()
        self._apply_theme()

        if use_tray and QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_available = True
            self._init_tray()

        if start_hidden and self._tray_available:
            self.hide()
        else:
            self.show()

        QtCore.QTimer.singleShot(0, self._start_device_open)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        logo = QtWidgets.QLabel()
        logo_path = self.icon_dir / "hyperx.png"
        if logo_path.exists():
            pixmap = QtGui.QPixmap(str(logo_path)).scaledToWidth(240, QtCore.Qt.SmoothTransformation)
            logo.setPixmap(pixmap)
            logo.setAlignment(QtCore.Qt.AlignHCenter)
            layout.addWidget(logo)

        title = QtWidgets.QLabel("HyperX Alpha")
        title.setObjectName("titleLabel")
        subtitle = QtWidgets.QLabel("Wireless Control Panel")
        subtitle.setObjectName("subtitleLabel")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.status_label = QtWidgets.QLabel("Disconnected")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

        features = QtWidgets.QGroupBox("Features")
        features.setObjectName("card")
        features_layout = QtWidgets.QFormLayout(features)
        features_layout.setHorizontalSpacing(12)
        features_layout.setVerticalSpacing(10)

        self.sleep_combo = QtWidgets.QComboBox()
        self.sleep_combo.addItems(["10 Minutes", "20 Minutes", "30 Minutes"])
        self.sleep_combo.currentIndexChanged.connect(self._on_sleep_changed)

        self.voice_switch = ToggleSwitch()
        self.voice_switch.toggled.connect(self._on_voice_toggle)

        self.mic_switch = ToggleSwitch()
        self.mic_switch.toggled.connect(self._on_mic_toggle)
        if self.settings.mic_monitor_state is not None:
            self.mic_switch.setChecked(self.settings.mic_monitor_state)

        features_layout.addRow("Sleep Timer", self.sleep_combo)
        features_layout.addRow("Voice Prompt", self.voice_switch)
        features_layout.addRow("Mic Monitor", self.mic_switch)
        layout.addWidget(features)

        prefs = QtWidgets.QGroupBox("Preferences")
        prefs.setObjectName("card")
        prefs_layout = QtWidgets.QFormLayout(prefs)
        prefs_layout.setHorizontalSpacing(12)
        prefs_layout.setVerticalSpacing(10)

        self.tray_switch = ToggleSwitch()
        self.tray_switch.setChecked(self.settings.start_in_tray)
        self.tray_switch.toggled.connect(self._on_tray_toggle)

        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItem("System", "system")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        index = self.theme_combo.findData(self.settings.theme_mode)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)

        self.notify_switch = ToggleSwitch()
        self.notify_switch.setChecked(self.settings.low_battery_notifications)
        self.notify_switch.toggled.connect(self._on_notify_toggle)

        prefs_layout.addRow("Always start in Systray", self.tray_switch)
        prefs_layout.addRow("Theme", self.theme_combo)
        prefs_layout.addRow("Low battery notifications", self.notify_switch)
        layout.addWidget(prefs)

        button_box = QtWidgets.QVBoxLayout()
        button_box.setSpacing(8)

        self.quit_button = QtWidgets.QPushButton("Quit")
        self.quit_button.clicked.connect(self.quit)
        self.quit_button.setObjectName("destructiveButton")

        self.min_button = QtWidgets.QPushButton("Minimize")
        self.min_button.clicked.connect(self._on_minimize)
        self.min_button.setObjectName("softButton")

        self.log_button = QtWidgets.QPushButton("Open Logs")
        self.log_button.clicked.connect(self._show_logs)
        self.log_button.setObjectName("softButton")

        button_box.addWidget(self.quit_button)
        button_box.addWidget(self.min_button)
        button_box.addWidget(self.log_button)

        layout.addLayout(button_box)

        self._set_controls_enabled(False)

    def _apply_theme(self):
        dark = self._is_dark_mode()
        self._theme_is_dark = dark
        app = QtWidgets.QApplication.instance()
        palette = app.style().standardPalette()
        if dark:
            palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#0f141a"))
            palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#e6edf3"))
            palette.setColor(QtGui.QPalette.Base, QtGui.QColor("#111827"))
            palette.setColor(QtGui.QPalette.Text, QtGui.QColor("#e6edf3"))
            palette.setColor(QtGui.QPalette.Button, QtGui.QColor("#1f2937"))
            palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#e6edf3"))
            palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#1f6aa5"))
            palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
        else:
            palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#f4f8fc"))
            palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#14273f"))
            palette.setColor(QtGui.QPalette.Base, QtGui.QColor("#ffffff"))
            palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#ecf3fa"))
            palette.setColor(QtGui.QPalette.Text, QtGui.QColor("#14273f"))
            palette.setColor(QtGui.QPalette.Button, QtGui.QColor("#eaf2f9"))
            palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#123a5d"))
            palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#2f7ab8"))
            palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
        app.setPalette(palette)
        app.setStyleSheet(self._stylesheet(dark))
        self._update_switch_colors()

    def _update_switch_colors(self):
        if self._theme_is_dark:
            on_color = "#6ee7b7"
            off_color = "#334155"
            knob = "#f8fafc"
        else:
            on_color = "#19a79a"
            off_color = "#b5c8d9"
            knob = "#ffffff"
        for switch in (self.voice_switch, self.mic_switch, self.tray_switch, self.notify_switch):
            switch.set_colors(on_color, off_color, knob)

    def _stylesheet(self, dark):
        if dark:
            return """
            QWidget { font-family: 'IBM Plex Sans', 'Source Sans 3', 'Noto Sans', sans-serif; }
            QWidget#rootWindow { background-color: #0f141a; }
            #titleLabel { font-size: 20px; font-weight: 700; color: #f4f7fb; }
            #subtitleLabel { color: #9aa6b2; }
            #statusLabel { color: #6ee7b7; font-weight: 600; }
            QLabel { color: #e6edf3; }
            QGroupBox#card { background-color: #151a23; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; margin-top: 12px; }
            QGroupBox#card::title { subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 6px; }
            QPushButton#softButton { background-color: rgba(110,231,183,0.12); color: #6ee7b7; border-radius: 10px; padding: 8px; }
            QPushButton#destructiveButton { background-color: #ff7a59; color: white; border-radius: 10px; padding: 8px; }
            """
        return """
            QWidget { font-family: 'IBM Plex Sans', 'Source Sans 3', 'Noto Sans', sans-serif; }
            QWidget#rootWindow { background-color: #f4f8fc; }
            #titleLabel { font-size: 20px; font-weight: 700; color: #123a5d; }
            #subtitleLabel { color: #3f5f80; }
            #statusLabel { color: #0e8c7a; font-weight: 600; }
            QLabel { color: #1b3550; }
            QGroupBox#card { background-color: #ffffff; border: 1px solid rgba(17,58,93,0.14); border-radius: 12px; margin-top: 12px; }
            QGroupBox#card::title { subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 6px; color: #2b5275; }
            QComboBox {
                background-color: #ffffff;
                color: #143a59;
                border: 1px solid #b7cbde;
                border-radius: 8px;
                padding: 2px 24px 2px 8px;
            }
            QComboBox:hover { border-color: #88abcd; }
            QComboBox::drop-down { border: none; width: 22px; }
            QPushButton#softButton {
                background-color: rgba(33,102,163,0.10);
                color: #1f5b8f;
                border: 1px solid rgba(33,102,163,0.18);
                border-radius: 10px;
                padding: 8px;
                font-weight: 600;
            }
            QPushButton#softButton:hover { background-color: rgba(33,102,163,0.16); }
            QPushButton#destructiveButton {
                background-color: #e4684d;
                color: white;
                border: 1px solid #cd4e35;
                border-radius: 10px;
                padding: 8px;
                font-weight: 600;
            }
            QPushButton#destructiveButton:hover { background-color: #d95b40; }
            """

    def _is_dark_mode(self):
        mode = self.settings.theme_mode
        if mode == "dark":
            return True
        if mode == "light":
            return False
        return self._system_prefers_dark()

    def _system_prefers_dark(self):
        try:
            hints = QtGui.QGuiApplication.styleHints()
            if hasattr(hints, "colorScheme"):
                scheme = hints.colorScheme()
                if scheme == QtCore.Qt.ColorScheme.Dark:
                    return True
                if scheme == QtCore.Qt.ColorScheme.Light:
                    return False
        except Exception:
            pass
        palette = QtWidgets.QApplication.palette()
        window = palette.color(QtGui.QPalette.Window)
        luminance = (0.2126 * window.red()) + (0.7152 * window.green()) + (0.0722 * window.blue())
        return luminance < 128

    def _log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self._log_buffer.append(line)
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

    def _send_command(self, cmd):
        if not self._device_ready:
            if self._verbose_io_logs:
                self._log(f"TX skipped (device not ready): {cmd.name}")
            return False
        if self._verbose_io_logs:
            self._log(f"TX {cmd.name} (0x{int(cmd):08X})")
        try:
            sent = self._device_service.send_command(cmd)
        except HidIoError as exc:
            self._handle_device_io_error(f"TX {cmd.name} failed: {exc}")
            return False
        if not sent:
            self._handle_device_io_error(
                f"TX {cmd.name} failed: device handle unavailable."
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
        except Exception as exc:
            self._open_signals.failed.emit(generation, str(exc))

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
        QtCore.QTimer.singleShot(300, self._request_feature_states)

    def _on_device_failed(self, generation, message):
        self._opener_thread = None
        if self._shutting_down or generation != self._open_generation:
            return
        self._device_ready = False
        self._mic_state_reported = False
        self._mic_state_probe_timer.stop()
        self.status = ConnectionStatus.DISCONNECTED
        self.battery = None
        self._set_controls_enabled(False)
        self._set_status_text()
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

        self._device_ready = False
        self._mic_state_reported = False
        self._mic_state_probe_timer.stop()
        self._stop_reader()
        self._device_service.close()
        self.status = ConnectionStatus.DISCONNECTED
        self.battery = None
        self._battery_notified_levels.clear()
        self._set_controls_enabled(False)
        self._set_status_text()
        self._update_tray_icon()
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
        self._tray_menu.addAction("Quit", self.quit)
        self._tray.setContextMenu(self._tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setToolTip("HyperX Alpha")
        self._tray.show()

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

    def _set_controls_enabled(self, enabled):
        self.sleep_combo.setEnabled(enabled)
        self.voice_switch.setEnabled(enabled)
        self.mic_switch.setEnabled(enabled)

    def _on_minimize(self):
        self.hide()
        self._update_tray_menu_label()

    def _on_tray_toggle(self, _checked):
        if self._updating_settings:
            return
        enabled = self.tray_switch.isChecked()
        self.settings.start_in_tray = enabled
        if not self._settings_service.save(self.settings):
            self._show_error("Settings Error", "Unable to save preferences.")
            return
        if not self._settings_service.set_autostart(enabled):
            self._updating_settings = True
            self.tray_switch.setChecked(not enabled)
            self.settings.start_in_tray = not enabled
            self._settings_service.save(self.settings)
            self._updating_settings = False
            self._show_error("Autostart Error", "Unable to update autostart entry.")

    def _on_theme_changed(self, _index):
        if self._updating_settings:
            return
        mode = self.theme_combo.currentData() or "system"
        self.settings.theme_mode = mode
        if not self._settings_service.save(self.settings):
            self._show_error("Settings Error", "Unable to save preferences.")
            return
        self._apply_theme()

    def _on_notify_toggle(self, _checked):
        if self._updating_settings:
            return
        enabled = self.notify_switch.isChecked()
        self.settings.low_battery_notifications = enabled
        if not self._settings_service.save(self.settings):
            self._show_error("Settings Error", "Unable to save preferences.")
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

    def _on_voice_toggle(self, active):
        if self._updating_controls:
            return
        if active:
            self._send_command(Command.VOICE_PROMPTS)
        else:
            self._send_command(Command.VOICE_PROMPTS_OFF)

    def _persist_mic_monitor_state(self, active):
        if self.settings.mic_monitor_state == active:
            return
        self.settings.mic_monitor_state = active
        if not self._settings_service.save(self.settings):
            self._log("Unable to persist mic monitor state.")

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

    def _set_mic_monitor_state(self, active, persist=True):
        self._updating_controls = True
        self.mic_switch.setChecked(bool(active))
        self._updating_controls = False
        if persist:
            self._persist_mic_monitor_state(bool(active))

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
            self._log("Mic monitor state unavailable from headset (no cached fallback).")
            return
        self._log("Mic monitor state unavailable from headset; using cached value.")
        self._set_mic_monitor_state(bool(cached), persist=False)

    def _poll_headset(self):
        if not self._device_ready:
            return
        if self.status != ConnectionStatus.CONNECTED:
            self._send_command(Command.CONNECTION_STATE)
            self._send_command(Command.STATUS_REQUEST)
            return
        self._send_command(Command.STATUS_REQUEST)
        self._send_command(Command.PING)

    def _maybe_notify_battery(self):
        if not self.settings.low_battery_notifications:
            return
        if self.battery is None:
            return
        thresholds = (20, 10, 5)
        for level in thresholds:
            if self.battery > level:
                self._battery_notified_levels.discard(level)
        for level in thresholds:
            if self.battery <= level and level not in self._battery_notified_levels:
                self._battery_notified_levels.add(level)
                self._send_battery_notification()
                break

    def _send_battery_notification(self):
        if self._tray is None or self.battery is None:
            return
        self._tray.showMessage(
            "HyperX Alpha battery low",
            f"Battery at {self.battery}%",
            QtWidgets.QSystemTrayIcon.Warning,
            6000,
        )
        self._log(f"Battery notification sent ({self.battery}%).")

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

    def _set_status_text(self):
        if self.status == ConnectionStatus.CONNECTED and self.battery is not None:
            self.status_label.setText(f"Battery: {self.battery}%")
        elif self.status == ConnectionStatus.CONNECTED:
            self.status_label.setText("Connected")
        else:
            self.status_label.setText("Disconnected")

    def _on_connect(self):
        self.status = ConnectionStatus.CONNECTED
        self._mic_state_reported = False
        self._set_controls_enabled(True)
        self._set_status_text()
        self._update_tray_icon()
        self._log("Status: CONNECTED")
        self._poll_timer.setInterval(30000)
        if not self._poll_timer.isActive():
            self._poll_timer.start()
        self._request_feature_states()

    def _on_disconnect(self):
        self.status = ConnectionStatus.DISCONNECTED
        self._set_controls_enabled(False)
        self._set_status_text()
        self._update_tray_icon()
        self._log("Status: DISCONNECTED")
        self.battery = None
        self._battery_notified_levels.clear()
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
                self._on_connect()
            self._updating_controls = True
            if value == 0x0A:
                self.sleep_combo.setCurrentIndex(0)
            elif value == 0x14:
                self.sleep_combo.setCurrentIndex(1)
            elif value == 0x1E:
                self.sleep_combo.setCurrentIndex(2)
            self._updating_controls = False
        elif code == 0x09:
            if self.status != ConnectionStatus.CONNECTED:
                self._on_connect()
            self._updating_controls = True
            self.voice_switch.setChecked(value == 0x01)
            self._updating_controls = False
        elif code == 0x0A:
            if self.status != ConnectionStatus.CONNECTED:
                self._on_connect()
            self._log("Mic monitor state response (0x0A) does not report state.")
        elif code == 0x0B:
            if self.status != ConnectionStatus.CONNECTED:
                self._on_connect()
            self.battery = value
            self._set_status_text()
            self._update_tray_icon()
            self._maybe_notify_battery()
        elif code == 0x12:
            if self.status != ConnectionStatus.CONNECTED:
                self._on_connect()
            self._updating_controls = True
            if value == 0x0A:
                self.sleep_combo.setCurrentIndex(0)
            elif value == 0x14:
                self.sleep_combo.setCurrentIndex(1)
            elif value == 0x1E:
                self.sleep_combo.setCurrentIndex(2)
            self._updating_controls = False
        elif code == 0x13:
            if self.status != ConnectionStatus.CONNECTED:
                self._on_connect()
            self._updating_controls = True
            self.voice_switch.setChecked(value == 0x01)
            self._updating_controls = False
        elif code == 0x22:
            if self.status != ConnectionStatus.CONNECTED:
                self._on_connect()
            self._mic_state_reported = True
            self._mic_state_probe_timer.stop()
            self._set_mic_monitor_state(value == 0x01)
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
        self._mic_state_probe_timer.stop()
        self._open_retry_timer.stop()
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
