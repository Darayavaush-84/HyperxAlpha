from PySide6 import QtCore, QtGui, QtWidgets


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
        painter.setBrush(self._color_on if self.isChecked() else self._color_off)
        painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)

        knob_size = rect.height() - 4
        x = rect.width() - knob_size - 2 if self.isChecked() else 2
        knob_rect = QtCore.QRectF(x, 2, knob_size, knob_size)
        painter.setBrush(self._knob)
        painter.drawEllipse(knob_rect)
