import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
import pwd
import ctypes

UDEV_RULE_PATH = "/etc/udev/rules.d/50-hyperxalpha.rules"
UDEV_RULE_LINES = [
    'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="03f0", ATTRS{idProduct}=="098d", MODE="0660", TAG+="uaccess"',
    'SUBSYSTEMS=="usb", ATTRS{idVendor}=="03f0", ATTRS{idProduct}=="098d", MODE="0660", TAG+="uaccess"',
]
UDEV_RULE_CONTENT = "# HyperX Alpha Wireless permissions\n" + "\n".join(
    UDEV_RULE_LINES
) + "\n"
STATE_DIR = "/var/lib/hyperxalpha"
RECEIPT_PATH = f"{STATE_DIR}/install-receipt.json"
RUNTIME_ROOT = Path("/opt/hyperxalpha")
RUNTIME_PACKAGE_DIR = RUNTIME_ROOT / "hyperxalpha"
LAUNCHER_PATH = Path("/usr/local/bin/hyperxalpha")


def _read_os_release():
    data = {}
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key] = value.strip().strip('"')
    except OSError:
        return {}
    return data


def _is_ubuntu_like():
    data = _read_os_release()
    distro_id = data.get("ID", "").lower()
    if distro_id in {"ubuntu", "debian"}:
        return True
    id_like = data.get("ID_LIKE", "").lower()
    return "ubuntu" in id_like or "debian" in id_like


def _is_fedora_like():
    data = _read_os_release()
    distro_id = data.get("ID", "").lower()
    if distro_id == "fedora":
        return True
    id_like = data.get("ID_LIKE", "").lower()
    return "fedora" in id_like or "rhel" in id_like


def _check_qt():
    try:
        import PySide6  # noqa: F401
    except ImportError as exc:
        return False, str(exc)
    return True, None


def _apt_install(packages):
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    cmd = sudo + ["apt-get", "install", "-y"] + list(packages)
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def _dnf_install(packages):
    cmd = ["dnf", "install", "-y"] + list(packages)
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def _reload_udev_rules():
    for cmd in (["udevadm", "control", "--reload-rules"], ["udevadm", "trigger"]):
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            print("udevadm not found; please reload udev rules manually.")
            return False
        except subprocess.CalledProcessError:
            print("Failed to reload udev rules; please check your system.")
            return False
    return True


def _install_udev_rule():
    if os.geteuid() != 0:
        print("Skipping udev rule install (run the installer with sudo).")
        return False

    existing = ""
    try:
        with open(UDEV_RULE_PATH, "r", encoding="utf-8") as handle:
            existing = handle.read()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"Failed to read {UDEV_RULE_PATH}: {exc}")
        return False

    if existing and all(line in existing for line in UDEV_RULE_LINES):
        print("udev rule already present.")
        return True

    try:
        with open(UDEV_RULE_PATH, "w", encoding="utf-8") as handle:
            handle.write(UDEV_RULE_CONTENT)
    except OSError as exc:
        print(f"Failed to write {UDEV_RULE_PATH}: {exc}")
        return False

    if existing:
        print(f"Updated udev rule at {UDEV_RULE_PATH}.")
    else:
        print(f"Wrote udev rule to {UDEV_RULE_PATH}.")
    return _reload_udev_rules()


def _escape_desktop_value(value):
    return value.replace("\\", "\\\\").replace(" ", "\\ ")


def _runtime_copy_ignore(_dir, names):
    ignored = set()
    for name in names:
        if name in {"__pycache__", "old"}:
            ignored.add(name)
            continue
        if name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def _install_runtime_files():
    source_package_dir = Path(__file__).resolve().parent
    try:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        if RUNTIME_PACKAGE_DIR.exists():
            shutil.rmtree(RUNTIME_PACKAGE_DIR)
        shutil.copytree(
            source_package_dir,
            RUNTIME_PACKAGE_DIR,
            ignore=_runtime_copy_ignore,
        )
    except OSError as exc:
        print(f"Failed to install runtime files in {RUNTIME_ROOT}: {exc}")
        return False

    print(f"Runtime files installed in {RUNTIME_PACKAGE_DIR}.")
    return True


def _launcher_script_content():
    return (
        "#!/usr/bin/env python3\n"
        "import runpy\n"
        "import sys\n"
        f"sys.path.insert(0, {str(RUNTIME_ROOT)!r})\n"
        "runpy.run_module('hyperxalpha', run_name='__main__')\n"
    )


def _install_launcher():
    try:
        LAUNCHER_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHER_PATH.write_text(_launcher_script_content(), encoding="utf-8")
        LAUNCHER_PATH.chmod(0o755)
    except OSError as exc:
        print(f"Failed to install launcher at {LAUNCHER_PATH}: {exc}")
        return False

    print(f"Launcher installed at {LAUNCHER_PATH}.")
    return True


def _desktop_icon_path():
    candidates = (
        RUNTIME_PACKAGE_DIR / "assets" / "img" / "hyperx.png",
        Path(__file__).resolve().parent / "assets" / "img" / "hyperx.png",
    )
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def _desktop_exec_value():
    return _escape_desktop_value(str(LAUNCHER_PATH))


def _desktop_entry_content():
    icon_value = _escape_desktop_value(str(_desktop_icon_path()))
    exec_value = _desktop_exec_value()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=HyperX Alpha\n"
        "Comment=HyperX Cloud Alpha Wireless control\n"
        f"Exec={exec_value}\n"
        f"Icon={icon_value}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Settings;\n"
        "StartupNotify=true\n"
    )


def _install_desktop_entry():
    sudo_user = os.environ.get("SUDO_USER")

    if sudo_user:
        try:
            user_info = pwd.getpwnam(sudo_user)
        except KeyError:
            print("Unable to resolve SUDO_USER for desktop entry.")
            return None
        app_dir = Path(user_info.pw_dir) / ".local" / "share" / "applications"
        uid = user_info.pw_uid
        gid = user_info.pw_gid
    else:
        app_dir = Path.home() / ".local" / "share" / "applications"
        uid = os.geteuid()
        gid = os.getegid()

    try:
        app_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Failed to create {app_dir}: {exc}")
        return None

    entry_path = app_dir / "hyperxalpha.desktop"
    try:
        entry_path.write_text(_desktop_entry_content(), encoding="utf-8")
    except OSError as exc:
        print(f"Failed to write {entry_path}: {exc}")
        return None

    try:
        os.chown(entry_path, uid, gid)
        os.chown(app_dir, uid, gid)
    except PermissionError:
        pass

    print(f"Desktop entry written to {entry_path}.")
    return entry_path


def _prompt_install_scope():
    if not sys.stdin.isatty():
        return "system"
    reply = input("Install desktop entry for all users? [Y/n] ").strip() or "Y"
    return "system" if reply.lower().startswith("y") else "user"


def _install_desktop_entry_with_scope(scope):
    if scope == "system":
        app_dir = Path("/usr/share/applications")
        entry_path = app_dir / "hyperxalpha.desktop"
        try:
            app_dir.mkdir(parents=True, exist_ok=True)
            entry_path.write_text(_desktop_entry_content(), encoding="utf-8")
        except OSError as exc:
            print(f"Failed to write {entry_path}: {exc}")
            return None
        print(f"Desktop entry written to {entry_path} (all users).")
        return entry_path

    return _install_desktop_entry()


def _check_hidraw_lib():
    for name in ("libhidapi-hidraw.so.0", "libhidapi-hidraw.so"):
        try:
            ctypes.CDLL(name)
            return True
        except OSError:
            continue
    return False


def _write_install_receipt(data):
    try:
        Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
        with open(RECEIPT_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        print(f"Install receipt written to {RECEIPT_PATH}.")
        return True
    except OSError as exc:
        print(f"Failed to write install receipt: {exc}")
        return False


def install_all():
    if os.geteuid() != 0:
        print("Please run the installer with sudo.")
        return False

    ok = True
    package_manager = None
    packages_requested = []
    install_scope = _prompt_install_scope()

    if _is_ubuntu_like():
        package_manager = "apt"
        base_packages = [
            "python3",
            "python3-pyside6",
            "libhidapi-hidraw0",
        ]
        packages_requested = list(base_packages)

        print("Installing required packages:")
        print("  " + " ".join(base_packages))
        if not _apt_install(base_packages):
            print("Failed to install base packages.")
            ok = False
    elif _is_fedora_like():
        package_manager = "dnf"
        base_packages = [
            "python3",
            "python3-pyside6",
            "hidapi",
        ]
        packages_requested = list(base_packages)

        print("Installing required packages:")
        print("  " + " ".join(base_packages))
        if not _dnf_install(base_packages):
            print("Failed to install base packages.")
            ok = False
    else:
        print("Package install skipped (unsupported distro).")
        print("Please install the dependencies listed in README.md.")
        ok = False

    if not _install_udev_rule():
        ok = False

    if not _install_runtime_files():
        ok = False

    if not _install_launcher():
        ok = False

    desktop_entry_path = _install_desktop_entry_with_scope(install_scope)
    if desktop_entry_path is None:
        ok = False

    if _check_hidraw_lib():
        print("hidraw backend available.")
    else:
        print("hidraw backend not found. Install libhidapi-hidraw and retry.")
        ok = False

    if Path(UDEV_RULE_PATH).exists():
        print("udev rule present.")
    else:
        print("udev rule missing.")
        ok = False

    receipt_data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "distro": _read_os_release().get("PRETTY_NAME", "unknown"),
        "package_manager": package_manager,
        "packages_requested": packages_requested,
        "udev_rule_path": UDEV_RULE_PATH,
        "desktop_entry_path": str(desktop_entry_path) if desktop_entry_path else None,
        "install_scope": install_scope,
        "runtime_root": str(RUNTIME_ROOT),
        "runtime_package_dir": str(RUNTIME_PACKAGE_DIR),
        "launcher_path": str(LAUNCHER_PATH),
    }
    if not _write_install_receipt(receipt_data):
        ok = False

    if ok:
        print("Done.")
    return ok


def main():
    parser = argparse.ArgumentParser(
        description="HyperX Alpha dependency installer."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check Qt and hidraw availability.",
    )
    args = parser.parse_args()

    if args.check:
        ok, reason = _check_qt()
        if ok:
            print("PySide6 is available.")
        else:
            print(f"PySide6 not available: {reason}")
        if _check_hidraw_lib():
            print("hidraw backend available.")
        else:
            print("hidraw backend not found. Install libhidapi-hidraw and retry.")
            ok = False
        if Path(UDEV_RULE_PATH).exists():
            print("udev rule present.")
        else:
            print("udev rule missing.")
        if LAUNCHER_PATH.is_file():
            print(f"Launcher present: {LAUNCHER_PATH}")
        else:
            print(f"Launcher missing: {LAUNCHER_PATH}")
        return 0 if ok else 1

    return 0 if install_all() else 1


if __name__ == "__main__":
    raise SystemExit(main())
