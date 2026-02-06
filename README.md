# HyperX Alpha (Python rewrite)

This is a Python rewrite of the HyperX Alpha Wireless app. It uses Qt 6
(PySide6) and hidapi (hidraw) via system packages (no venv required).

## Fedora 43 dependencies

```bash
sudo dnf install -y \
  python3 \
  python3-pyside6 \
  hidapi
```

On GNOME, a system tray extension may be required for tray icons to be visible.

## Other Linux distros (example packages)

- Python 3, PySide6 (Qt 6), hidapi (hidraw)

Package names vary by distro, but the runtime pieces are the same.

## Systray notes

- Tray uses Qt's QSystemTrayIcon.
- GNOME typically requires a tray extension to show icons.
- On some Wayland setups, tray icons may be hidden by default.

## Run

```bash
python3 -m hyperxalpha
```

Run the app as a normal user (no sudo).

### Flags

- `--no-tray` Disable tray integration
- `--start-hidden` Start hidden when tray is available

### Performance / debug env vars

- `HYPERX_FORCE_SOFTWARE_OPENGL=1` force software OpenGL fallback (disabled by default)
- `HYPERX_DEBUG_IO=1` enable verbose RX/TX packet logging

## Autostart + settings

Enable "Always start in Systray" in the UI to create an autostart entry.
Preferences are stored in `~/.config/hyperxalpha/settings.json`.

## Install helper (Fedora/Ubuntu/Debian)

```bash
sudo python3 hyperxalpha/installer.py
```

This installs dependencies, writes the udev rule, and creates a desktop entry
for KDE/GNOME so the app appears in the launcher. The installer asks whether
to install the desktop entry for all users or only the current user.
All-users entries are written to `/usr/share/applications`.
Runtime files are installed to `/opt/hyperxalpha` and a launcher is written to
`/usr/local/bin/hyperxalpha`.
Use `--check` to verify Qt/hidraw availability (no sudo required).
The installer also writes an install receipt to `/var/lib/hyperxalpha/install-receipt.json`.

## Uninstall helper

```bash
sudo python3 hyperxalpha/uninstaller.py
```

This removes the udev rule, desktop entry, launcher, runtime files, and autostart
entry created by the installer.
It does not remove system packages, since they may be shared with other apps.

## Device permissions (udev rule)

The installer above writes this rule automatically when run with sudo.
Manual setup:

```bash
sudo tee /etc/udev/rules.d/50-hyperxalpha.rules >/dev/null <<'EOF_RULE'
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="03f0", ATTRS{idProduct}=="098d", MODE="0660", TAG+="uaccess"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="03f0", ATTRS{idProduct}=="098d", MODE="0660", TAG+="uaccess"
EOF_RULE

sudo udevadm control --reload-rules
sudo udevadm trigger
```

## Changelog

See `CHANGELOG.md`.
