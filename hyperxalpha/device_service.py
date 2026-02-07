from dataclasses import dataclass

from PySide6 import QtCore

from .constants import COMPATIBLE_MODELS, PRODUCT_ID, VENDOR_ID
from .device import HidIoError, HyperxDevice


@dataclass(frozen=True)
class DeviceDescriptor:
    key: str
    vendor_id: int
    product_id: int
    model_name: str
    path: str | None
    serial_number: str | None
    manufacturer_string: str | None
    product_string: str | None

    def display_name(self):
        details = [self.model_name, f"[{self.vendor_id:04X}:{self.product_id:04X}]"]
        if self.serial_number:
            details.append(f"SN:{self.serial_number}")
        return " ".join(details)


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
            except Exception as exc:
                self.io_failed.emit(f"Unexpected device read error: {exc}")
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
        self._descriptors_by_key = {}

    def list_compatible_devices(self):
        devices = HyperxDevice.list_devices(vendor_id=VENDOR_ID, product_id=0)
        descriptors = []
        dedupe_keys = set()
        for info in devices:
            descriptor = self._to_descriptor(info)
            if descriptor is None:
                continue
            if descriptor.key in dedupe_keys:
                continue
            dedupe_keys.add(descriptor.key)
            descriptors.append(descriptor)
        descriptors.sort(key=lambda item: item.display_name().lower())
        self._descriptors_by_key = {item.key: item for item in descriptors}
        return descriptors

    def select_device(self, key):
        descriptor = self._descriptors_by_key.get(key)
        if descriptor is None:
            return None
        self._device.set_target(
            vendor_id=descriptor.vendor_id,
            product_id=descriptor.product_id,
            device_path=descriptor.path,
        )
        return descriptor

    def set_default_target(self):
        self._device.set_target(
            vendor_id=VENDOR_ID,
            product_id=PRODUCT_ID,
            device_path=None,
        )

    def open(self):
        return self._device.open()

    def close(self):
        self._device.close()

    def send_command(self, cmd):
        return self._device.send_command(cmd)

    def read(self, timeout_ms=100):
        return self._device.read(timeout_ms=timeout_ms)

    def _to_descriptor(self, info):
        if not self._is_compatible(info):
            return None
        model_name = self._model_name(info)
        serial = info.serial_number or None
        key = (
            f"path:{info.path}"
            if info.path
            else f"vidpid:{info.vendor_id:04X}:{info.product_id:04X}:{serial or 'noserial'}"
        )
        return DeviceDescriptor(
            key=key,
            vendor_id=info.vendor_id,
            product_id=info.product_id,
            model_name=model_name,
            path=info.path or None,
            serial_number=serial,
            manufacturer_string=info.manufacturer_string or None,
            product_string=info.product_string or None,
        )

    def _is_compatible(self, info):
        return (info.vendor_id, info.product_id) in COMPATIBLE_MODELS

    def _model_name(self, info):
        model_key = (info.vendor_id, info.product_id)
        if model_key in COMPATIBLE_MODELS:
            return COMPATIBLE_MODELS[model_key]
        if info.product_string:
            return info.product_string
        return f"HyperX compatible model 0x{info.product_id:04X}"
