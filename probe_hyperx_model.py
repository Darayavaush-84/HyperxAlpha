#!/usr/bin/env python3
"""Collect HID information useful to add new HyperX models."""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from hyperxalpha.constants import COMPATIBLE_MODELS, VENDOR_ID
from hyperxalpha.device import HidUnavailable, HyperxDevice


@dataclass(frozen=True)
class ProbeItem:
    vendor_id: int
    product_id: int
    vid_pid: str
    path: str | None
    serial_number: str | None
    manufacturer_string: str | None
    product_string: str | None
    interface_number: int | None
    already_supported: bool
    supported_model_name: str | None
    suggested_compatible_models_entry: str | None


def _normalize_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _vid_pid(vendor_id, product_id):
    return f"{int(vendor_id):04X}:{int(product_id):04X}"


def is_hyperx_candidate(device):
    text_blob = " ".join(
        part
        for part in (
            _normalize_text(device.manufacturer_string),
            _normalize_text(device.product_string),
        )
        if part
    ).lower()
    return int(device.vendor_id) == int(VENDOR_ID) or "hyperx" in text_blob


def device_to_report_item(device):
    vendor_id = int(device.vendor_id)
    product_id = int(device.product_id)
    model_key = (vendor_id, product_id)
    known_name = COMPATIBLE_MODELS.get(model_key)
    is_supported = known_name is not None
    suggestion = None
    if not is_supported:
        suggestion = (
            f"(0x{vendor_id:04X}, 0x{product_id:04X}): "
            '"Model Name Here",'
        )
    return ProbeItem(
        vendor_id=vendor_id,
        product_id=product_id,
        vid_pid=_vid_pid(vendor_id, product_id),
        path=_normalize_text(device.path),
        serial_number=_normalize_text(device.serial_number),
        manufacturer_string=_normalize_text(device.manufacturer_string),
        product_string=_normalize_text(device.product_string),
        interface_number=(
            int(device.interface_number)
            if device.interface_number is not None
            else None
        ),
        already_supported=is_supported,
        supported_model_name=known_name,
        suggested_compatible_models_entry=suggestion,
    )


def collect_candidate_devices(include_all=False):
    devices = HyperxDevice.list_devices(vendor_id=0, product_id=0)
    filtered = []
    for device in devices:
        if include_all or is_hyperx_candidate(device):
            filtered.append(device_to_report_item(device))
    filtered.sort(
        key=lambda item: (
            item.vendor_id,
            item.product_id,
            item.path or "",
            item.serial_number or "",
        )
    )
    return filtered


def build_report(include_all=False):
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "filter": (
            "all hid devices"
            if include_all
            else f"vendor 0x{VENDOR_ID:04X} or product/manufacturer name contains 'HyperX'"
        ),
        "known_model_count": len(COMPATIBLE_MODELS),
        "devices": [],
        "error": None,
    }
    try:
        devices = collect_candidate_devices(include_all=include_all)
    except HidUnavailable as exc:
        report["error"] = str(exc)
        return report

    report["devices"] = [asdict(item) for item in devices]
    return report


def print_human_report(report):
    print("HyperX model probe report")
    print(f"Generated (UTC): {report['generated_at_utc']}")
    print(f"Platform: {report['platform']}")
    print(f"Filter: {report['filter']}")
    print(f"Known models in app: {report['known_model_count']}")
    print("")

    if report.get("error"):
        print("Probe error:")
        print(f"  {report['error']}")
        return

    devices = report.get("devices", [])
    if not devices:
        print("No matching devices found.")
        print("Tip: connect/power on headset, then rerun this script.")
        return

    for index, device in enumerate(devices, start=1):
        print(f"[{index}] VID:PID {device['vid_pid']}")
        print(f"  Path: {device.get('path') or '-'}")
        print(
            "  Manufacturer/Product: "
            f"{device.get('manufacturer_string') or '-'} / "
            f"{device.get('product_string') or '-'}"
        )
        print(f"  Serial: {device.get('serial_number') or '-'}")
        print(f"  Interface: {device.get('interface_number')}")
        if device.get("already_supported"):
            print(f"  Status: already supported ({device['supported_model_name']})")
        else:
            print("  Status: not in COMPATIBLE_MODELS")
            print(
                "  Suggested entry: "
                f"{device['suggested_compatible_models_entry']}"
            )
        print("")

    print("When reporting a new model, share:")
    print("  1. Full output of this script")
    print("  2. Marketing model name as printed on the box")


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Probe HID devices and report info needed to add HyperX models."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="include all HID devices, not only HyperX-like candidates",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON report",
    )
    return parser


def main(argv=None):
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    report = build_report(include_all=args.all)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human_report(report)

    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
