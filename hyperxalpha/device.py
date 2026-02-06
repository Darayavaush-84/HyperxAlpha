import time
import ctypes
import threading

from .constants import Command, PRODUCT_ID, VENDOR_ID


class HidUnavailable(RuntimeError):
    pass


class HidIoError(RuntimeError):
    pass


class _HidrawBackend:
    def __init__(self):
        self._lib = None
        for name in ("libhidapi-hidraw.so.0", "libhidapi-hidraw.so"):
            try:
                self._lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if self._lib is None:
            raise OSError("libhidapi-hidraw not found")

        self._lib.hid_init.restype = ctypes.c_int
        self._lib.hid_exit.restype = ctypes.c_int
        self._lib.hid_open.argtypes = [
            ctypes.c_ushort,
            ctypes.c_ushort,
            ctypes.c_wchar_p,
        ]
        self._lib.hid_open.restype = ctypes.c_void_p
        self._lib.hid_close.argtypes = [ctypes.c_void_p]
        self._lib.hid_write.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_size_t,
        ]
        self._lib.hid_write.restype = ctypes.c_int
        self._lib.hid_read_timeout.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        self._lib.hid_read_timeout.restype = ctypes.c_int
        try:
            self._lib.hid_set_nonblocking.argtypes = [ctypes.c_void_p, ctypes.c_int]
            self._lib.hid_set_nonblocking.restype = ctypes.c_int
        except AttributeError:
            pass
        try:
            self._lib.hid_error.argtypes = [ctypes.c_void_p]
            self._lib.hid_error.restype = ctypes.c_wchar_p
        except AttributeError:
            pass

        if self._lib.hid_init() < 0:
            raise OSError("hidapi init failed")

    def open(self, vendor_id, product_id):
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
        except Exception:
            pass
        return "unknown hidapi error"


class HyperxDevice:
    def __init__(self, vendor_id=VENDOR_ID, product_id=PRODUCT_ID):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self._dev = None
        self._backend = None
        self._io_lock = threading.Lock()

    def open(self):
        try:
            hidraw = _HidrawBackend()
            dev = hidraw.open(self.vendor_id, self.product_id)
            with self._io_lock:
                self._dev = dev
                self._backend = "hidraw"
        except OSError as exc:
            with self._io_lock:
                self._dev = None
            raise HidUnavailable(
                "Unable to open HyperX device via hidraw. "
                "Install libhidapi-hidraw and check udev rules."
            ) from exc

        try:
            dev.set_nonblocking(False)
        except Exception:
            pass
        return True

    def close(self):
        with self._io_lock:
            dev = self._dev
            self._dev = None
        if dev is None:
            return
        try:
            dev.close()
        except Exception:
            pass

    def send_command(self, cmd: Command):
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
