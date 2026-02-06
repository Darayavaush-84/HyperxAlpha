import json
import os
import pwd
import shutil
import signal
import subprocess
import time
from pathlib import Path

UDEV_RULE_PATH = Path("/etc/udev/rules.d/50-hyperxalpha.rules")
STATE_DIR = Path("/var/lib/hyperxalpha")
RECEIPT_PATH = STATE_DIR / "install-receipt.json"
DEFAULT_RUNTIME_ROOT = Path("/opt/hyperxalpha")
DEFAULT_LAUNCHER_PATH = Path("/usr/local/bin/hyperxalpha")


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


def _remove_file(path):
    try:
        path.unlink()
        print(f"Removed {path}.")
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        print(f"Failed to remove {path}: {exc}")
        return False


def _remove_tree(path):
    try:
        shutil.rmtree(path)
        print(f"Removed {path}.")
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        print(f"Failed to remove {path}: {exc}")
        return False


def _read_receipt():
    try:
        with open(RECEIPT_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(f"Failed to read install receipt: {exc}")
        return None


def _candidate_homes():
    homes = {Path.home()}
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            homes.add(Path(pwd.getpwnam(sudo_user).pw_dir))
        except KeyError:
            print("Unable to resolve SUDO_USER home directory.")
    return sorted(homes)


def _read_cmdline_tokens(pid):
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return []
    if not raw:
        return []
    return [token for token in raw.decode("utf-8", errors="ignore").split("\x00") if token]


def _is_hyperxalpha_cmdline(tokens):
    if not tokens:
        return False
    for index, token in enumerate(tokens[:-1]):
        if token == "-m" and tokens[index + 1] == "hyperxalpha":
            return True
    for token in tokens:
        if token.endswith("/hyperxalpha/__main__.py"):
            return True
    return False


def _running_hyperxalpha_pids():
    pids = []
    proc_root = Path("/proc")
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        if _is_hyperxalpha_cmdline(_read_cmdline_tokens(pid)):
            pids.append(pid)
    return sorted(set(pids))


def _wait_for_exit(pids, timeout_seconds):
    remaining = set(pids)
    deadline = time.time() + timeout_seconds
    while remaining and time.time() < deadline:
        still_alive = set()
        for pid in remaining:
            try:
                os.kill(pid, 0)
                still_alive.add(pid)
            except ProcessLookupError:
                continue
            except PermissionError:
                still_alive.add(pid)
        remaining = still_alive
        if remaining:
            time.sleep(0.1)
    return remaining


def _kill_running_app():
    if os.geteuid() != 0:
        return False

    pids = _running_hyperxalpha_pids()
    if not pids:
        return False

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue

    remaining = _wait_for_exit(pids, 5.0)
    if remaining:
        for pid in list(remaining):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except PermissionError:
                continue
        remaining = _wait_for_exit(remaining, 2.0)

    if remaining:
        print("Some HyperX Alpha processes could not be terminated:", sorted(remaining))
    return True


def _safe_runtime_root(path):
    resolved = path.resolve(strict=False)
    return str(resolved) == str(DEFAULT_RUNTIME_ROOT) or str(resolved).startswith(
        f"{DEFAULT_RUNTIME_ROOT}/"
    )


def _candidate_desktop_paths(receipt):
    paths = {
        Path("/usr/local/share/applications/hyperxalpha.desktop"),
        Path("/usr/share/applications/hyperxalpha.desktop"),
    }
    if receipt and receipt.get("desktop_entry_path"):
        paths.add(Path(receipt["desktop_entry_path"]))
    for home in _candidate_homes():
        paths.add(home / ".local" / "share" / "applications" / "hyperxalpha.desktop")
    return sorted(paths)


def _candidate_autostart_paths():
    paths = {Path("/etc/xdg/autostart/hyperxalpha.desktop")}
    for home in _candidate_homes():
        paths.add(home / ".config" / "autostart" / "hyperxalpha.desktop")
    return sorted(paths)


def _candidate_launcher_paths(receipt):
    paths = {DEFAULT_LAUNCHER_PATH}
    if receipt and receipt.get("launcher_path"):
        paths.add(Path(receipt["launcher_path"]))
    return sorted(paths)


def _candidate_runtime_roots(receipt):
    roots = {DEFAULT_RUNTIME_ROOT}
    if receipt and receipt.get("runtime_root"):
        roots.add(Path(receipt["runtime_root"]))
    if receipt and receipt.get("runtime_package_dir"):
        roots.add(Path(receipt["runtime_package_dir"]).parent)

    safe_roots = []
    for root in roots:
        if _safe_runtime_root(root):
            safe_roots.append(root)
        else:
            print(f"Skipping unsafe runtime path from receipt: {root}")
    return sorted(set(safe_roots))


def _collect_leftovers(receipt):
    leftovers = []
    udev_candidate = (
        Path(receipt.get("udev_rule_path"))
        if receipt and receipt.get("udev_rule_path")
        else UDEV_RULE_PATH
    )
    if udev_candidate.exists():
        leftovers.append(udev_candidate)

    for path in _candidate_desktop_paths(receipt):
        if path.exists():
            leftovers.append(path)

    for path in _candidate_autostart_paths():
        if path.exists():
            leftovers.append(path)

    for path in _candidate_launcher_paths(receipt):
        if path.exists():
            leftovers.append(path)

    for path in _candidate_runtime_roots(receipt):
        if path.exists():
            leftovers.append(path)

    if RECEIPT_PATH.exists():
        leftovers.append(RECEIPT_PATH)

    return sorted(set(leftovers))


def uninstall():
    ok = True
    removed_any = False
    receipt = _read_receipt()

    if os.geteuid() != 0:
        print("Please run with sudo to remove installed system files.")
        ok = False
    else:
        if _kill_running_app():
            print("Stopped running HyperX Alpha instance.")

        udev_path = (
            Path(receipt.get("udev_rule_path"))
            if receipt and receipt.get("udev_rule_path")
            else UDEV_RULE_PATH
        )
        if _remove_file(udev_path):
            removed_any = True
            if not _reload_udev_rules():
                ok = False

        for launcher_path in _candidate_launcher_paths(receipt):
            if _remove_file(launcher_path):
                removed_any = True

        for runtime_root in _candidate_runtime_roots(receipt):
            if _remove_tree(runtime_root):
                removed_any = True

    for desktop_path in _candidate_desktop_paths(receipt):
        if _remove_file(desktop_path):
            removed_any = True

    for autostart_path in _candidate_autostart_paths():
        if _remove_file(autostart_path):
            removed_any = True

    if _remove_file(RECEIPT_PATH):
        removed_any = True

    try:
        STATE_DIR.rmdir()
    except OSError:
        pass

    leftovers = _collect_leftovers(receipt)
    if leftovers:
        ok = False
        print("Uninstall incomplete, leftover files:")
        for path in leftovers:
            print(f"  - {path}")

    if not removed_any:
        print("Nothing to remove.")

    if ok and removed_any:
        print("Uninstall complete.")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(uninstall())
