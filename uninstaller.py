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
    except json.JSONDecodeError as exc:
        print(f"Install receipt is corrupted and will be ignored: {exc}")
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
    if os.geteuid() == 0:
        try:
            users = pwd.getpwall()
        except OSError:
            users = []
        for user in users:
            if user.pw_uid < 1000:
                continue
            shell = user.pw_shell or ""
            if shell.endswith("nologin") or shell.endswith("false"):
                continue
            home = Path(user.pw_dir)
            if home.is_absolute():
                homes.add(home)
    return sorted(homes)


def _install_scope_from_receipt(receipt):
    if not receipt:
        return None
    scope = receipt.get("install_scope")
    if not isinstance(scope, str):
        return None
    normalized = scope.strip().lower()
    if normalized in {"system", "user"}:
        return normalized
    return None


def _invoking_homes():
    homes = {Path.home()}
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            homes.add(Path(pwd.getpwnam(sudo_user).pw_dir))
        except KeyError:
            print("Unable to resolve SUDO_USER home directory.")
    return sorted(homes)


def _receipt_user_home(receipt):
    if not receipt:
        return None
    raw_home = receipt.get("install_user_home")
    if raw_home:
        try:
            resolved = Path(raw_home).resolve(strict=False)
            if resolved.is_absolute():
                return resolved
        except (OSError, RuntimeError):
            pass

    raw_desktop = receipt.get("desktop_entry_path")
    if not raw_desktop:
        return None
    try:
        desktop_path = Path(raw_desktop).resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if desktop_path.name != "hyperxalpha.desktop":
        return None
    applications_dir = desktop_path.parent
    if applications_dir.name != "applications":
        return None
    share_dir = applications_dir.parent
    if share_dir.name != "share":
        return None
    local_dir = share_dir.parent
    if local_dir.name != ".local":
        return None
    home = local_dir.parent
    return home if home.is_absolute() else None


def _scoped_homes(receipt):
    if _install_scope_from_receipt(receipt) != "user":
        return _candidate_homes()
    receipt_home = _receipt_user_home(receipt)
    if receipt_home is not None:
        return [receipt_home]
    return _invoking_homes()


def _path_in_allowed_dirs(path, allowed_dirs, expected_name):
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    if resolved.name != expected_name:
        return False
    for directory in allowed_dirs:
        try:
            directory_resolved = directory.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if resolved.parent == directory_resolved:
            return True
    return False


def _safe_udev_path(path):
    try:
        return path.resolve(strict=False) == UDEV_RULE_PATH.resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def _safe_desktop_path(path):
    allowed_dirs = [
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
    ]
    allowed_dirs.extend(
        home / ".local" / "share" / "applications" for home in _candidate_homes()
    )
    return _path_in_allowed_dirs(path, allowed_dirs, "hyperxalpha.desktop")


def _safe_launcher_path(path):
    allowed_dirs = [DEFAULT_LAUNCHER_PATH.parent]
    allowed_dirs.extend(home / ".local" / "bin" for home in _candidate_homes())
    return _path_in_allowed_dirs(path, allowed_dirs, "hyperxalpha")


def _receipt_path_if_safe(receipt, key, validator, label):
    if not receipt:
        return None
    raw_value = receipt.get(key)
    if not raw_value:
        return None
    path = Path(raw_value)
    if validator(path):
        return path
    print(f"Ignoring unsafe {label} path from receipt: {path}")
    return None


def _read_cmdline_tokens(pid):
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return []
    if not raw:
        return []
    return [token for token in raw.decode("utf-8", errors="ignore").split("\x00") if token]


def _candidate_launcher_tokens(receipt):
    return {str(path) for path in _candidate_launcher_paths(receipt)}


def _is_python_command(token):
    name = Path(token).name.lower()
    return name.startswith("python")


def _is_hyperxalpha_cmdline(tokens, launcher_tokens=None):
    if not tokens:
        return False
    if launcher_tokens:
        if tokens[0] in launcher_tokens:
            return True
        if len(tokens) >= 2 and _is_python_command(tokens[0]) and tokens[1] in launcher_tokens:
            return True
        if (
            len(tokens) >= 3
            and Path(tokens[0]).name == "env"
            and _is_python_command(tokens[1])
            and tokens[2] in launcher_tokens
        ):
            return True
    for index, token in enumerate(tokens[:-1]):
        if token == "-m" and tokens[index + 1] == "hyperxalpha":
            return True
    for token in tokens:
        if token.endswith("/hyperxalpha/__main__.py"):
            return True
    return False


def _running_hyperxalpha_pids(launcher_tokens=None):
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
        if _is_hyperxalpha_cmdline(
            _read_cmdline_tokens(pid),
            launcher_tokens=launcher_tokens,
        ):
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


def _kill_running_app(receipt=None):
    if os.geteuid() != 0:
        return False, False

    launcher_tokens = _candidate_launcher_tokens(receipt)
    pids = _running_hyperxalpha_pids(launcher_tokens=launcher_tokens)
    if not pids:
        return False, True

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
    return True, not remaining


def _candidate_udev_path(receipt):
    path = _receipt_path_if_safe(
        receipt,
        "udev_rule_path",
        validator=_safe_udev_path,
        label="udev rule",
    )
    return path if path is not None else UDEV_RULE_PATH


def _safe_runtime_root(path):
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    return str(resolved) == str(DEFAULT_RUNTIME_ROOT) or str(resolved).startswith(
        f"{DEFAULT_RUNTIME_ROOT}/"
    )


def _candidate_desktop_paths(receipt):
    scope = _install_scope_from_receipt(receipt)
    paths = set()
    if scope in (None, "system"):
        paths.update(
            {
                Path("/usr/local/share/applications/hyperxalpha.desktop"),
                Path("/usr/share/applications/hyperxalpha.desktop"),
            }
        )
    receipt_path = _receipt_path_if_safe(
        receipt,
        "desktop_entry_path",
        validator=_safe_desktop_path,
        label="desktop entry",
    )
    if receipt_path is not None:
        paths.add(receipt_path)
    if scope in (None, "user"):
        for home in _scoped_homes(receipt):
            paths.add(home / ".local" / "share" / "applications" / "hyperxalpha.desktop")
    return sorted(paths)


def _candidate_autostart_paths(receipt=None):
    scope = _install_scope_from_receipt(receipt)
    paths = set()
    if scope in (None, "system"):
        paths.add(Path("/etc/xdg/autostart/hyperxalpha.desktop"))
    if scope in (None, "user"):
        for home in _scoped_homes(receipt):
            paths.add(home / ".config" / "autostart" / "hyperxalpha.desktop")
    return sorted(paths)


def _candidate_launcher_paths(receipt):
    paths = {DEFAULT_LAUNCHER_PATH}
    receipt_path = _receipt_path_if_safe(
        receipt,
        "launcher_path",
        validator=_safe_launcher_path,
        label="launcher",
    )
    if receipt_path is not None:
        paths.add(receipt_path)
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
    udev_candidate = _candidate_udev_path(receipt)
    if udev_candidate.exists():
        leftovers.append(udev_candidate)

    for path in _candidate_desktop_paths(receipt):
        if path.exists():
            leftovers.append(path)

    for path in _candidate_autostart_paths(receipt):
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
        had_running, stopped = _kill_running_app(receipt=receipt)
        if had_running and stopped:
            print("Stopped running HyperX Alpha instance.")
        elif had_running and not stopped:
            print("Cannot continue uninstall while HyperX Alpha is still running.")
            return 1

        udev_path = _candidate_udev_path(receipt)
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

    for autostart_path in _candidate_autostart_paths(receipt):
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
