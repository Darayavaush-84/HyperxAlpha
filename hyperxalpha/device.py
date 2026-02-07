import atexit
import ctypes
import threading
import time
from dataclasses import dataclass

from .constants import PRODUCT_ID, VENDOR_ID


class HidUnavailable(RuntimeError):
    pass


class HidIoError(RuntimeError):
    pass


def _exception_detail(exc):
    text = str(exc).strip()
    if text:
        return f"{exc.__class__.__name__}: {text}"
    return exc.__class__.__name__


class _HidDeviceInfoStruct(ctypes.Structure):
    pass


_HidDeviceInfoPtr = ctypes.POINTER(_HidDeviceInfoStruct)
_HidDeviceInfoStruct._fields_ = [
    ("path", ctypes.c_char_p),
    ("vendor_id", ctypes.c_ushort),
    ("product_id", ctypes.c_ushort),
    ("serial_number", ctypes.c_wchar_p),
    ("release_number", ctypes.c_ushort),
    ("manufacturer_string", ctypes.c_wchar_p),
    ("product_string", ctypes.c_wchar_p),
    ("usage_page", ctypes.c_ushort),
    ("usage", ctypes.c_ushort),
    ("interface_number", ctypes.c_int),
    ("next", _HidDeviceInfoPtr),
]


@dataclass(frozen=True)
class HidDeviceInfo:
    path: str
    vendor_id: int
    product_id: int
    serial_number: str | None = None
    manufacturer_string: str | None = None
    product_string: str | None = None
    interface_number: int | None = None


class _HidrawBackend:
    _state_lock = threading.Lock()
    _shared_lib = None
    _shared_has_open_path = False
    _shared_has_enumerate = False
    _shared_initialized = False
    _shared_shutdown_registered = False

    def __init__(self):
        self._initialize_shared_backend()
        self._lib = self.__class__._shared_lib
        self._has_open_path = self.__class__._shared_has_open_path
        self._has_enumerate = self.__class__._shared_has_enumerate

    @classmethod
    def _initialize_shared_backend(cls):
        with cls._state_lock:
            if cls._shared_initialized and cls._shared_lib is not None:
                return

            lib = None
            has_open_path = False
            has_enumerate = False
            for name in ("libhidapi-hidraw.so.0", "libhidapi-hidraw.so"):
                try:
                    lib = ctypes.CDLL(name)
                    break
                except OSError:
                    continue
            if lib is None:
                raise OSError("libhidapi-hidraw not found")

            lib.hid_init.restype = ctypes.c_int
            lib.hid_exit.restype = ctypes.c_int
            lib.hid_open.argtypes = [
                ctypes.c_ushort,
                ctypes.c_ushort,
                ctypes.c_wchar_p,
            ]
            lib.hid_open.restype = ctypes.c_void_p
            lib.hid_close.argtypes = [ctypes.c_void_p]
            lib.hid_write.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ubyte),
                ctypes.c_size_t,
            ]
            lib.hid_write.restype = ctypes.c_int
            lib.hid_read_timeout.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ubyte),
                ctypes.c_size_t,
                ctypes.c_int,
            ]
            lib.hid_read_timeout.restype = ctypes.c_int
            try:
                lib.hid_set_nonblocking.argtypes = [ctypes.c_void_p, ctypes.c_int]
                lib.hid_set_nonblocking.restype = ctypes.c_int
            except AttributeError:
                pass
            try:
                lib.hid_error.argtypes = [ctypes.c_void_p]
                lib.hid_error.restype = ctypes.c_wchar_p
            except AttributeError:
                pass
            if hasattr(lib, "hid_open_path"):
                lib.hid_open_path.argtypes = [ctypes.c_char_p]
                lib.hid_open_path.restype = ctypes.c_void_p
                has_open_path = True
            if hasattr(lib, "hid_enumerate") and hasattr(lib, "hid_free_enumeration"):
                lib.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
                lib.hid_enumerate.restype = _HidDeviceInfoPtr
                lib.hid_free_enumeration.argtypes = [_HidDeviceInfoPtr]
                lib.hid_free_enumeration.restype = None
                has_enumerate = True

            if lib.hid_init() < 0:
                raise OSError("hidapi init failed")

            cls._shared_lib = lib
            cls._shared_has_open_path = has_open_path
            cls._shared_has_enumerate = has_enumerate
            cls._shared_initialized = True

            if not cls._shared_shutdown_registered:
                atexit.register(cls._shutdown_shared_backend)
                cls._shared_shutdown_registered = True

    @classmethod
    def _shutdown_shared_backend(cls):
        with cls._state_lock:
            lib = cls._shared_lib
            if lib is None:
                return
            try:
                lib.hid_exit()
            except (AttributeError, OSError, TypeError, ValueError):
                pass
            cls._shared_lib = None
            cls._shared_has_open_path = False
            cls._shared_has_enumerate = False
            cls._shared_initialized = False

    def enumerate(self, vendor_id=0, product_id=0):
        if not self._has_enumerate:
            return []
        head = self._lib.hid_enumerate(int(vendor_id), int(product_id))
        devices = []
        current = head
        try:
            while current:
                entry = current.contents
                path = (
                    entry.path.decode("utf-8", errors="ignore")
                    if entry.path
                    else ""
                )
                if path:
                    interface_number = int(entry.interface_number)
                    if interface_number < 0:
                        interface_number = None
                    devices.append(
                        HidDeviceInfo(
                            path=path,
                            vendor_id=int(entry.vendor_id),
                            product_id=int(entry.product_id),
                            serial_number=entry.serial_number or None,
                            manufacturer_string=entry.manufacturer_string or None,
                            product_string=entry.product_string or None,
                            interface_number=interface_number,
                        )
                    )
                current = entry.next
        finally:
            if head:
                self._lib.hid_free_enumeration(head)
        return devices

    def open(self, vendor_id, product_id, device_path=None):
        if device_path:
            if not self._has_open_path:
                raise OSError("hid_open_path is not available in this hidapi build")
            encoded_path = device_path.encode("utf-8", errors="ignore")
            handle = self._lib.hid_open_path(encoded_path)
            if not handle:
                raise OSError(f"hid_open_path failed for {device_path}")
        else:
            handle = self._lib.hid_open(vendor_id, product_id, None)
            if not handle:
                raise OSError("hid_open failed")
        try:
            self._lib.hid_set_nonblocking(handle, 0)
        except AttributeError:
            pass
        return _HidrawHandle(self._lib, handle)


class _HidrawHandle:
    def __init__(self, lib, handle):
        self._lib = lib
        self._handle = handle

    def close(self):
        if self._handle:
            self._lib.hid_close(self._handle)
            self._handle = None

    def write(self, payload):
        data = (ctypes.c_ubyte * len(payload))(*payload)
        result = self._lib.hid_write(self._handle, data, len(payload))
        if result < 0:
            raise HidIoError(f"hid_write failed: {self._last_error()}")
        if result != len(payload):
            raise HidIoError(
                f"hid_write incomplete: wrote {result} of {len(payload)} bytes"
            )
        return result

    def read(self, size, timeout_ms):
        buffer = (ctypes.c_ubyte * size)()
        res = self._lib.hid_read_timeout(self._handle, buffer, size, timeout_ms)
        if res < 0:
            raise HidIoError(f"hid_read_timeout failed: {self._last_error()}")
        if res == 0:
            return []
        return list(buffer[:res])

    def _last_error(self):
        try:
            message = self._lib.hid_error(self._handle)
            if message:
                return str(message)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        return "unknown hidapi error"


class HyperxDevice:
    def __init__(self, vendor_id=VENDOR_ID, product_id=PRODUCT_ID):
        self.vendor_id = int(vendor_id)
        self.product_id = int(product_id)
        self.device_path = None
        self._dev = None
        self._backend = None
        self._io_lock = threading.Lock()

    @staticmethod
    def list_devices(vendor_id=VENDOR_ID, product_id=0):
        try:
            hidraw = _HidrawBackend()
            return hidraw.enumerate(vendor_id=vendor_id, product_id=product_id)
        except Exception as exc:
            detail = _exception_detail(exc)
            raise HidUnavailable(
                "Unable to enumerate HID devices. "
                "Install libhidapi-hidraw and check udev rules. "
                f"Cause: {detail}"
            ) from exc

    def set_target(self, vendor_id=None, product_id=None, device_path=None):
        with self._io_lock:
            if vendor_id is not None:
                self.vendor_id = int(vendor_id)
            if product_id is not None:
                self.product_id = int(product_id)
            self.device_path = str(device_path) if device_path else None

    def open(self):
        try:
            hidraw = _HidrawBackend()
            dev = hidraw.open(
                self.vendor_id,
                self.product_id,
                device_path=self.device_path,
            )
            with self._io_lock:
                self._dev = dev
                self._backend = "hidraw"
        except Exception as exc:
            with self._io_lock:
                self._dev = None
                self._backend = None
            target = (
                f"path={self.device_path}"
                if self.device_path
                else f"vid=0x{self.vendor_id:04X}, pid=0x{self.product_id:04X}"
            )
            detail = _exception_detail(exc)
            raise HidUnavailable(
                "Unable to open HyperX device via hidraw "
                f"({target}). Install libhidapi-hidraw and check udev rules. "
                f"Cause: {detail}"
            ) from exc
        return True

    def close(self):
        with self._io_lock:
            dev = self._dev
            self._dev = None
        if dev is None:
            return
        try:
            dev.close()
        except OSError:
            pass

    def send_command(self, cmd):
        payload = int(cmd).to_bytes(4, "big")
        with self._io_lock:
            dev = self._dev
            if dev is None:
                return False
            try:
                dev.write(payload)
            except TypeError:
                dev.write(list(payload))
        return True

    def read(self, timeout_ms=100):
        timeout_ms = max(20, int(timeout_ms))
        with self._io_lock:
            dev = self._dev
            if dev is None:
                time.sleep(min(timeout_ms, 50) / 1000.0)
                return None
            data = dev.read(32, timeout_ms)
        if not data:
            return None
        if isinstance(data, bytes):
            return list(data)
        return data
