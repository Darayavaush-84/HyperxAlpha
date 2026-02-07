import unittest

from hyperxalpha.device import HidIoError
from hyperxalpha.device_service import DeviceReader


class _FailingReadService:
    def __init__(self, exc):
        self._exc = exc

    def read(self, timeout_ms=100):
        raise self._exc


class DeviceReaderTests(unittest.TestCase):
    def test_emits_io_failed_on_hid_io_error(self):
        errors = []
        reader = DeviceReader(_FailingReadService(HidIoError("hid-fail")))
        reader.io_failed.connect(errors.append)

        reader.run()

        self.assertEqual(errors, ["hid-fail"])

    def test_emits_io_failed_on_unexpected_error(self):
        errors = []
        reader = DeviceReader(_FailingReadService(ValueError("boom")))
        reader.io_failed.connect(errors.append)

        reader.run()

        self.assertEqual(errors, ["Unexpected device read error: boom"])


if __name__ == "__main__":
    unittest.main()
