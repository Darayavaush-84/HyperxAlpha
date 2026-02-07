import unittest
from types import SimpleNamespace
from unittest.mock import patch

import probe_hyperx_model as probe
from hyperxalpha.device import HidUnavailable


def _fake_device(
    *,
    vendor_id,
    product_id,
    path="/dev/hidraw0",
    serial_number=None,
    manufacturer_string=None,
    product_string=None,
    interface_number=0,
):
    return SimpleNamespace(
        vendor_id=vendor_id,
        product_id=product_id,
        path=path,
        serial_number=serial_number,
        manufacturer_string=manufacturer_string,
        product_string=product_string,
        interface_number=interface_number,
    )


class ProbeHyperxModelTests(unittest.TestCase):
    def test_is_hyperx_candidate_true_for_vendor_match(self):
        device = _fake_device(vendor_id=0x03F0, product_id=0x9999)
        self.assertTrue(probe.is_hyperx_candidate(device))

    def test_is_hyperx_candidate_true_for_name_match(self):
        device = _fake_device(
            vendor_id=0x1234,
            product_id=0x5678,
            manufacturer_string="HyperX",
            product_string="Unknown Headset",
        )
        self.assertTrue(probe.is_hyperx_candidate(device))

    def test_is_hyperx_candidate_false_for_non_matching_device(self):
        device = _fake_device(
            vendor_id=0x1111,
            product_id=0x2222,
            manufacturer_string="Generic",
            product_string="USB Device",
        )
        self.assertFalse(probe.is_hyperx_candidate(device))

    def test_device_to_report_item_marks_supported_model(self):
        device = _fake_device(vendor_id=0x03F0, product_id=0x098D)
        item = probe.device_to_report_item(device)
        self.assertTrue(item.already_supported)
        self.assertIsNotNone(item.supported_model_name)
        self.assertIsNone(item.suggested_compatible_models_entry)

    def test_collect_candidate_devices_filters_by_default(self):
        devices = [
            _fake_device(vendor_id=0x03F0, product_id=0x098D),
            _fake_device(vendor_id=0x1111, product_id=0x2222),
        ]
        with patch(
            "probe_hyperx_model.HyperxDevice.list_devices",
            return_value=devices,
        ):
            collected = probe.collect_candidate_devices(include_all=False)

        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0].vendor_id, 0x03F0)

    def test_collect_candidate_devices_include_all(self):
        devices = [
            _fake_device(vendor_id=0x03F0, product_id=0x098D),
            _fake_device(vendor_id=0x1111, product_id=0x2222),
        ]
        with patch(
            "probe_hyperx_model.HyperxDevice.list_devices",
            return_value=devices,
        ):
            collected = probe.collect_candidate_devices(include_all=True)

        self.assertEqual(len(collected), 2)

    def test_build_report_includes_error_when_probe_fails(self):
        with patch(
            "probe_hyperx_model.HyperxDevice.list_devices",
            side_effect=HidUnavailable("hid unavailable"),
        ):
            report = probe.build_report(include_all=False)

        self.assertEqual(report["devices"], [])
        self.assertIn("hid unavailable", report["error"])


if __name__ == "__main__":
    unittest.main()
