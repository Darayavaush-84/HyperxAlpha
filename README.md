# HyperX Alpha

Linux desktop app to manage HyperX Cloud Alpha Wireless headsets.
Built with Python + Qt (PySide6) and hidapi (hidraw).

## Features

- Real-time headset status (connection + battery).
- Headset controls:
  - Sleep timer (`10 / 20 / 30` minutes)
  - Voice prompt toggle
  - Mic monitoring toggle
- Automatic compatible-device detection (hotplug polling), plus manual `Scan Devices`.
- System tray integration:
  - Start hidden / minimize to tray
  - Quick controls from tray menu (voice, mic, sleep timer)
  - Show/hide window and quick log access
- Smart notifications:
  - Debounced connection/disconnection notifications
  - Grouped and rate-limited low-battery notifications
- Persistent preferences (theme, notifications, selected device, mic monitor state).

## Requirements

Fedora example:

```bash
sudo dnf install -y \
  python3 \
  python3-pyside6 \
  hidapi
```

Other distros need equivalent packages:

- Python 3
- PySide6 (Qt 6)
- hidapi with hidraw backend

## Run

```bash
python3 -m hyperxalpha
```

Run as normal user (not `sudo`).

### CLI flags

- `--no-tray` disable tray integration
- `--start-hidden` start hidden when tray is available

### Debug env vars

- `HYPERX_FORCE_SOFTWARE_OPENGL=1` force software OpenGL
- `HYPERX_DEBUG_IO=1` enable verbose RX/TX packet logging
- `HYPERX_LOG_STDOUT=1` mirror app logs to stdout

## Settings

- Preferences file: `~/.config/hyperxalpha/settings.json`
- Autostart file (when enabled): `~/.config/autostart/hyperxalpha.desktop`

## Report new HyperX models

If your HyperX headset is not detected/supported, run:

```bash
python3 probe_hyperx_model.py
```

For machine-readable output:

```bash
python3 probe_hyperx_model.py --json > hyperx-model-report.json
```

Share this information when opening an issue/report:

- full script output (`stdout` or JSON file)
- exact marketing model name (as printed on box/product page)

The report includes VID/PID, HID path, serial (if available), manufacturer/product strings, and a suggested `COMPATIBLE_MODELS` entry.

## Installer (Fedora/Ubuntu/Debian)

```bash
sudo python3 installer.py
```

Installer actions:

- installs required dependencies (when distro is supported)
- writes udev rule for device permissions
- deploys runtime files and launcher
- creates desktop entry (user/system scope)
- stops running HyperX Alpha instances before updating runtime files

Useful options:

- `--check` verify runtime prerequisites
- `--scope user|system` force desktop-entry scope

## Uninstaller

```bash
sudo python3 uninstaller.py
```

Removes files created by installer (udev rule, launcher, desktop entry, runtime, receipt, autostart entries).

## Notes

- On GNOME, a tray extension may be required to display tray icons.
- On some Wayland setups, tray icons may be hidden by default.

## Device permissions (manual udev setup)

```bash
sudo tee /etc/udev/rules.d/50-hyperxalpha.rules >/dev/null <<'EOF_RULE'
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="03f0", ATTRS{idProduct}=="098d", MODE="0660", TAG+="uaccess"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="03f0", ATTRS{idProduct}=="098d", MODE="0660", TAG+="uaccess"
EOF_RULE

sudo udevadm control --reload-rules
sudo udevadm trigger
```
