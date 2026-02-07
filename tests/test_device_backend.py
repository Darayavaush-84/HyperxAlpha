import unittest
from unittest.mock import patch

from hyperxalpha.device import (
    HidUnavailable,
    HyperxDevice,
    _HidrawBackend,
    _HidrawHandle,
)


class _FakeLib:
    def __init__(self):
        self.path_result = None
        self.open_result = object()
        self.open_path_calls = 0
        self.open_calls = 0
        self.nonblocking_calls = 0

    def hid_open_path(self, _path):
        self.open_path_calls += 1
        return self.path_result

    def hid_open(self, _vendor_id, _product_id, _serial):
        self.open_calls += 1
        return self.open_result

    def hid_set_nonblocking(self, _handle, _mode):
        self.nonblocking_calls += 1


class HidrawBackendTests(unittest.TestCase):
    def test_open_with_device_path_fails_when_open_path_unavailable(self):
        backend = _HidrawBackend.__new__(_HidrawBackend)
        backend._lib = _FakeLib()
        backend._has_open_path = False

        with self.assertRaises(OSError):
            backend.open(0x03F0, 0x098D, device_path="/dev/hidraw0")

    def test_open_with_device_path_does_not_fallback_to_vid_pid(self):
        backend = _HidrawBackend.__new__(_HidrawBackend)
        backend._lib = _FakeLib()
        backend._has_open_path = True

        with self.assertRaises(OSError):
            backend.open(0x03F0, 0x098D, device_path="/dev/hidraw0")

        self.assertEqual(backend._lib.open_path_calls, 1)
        self.assertEqual(backend._lib.open_calls, 0)

    def test_open_with_device_path_returns_handle_when_open_path_succeeds(self):
        backend = _HidrawBackend.__new__(_HidrawBackend)
        backend._lib = _FakeLib()
        backend._has_open_path = True
        backend._lib.path_result = object()

        handle = backend.open(0x03F0, 0x098D, device_path="/dev/hidraw0")

        self.assertIsInstance(handle, _HidrawHandle)
        self.assertEqual(backend._lib.open_path_calls, 1)
        self.assertEqual(backend._lib.open_calls, 0)
        self.assertEqual(backend._lib.nonblocking_calls, 1)

    def test_shared_backend_initializes_hidapi_once(self):
        class _Fn:
            def __init__(self, return_value=0):
                self.return_value = return_value
                self.calls = 0

            def __call__(self, *_args, **_kwargs):
                self.calls += 1
                return self.return_value

        class _SharedFakeLib:
            def __init__(self):
                self.hid_init = _Fn(0)
                self.hid_exit = _Fn(0)
                self.hid_open = _Fn(1)
                self.hid_close = _Fn(0)
                self.hid_write = _Fn(0)
                self.hid_read_timeout = _Fn(0)
                self.hid_error = _Fn("")

        fake_lib = _SharedFakeLib()
        original_state = (
            _HidrawBackend._shared_lib,
            _HidrawBackend._shared_has_open_path,
            _HidrawBackend._shared_has_enumerate,
            _HidrawBackend._shared_initialized,
            _HidrawBackend._shared_shutdown_registered,
        )
        _HidrawBackend._shared_lib = None
        _HidrawBackend._shared_has_open_path = False
        _HidrawBackend._shared_has_enumerate = False
        _HidrawBackend._shared_initialized = False
        _HidrawBackend._shared_shutdown_registered = False
        try:
            with patch("hyperxalpha.device.ctypes.CDLL", return_value=fake_lib) as mocked_cdll:
                _HidrawBackend()
                _HidrawBackend()
                self.assertEqual(mocked_cdll.call_count, 1)
                self.assertEqual(fake_lib.hid_init.calls, 1)
        finally:
            _HidrawBackend._shutdown_shared_backend()
            (
                _HidrawBackend._shared_lib,
                _HidrawBackend._shared_has_open_path,
                _HidrawBackend._shared_has_enumerate,
                _HidrawBackend._shared_initialized,
                _HidrawBackend._shared_shutdown_registered,
            ) = original_state

    def test_list_devices_includes_original_exception_details(self):
        with patch(
            "hyperxalpha.device._HidrawBackend",
            side_effect=OSError("missing hidraw lib"),
        ):
            with self.assertRaises(HidUnavailable) as exc_ctx:
                HyperxDevice.list_devices()

        message = str(exc_ctx.exception)
        self.assertIn("Cause: OSError: missing hidraw lib", message)

    def test_open_includes_target_and_original_exception_details(self):
        class _FailingBackend:
            def open(self, *_args, **_kwargs):
                raise PermissionError("denied")

        device = HyperxDevice()
        device.set_target(device_path="/dev/hidraw42")

        with patch("hyperxalpha.device._HidrawBackend", return_value=_FailingBackend()):
            with self.assertRaises(HidUnavailable) as exc_ctx:
                device.open()

        message = str(exc_ctx.exception)
        self.assertIn("path=/dev/hidraw42", message)
        self.assertIn("Cause: PermissionError: denied", message)


if __name__ == "__main__":
    unittest.main()
