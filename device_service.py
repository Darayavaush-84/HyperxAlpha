from PySide6 import QtCore

from .constants import PRODUCT_ID, VENDOR_ID, Command
from .device import HidIoError, HyperxDevice


class DeviceReader(QtCore.QThread):
    packet_received = QtCore.Signal(list)
    io_failed = QtCore.Signal(str)

    def __init__(self, device_service, read_timeout_ms=100, parent=None):
        super().__init__(parent)
        self._device_service = device_service
        self._read_timeout_ms = max(20, int(read_timeout_ms))
        self._running = True

    def run(self):
        while self._running and not self.isInterruptionRequested():
            try:
                data = self._device_service.read(timeout_ms=self._read_timeout_ms)
            except HidIoError as exc:
                self.io_failed.emit(str(exc))
                return
            if data is None:
                continue
            self.packet_received.emit(data)

    def stop(self):
        self._running = False
        self.requestInterruption()


class DeviceOpenSignals(QtCore.QObject):
    opened = QtCore.Signal(int)
    failed = QtCore.Signal(int, str)


class DeviceService:
    def __init__(self, vendor_id=VENDOR_ID, product_id=PRODUCT_ID):
        self._device = HyperxDevice(vendor_id=vendor_id, product_id=product_id)

    def open(self):
        return self._device.open()

    def close(self):
        self._device.close()

    def send_command(self, cmd: Command):
        return self._device.send_command(cmd)

    def read(self, timeout_ms=100):
        return self._device.read(timeout_ms=timeout_ms)
