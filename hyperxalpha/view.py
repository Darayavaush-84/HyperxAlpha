from PySide6 import QtCore, QtGui, QtWidgets

from . import APP_NAME
from .constants import ConnectionStatus


class LogDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HyperX Alpha Logs")
        self.resize(560, 360)

        layout = QtWidgets.QVBoxLayout(self)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)

    def set_text(self, text):
        self.text.setPlainText(text)
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def append_line(self, line):
        self.text.appendPlainText(line)
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())


class ToggleSwitch(QtWidgets.QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(46, 24)
        self._color_on = QtGui.QColor("#2a9d8f")
        self._color_off = QtGui.QColor("#cbd5e1")
        self._knob = QtGui.QColor("#ffffff")

    def set_colors(self, on_color, off_color, knob_color):
        self._color_on = QtGui.QColor(on_color)
        self._color_off = QtGui.QColor(off_color)
        self._knob = QtGui.QColor(knob_color)
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(self.rect())
        radius = rect.height() / 2.0
        painter.setPen(QtCore.Qt.NoPen)
        # Disabled switches must not look "active" even if they keep last state.
        is_active = self.isChecked() and self.isEnabled()
        painter.setBrush(self._color_on if is_active else self._color_off)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)

        knob_size = rect.height() - 4
        x = rect.width() - knob_size - 2 if self.isChecked() else 2
        knob_rect = QtCore.QRectF(x, 2, knob_size, knob_size)
        painter.setBrush(self._knob)
        painter.drawEllipse(knob_rect)


class HyperxViewMixin:
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(16)

        header_card = QtWidgets.QFrame()
        header_card.setObjectName("heroCard")
        header_layout = QtWidgets.QHBoxLayout(header_card)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(18)

        brand_col = QtWidgets.QVBoxLayout()
        brand_col.setSpacing(8)

        logo = QtWidgets.QLabel()
        logo.setObjectName("brandLogo")
        logo_path = self.icon_dir / "hyperx.png"
        if logo_path.exists():
            pixmap = QtGui.QPixmap(str(logo_path)).scaledToWidth(172, QtCore.Qt.SmoothTransformation)
            logo.setPixmap(pixmap)
            logo.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        brand_col.addWidget(logo)

        self.title_label = QtWidgets.QLabel(f"{APP_NAME} Control Center")
        self.title_label.setObjectName("titleLabel")
        subtitle = QtWidgets.QLabel("Headset management dashboard")
        subtitle.setObjectName("subtitleLabel")
        self.status_label = QtWidgets.QLabel("Disconnected")
        self.status_label.setObjectName("statusLabel")
        brand_col.addWidget(self.title_label)
        brand_col.addWidget(subtitle)
        brand_col.addWidget(self.status_label)
        brand_col.addStretch(1)

        status_col = QtWidgets.QVBoxLayout()
        status_col.setSpacing(10)
        status_col.setAlignment(QtCore.Qt.AlignTop)
        status_col.addWidget(QtWidgets.QLabel("Connection"))
        self.connection_badge = QtWidgets.QLabel("Disconnected")
        self.connection_badge.setObjectName("statusPill")
        self.connection_badge.setAlignment(QtCore.Qt.AlignCenter)
        self.connection_badge.setMinimumWidth(140)
        status_col.addWidget(self.connection_badge)
        battery_label = QtWidgets.QLabel("Battery")
        battery_label.setObjectName("subtleLabel")
        status_col.addWidget(battery_label)
        self.battery_summary_label = QtWidgets.QLabel("Headset powered off")
        self.battery_summary_label.setObjectName("batterySummary")
        self.battery_summary_label.setWordWrap(True)
        status_col.addWidget(self.battery_summary_label)
        self.battery_progress = QtWidgets.QProgressBar()
        self.battery_progress.setObjectName("batteryBar")
        self.battery_progress.setRange(0, 100)
        self.battery_progress.setValue(0)
        self.battery_progress.setTextVisible(False)
        self.battery_progress.setFixedHeight(10)
        status_col.addWidget(self.battery_progress)
        status_col.addStretch(1)

        header_layout.addLayout(brand_col, 2)
        header_layout.addLayout(status_col, 1)
        layout.addWidget(header_card)

        content_layout = QtWidgets.QHBoxLayout()
        content_layout.setSpacing(16)
        layout.addLayout(content_layout, 1)

        left_column = QtWidgets.QVBoxLayout()
        left_column.setSpacing(14)
        right_column = QtWidgets.QVBoxLayout()
        right_column.setSpacing(14)
        content_layout.addLayout(left_column, 3)
        content_layout.addLayout(right_column, 2)

        features = QtWidgets.QGroupBox("Headset Controls")
        features.setObjectName("card")
        features_layout = QtWidgets.QFormLayout(features)
        features_layout.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        features_layout.setHorizontalSpacing(16)
        features_layout.setVerticalSpacing(12)

        self.sleep_combo = QtWidgets.QComboBox()
        self.sleep_combo.addItems(["10 Minutes", "20 Minutes", "30 Minutes"])
        self.sleep_combo.currentIndexChanged.connect(self._on_sleep_changed)

        self.voice_switch = ToggleSwitch()
        self.voice_switch.toggled.connect(self._on_voice_toggle)

        self.mic_switch = ToggleSwitch()
        if self.settings.mic_monitor_state is not None:
            self.mic_switch.setChecked(bool(self.settings.mic_monitor_state))
        self.mic_switch.toggled.connect(self._on_mic_toggle)

        features_layout.addRow("Sleep Timer", self.sleep_combo)
        features_layout.addRow("Voice Prompt", self.voice_switch)
        features_layout.addRow("Mic Monitoring", self.mic_switch)
        left_column.addWidget(features)

        session = QtWidgets.QGroupBox("Session Actions")
        session.setObjectName("card")
        session_layout = QtWidgets.QVBoxLayout(session)
        session_layout.setContentsMargins(16, 16, 16, 16)
        session_layout.setSpacing(10)
        session_note = QtWidgets.QLabel(
            "Use minimize to keep the app running in systray. Open logs for troubleshooting."
        )
        session_note.setObjectName("sectionHint")
        session_note.setWordWrap(True)
        session_layout.addWidget(session_note)

        action_grid = QtWidgets.QGridLayout()
        action_grid.setContentsMargins(0, 0, 0, 0)
        action_grid.setHorizontalSpacing(10)
        action_grid.setVerticalSpacing(10)

        self.min_button = QtWidgets.QPushButton("Minimize to Tray")
        self.min_button.clicked.connect(self._on_minimize)
        self.min_button.setObjectName("softButton")

        self.log_button = QtWidgets.QPushButton("Open Logs")
        self.log_button.clicked.connect(self._show_logs)
        self.log_button.setObjectName("softButton")

        self.quit_button = QtWidgets.QPushButton("Quit")
        self.quit_button.clicked.connect(self.quit)
        self.quit_button.setObjectName("destructiveButton")

        action_grid.addWidget(self.min_button, 0, 0)
        action_grid.addWidget(self.log_button, 0, 1)
        action_grid.addWidget(self.quit_button, 1, 0, 1, 2)
        session_layout.addLayout(action_grid)
        left_column.addWidget(session)
        left_column.addStretch(1)

        prefs = QtWidgets.QGroupBox("Device & Preferences")
        prefs.setObjectName("card")
        prefs_layout = QtWidgets.QFormLayout(prefs)
        prefs_layout.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        prefs_layout.setHorizontalSpacing(16)
        prefs_layout.setVerticalSpacing(12)

        self.device_combo = QtWidgets.QComboBox()
        self.device_combo.currentIndexChanged.connect(self._on_device_selection_changed)
        self.device_refresh_button = QtWidgets.QPushButton("Scan Devices")
        self.device_refresh_button.setObjectName("softButton")
        self.device_refresh_button.clicked.connect(self._on_scan_devices)
        device_box = QtWidgets.QHBoxLayout()
        device_box.setContentsMargins(0, 0, 0, 0)
        device_box.setSpacing(10)
        device_box.addWidget(self.device_combo, 1)
        device_box.addWidget(self.device_refresh_button)
        device_widget = QtWidgets.QWidget()
        device_widget.setLayout(device_box)

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
        self.notify_switch.setChecked(self.settings.tray_notifications)
        self.notify_switch.toggled.connect(self._on_notifications_toggle)

        prefs_layout.addRow("Active Headset", device_widget)
        prefs_layout.addRow("Start in Systray", self.tray_switch)
        prefs_layout.addRow("Theme", self.theme_combo)
        prefs_layout.addRow("Tray Notifications", self.notify_switch)
        right_column.addWidget(prefs)

        right_info = QtWidgets.QGroupBox("Status Notes")
        right_info.setObjectName("card")
        right_info_layout = QtWidgets.QVBoxLayout(right_info)
        right_info_layout.setContentsMargins(16, 16, 16, 16)
        right_info_layout.setSpacing(8)
        note = QtWidgets.QLabel(
            "Connection and battery data update from headset telemetry.\n"
            "If the headset is off, the app remains in disconnected mode."
        )
        note.setObjectName("sectionHint")
        note.setWordWrap(True)
        right_info_layout.addWidget(note)
        right_info_layout.addStretch(1)
        right_column.addWidget(right_info)
        right_column.addStretch(1)

        self._set_controls_enabled(False)
        self._set_status_text()

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
        self._set_status_text()

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
            QWidget {
                font-family: 'IBM Plex Sans', 'Source Sans 3', 'Noto Sans', sans-serif;
                font-size: 13px;
            }
            QWidget#rootWindow { background-color: #0a1321; }
            QFrame#heroCard {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #101f33, stop:1 #17283f
                );
                border: 1px solid rgba(165, 200, 232, 0.20);
                border-radius: 16px;
            }
            #titleLabel { font-size: 23px; font-weight: 700; color: #f8fbff; letter-spacing: 0.2px; }
            #subtitleLabel { color: #a6bdd6; font-size: 13px; }
            #statusLabel { color: #d9e8f7; font-size: 15px; font-weight: 600; }
            #subtleLabel { color: #9ab4ce; font-size: 12px; }
            #batterySummary { color: #e5eef8; font-weight: 600; }
            #sectionHint { color: #9fb0c2; font-size: 12px; line-height: 1.35; }
            QLabel#statusPill {
                border-radius: 999px;
                padding: 6px 12px;
                border: 1px solid rgba(179, 214, 255, 0.38);
                font-weight: 700;
                background-color: rgba(40, 70, 100, 0.35);
                color: #dceaf9;
            }
            QLabel#statusPill[state="connected"] {
                background-color: rgba(37, 148, 120, 0.22);
                border-color: rgba(130, 247, 206, 0.60);
                color: #9ff8d8;
            }
            QLabel#statusPill[state="disconnected"] {
                background-color: rgba(128, 80, 80, 0.22);
                border-color: rgba(255, 178, 178, 0.45);
                color: #ffd2d2;
            }
            QLabel { color: #e7f0fb; }
            QGroupBox#card {
                background-color: #101b2d;
                border: 1px solid rgba(165, 195, 225, 0.22);
                border-radius: 14px;
                margin-top: 13px;
                padding-top: 8px;
            }
            QGroupBox#card::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 8px;
                color: #d0e2f6;
                font-weight: 700;
            }
            QComboBox {
                background-color: #0d1727;
                color: #e6f0fb;
                border: 1px solid rgba(150, 182, 214, 0.45);
                border-radius: 8px;
                padding: 5px 26px 5px 10px;
                min-height: 30px;
            }
            QComboBox:hover { border-color: #77a8d9; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView {
                background-color: #0f1c30;
                color: #e8f1fb;
                border: 1px solid #2e4664;
                selection-background-color: #2e5d8c;
            }
            QProgressBar#batteryBar {
                background-color: #2a3a4d;
                border: 1px solid rgba(168, 196, 224, 0.30);
                border-radius: 5px;
            }
            QProgressBar#batteryBar::chunk {
                border-radius: 5px;
                background-color: #58c7a2;
            }
            QProgressBar#batteryBar[state="disconnected"]::chunk { background-color: #64748b; }
            QPushButton {
                min-height: 34px;
                border-radius: 9px;
                padding: 6px 10px;
                font-weight: 600;
            }
            QPushButton#softButton {
                background-color: rgba(112, 192, 255, 0.16);
                color: #cbe6ff;
                border: 1px solid rgba(112, 192, 255, 0.32);
            }
            QPushButton#softButton:hover { background-color: rgba(112, 192, 255, 0.25); }
            QPushButton#destructiveButton {
                background-color: #d95858;
                color: white;
                border: 1px solid #c74444;
            }
            QPushButton#destructiveButton:hover { background-color: #c94b4b; }
            """
        return """
            QWidget {
                font-family: 'IBM Plex Sans', 'Source Sans 3', 'Noto Sans', sans-serif;
                font-size: 13px;
            }
            QWidget#rootWindow { background-color: #eef3f8; }
            QFrame#heroCard {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffffff, stop:1 #edf4fb
                );
                border: 1px solid rgba(44, 89, 132, 0.20);
                border-radius: 16px;
            }
            #titleLabel { font-size: 23px; font-weight: 700; color: #12395d; letter-spacing: 0.2px; }
            #subtitleLabel { color: #4d6782; font-size: 13px; }
            #statusLabel { color: #1f486b; font-size: 15px; font-weight: 600; }
            #subtleLabel { color: #53708d; font-size: 12px; }
            #batterySummary { color: #1f4264; font-weight: 600; }
            #sectionHint { color: #5e7892; font-size: 12px; line-height: 1.35; }
            QLabel#statusPill {
                border-radius: 999px;
                padding: 6px 12px;
                border: 1px solid rgba(43, 93, 141, 0.35);
                font-weight: 700;
                background-color: rgba(72, 111, 147, 0.12);
                color: #28527a;
            }
            QLabel#statusPill[state="connected"] {
                background-color: rgba(20, 151, 124, 0.14);
                border-color: rgba(20, 151, 124, 0.45);
                color: #0f7d69;
            }
            QLabel#statusPill[state="disconnected"] {
                background-color: rgba(191, 81, 81, 0.12);
                border-color: rgba(191, 81, 81, 0.40);
                color: #9d3a3a;
            }
            QLabel { color: #1b3b5c; }
            QGroupBox#card {
                background-color: #ffffff;
                border: 1px solid rgba(46, 87, 126, 0.16);
                border-radius: 14px;
                margin-top: 13px;
                padding-top: 8px;
            }
            QGroupBox#card::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 8px;
                color: #284f74;
                font-weight: 700;
            }
            QComboBox {
                background-color: #ffffff;
                color: #143a59;
                border: 1px solid #b0c5d8;
                border-radius: 8px;
                padding: 5px 26px 5px 10px;
                min-height: 30px;
            }
            QComboBox:hover { border-color: #6a99c4; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                color: #143a59;
                border: 1px solid #aac0d3;
                selection-background-color: #d5e8f8;
            }
            QProgressBar#batteryBar {
                background-color: #d6e2ee;
                border: 1px solid rgba(46, 87, 126, 0.20);
                border-radius: 5px;
            }
            QProgressBar#batteryBar::chunk {
                border-radius: 5px;
                background-color: #20a17f;
            }
            QProgressBar#batteryBar[state="disconnected"]::chunk { background-color: #8ca0b4; }
            QPushButton {
                min-height: 34px;
                border-radius: 9px;
                padding: 6px 10px;
                font-weight: 600;
            }
            QPushButton#softButton {
                background-color: rgba(33, 102, 163, 0.10);
                color: #1f5b8f;
                border: 1px solid rgba(33, 102, 163, 0.18);
            }
            QPushButton#softButton:hover { background-color: rgba(33, 102, 163, 0.16); }
            QPushButton#destructiveButton {
                background-color: #e4684d;
                color: white;
                border: 1px solid #cd4e35;
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
        except (AttributeError, RuntimeError, TypeError):
            pass
        palette = QtWidgets.QApplication.palette()
        window = palette.color(QtGui.QPalette.Window)
        luminance = (0.2126 * window.red()) + (0.7152 * window.green()) + (0.0722 * window.blue())
        return luminance < 128

    @staticmethod
    def _refresh_widget_style(widget):
        if widget is None:
            return
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _set_controls_enabled(self, enabled):
        self.sleep_combo.setEnabled(enabled)
        self.voice_switch.setEnabled(enabled)
        self.mic_switch.setEnabled(enabled)

    def _set_status_text(self):
        connected = self.status == ConnectionStatus.CONNECTED
        if connected and self.battery is not None:
            self.status_label.setText(f"Battery: {self.battery}%")
        elif connected:
            self.status_label.setText("Connected")
        else:
            self.status_label.setText("Disconnected")

        if not hasattr(self, "connection_badge"):
            return

        badge_state = "connected" if connected else "disconnected"
        badge_text = "Connected" if connected else "Disconnected"
        self.connection_badge.setText(badge_text)
        if self.connection_badge.property("state") != badge_state:
            self.connection_badge.setProperty("state", badge_state)
            self._refresh_widget_style(self.connection_badge)

        if not hasattr(self, "battery_summary_label") or not hasattr(self, "battery_progress"):
            return

        if connected and self.battery is not None:
            hours = self.battery * 3
            self.battery_summary_label.setText(f"{self.battery}% (about {hours}h remaining)")
            self.battery_progress.setValue(self.battery)
        elif connected:
            self.battery_summary_label.setText("Reading headset battery...")
            self.battery_progress.setValue(0)
        else:
            self.battery_summary_label.setText("Headset powered off")
            self.battery_progress.setValue(0)

        if self.battery_progress.property("state") != badge_state:
            self.battery_progress.setProperty("state", badge_state)
            self._refresh_widget_style(self.battery_progress)
