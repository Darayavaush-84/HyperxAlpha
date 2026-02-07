import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import pwd
import ctypes
import urllib.error
import urllib.parse
import urllib.request

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
SOURCE_PACKAGE_DIR = Path(__file__).resolve().parent / "hyperxalpha"
RUNTIME_MODULE_FILES = (
    "__init__.py",
    "__main__.py",
    "constants.py",
    "controller.py",
    "device.py",
    "device_service.py",
    "settings.py",
    "settings_service.py",
    "ui.py",
    "view.py",
)
RUNTIME_RESOURCE_DIRS = ("assets",)
GITHUB_DEFAULT_REPO = "Darayavaush-84/HyperxAlpha"
GITHUB_RELEASES_PER_PAGE = 10
GITHUB_MAX_CHANGELOG_RELEASES = 3
GITHUB_MAX_CHANGELOG_LINES = 10
_SEMVER_RE = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def _source_python_modules(source_package_dir):
    return sorted(
        path.name for path in source_package_dir.glob("*.py") if path.is_file()
    )


def _extract_github_repo(remote_url):
    if not remote_url:
        return None
    candidate = str(remote_url).strip()
    if not candidate:
        return None
    patterns = (
        r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        matched = re.match(pattern, candidate)
        if matched:
            owner, repo = matched.groups()
            return f"{owner}/{repo}"
    return None


def _resolve_github_repo():
    from_env = os.environ.get("HYPERX_GITHUB_REPO", "").strip()
    if from_env:
        return from_env

    git_config_path = Path(__file__).resolve().parent / ".git" / "config"
    try:
        content = git_config_path.read_text(encoding="utf-8")
    except OSError:
        return GITHUB_DEFAULT_REPO

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("url ="):
            continue
        _key, raw_url = stripped.split("=", 1)
        parsed = _extract_github_repo(raw_url.strip())
        if parsed:
            return parsed
    return GITHUB_DEFAULT_REPO


def _read_local_version():
    init_path = SOURCE_PACKAGE_DIR / "__init__.py"
    try:
        content = init_path.read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    matched = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", content)
    if not matched:
        return "0.0.0"
    return matched.group(1).strip()


def _parse_semver(version):
    if not isinstance(version, str):
        return None
    matched = _SEMVER_RE.match(version.strip())
    if not matched:
        return None
    return tuple(int(part) for part in matched.groups())


def _fetch_github_releases(repo, per_page=GITHUB_RELEASES_PER_PAGE):
    safe_repo = urllib.parse.quote(repo, safe="/")
    safe_page = max(1, int(per_page))
    url = (
        f"https://api.github.com/repos/{safe_repo}/releases"
        f"?per_page={safe_page}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "hyperxalpha-installer",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return None, f"GitHub API HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return None, f"GitHub API unreachable: {exc.reason}"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"Invalid GitHub API response: {exc}"

    if not isinstance(payload, list):
        return None, "Unexpected GitHub API payload."
    return payload, None


def _collect_stable_semver_releases(payload):
    releases = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("draft") or item.get("prerelease"):
            continue
        tag_name = str(item.get("tag_name") or "").strip()
        version_tuple = _parse_semver(tag_name)
        if version_tuple is None:
            continue
        releases.append(
            {
                "tag_name": tag_name,
                "version_tuple": version_tuple,
                "name": str(item.get("name") or "").strip(),
                "body": str(item.get("body") or ""),
                "published_at": str(item.get("published_at") or ""),
                "html_url": str(item.get("html_url") or "").strip(),
            }
        )
    return sorted(releases, key=lambda entry: entry["version_tuple"], reverse=True)


def _newer_releases(local_version, releases):
    local_tuple = _parse_semver(local_version)
    if local_tuple is None:
        return []
    return [
        entry for entry in releases if entry.get("version_tuple") and entry["version_tuple"] > local_tuple
    ]


def _format_release_date(published_at):
    if not isinstance(published_at, str):
        return "unknown-date"
    text = published_at.strip()
    if len(text) >= 10:
        return text[:10]
    return text or "unknown-date"


def _normalize_changelog_line(text):
    line = str(text).strip()
    if not line:
        return ""
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
    if line.startswith(("- ", "* ", "+ ")):
        return "- " + line[2:].strip()
    if re.match(r"^\d+\.\s+", line):
        return line
    return "- " + line


def _release_changelog_lines(body, max_lines=GITHUB_MAX_CHANGELOG_LINES):
    lines = []
    truncated = False
    for raw_line in str(body).splitlines():
        normalized = _normalize_changelog_line(raw_line)
        if not normalized:
            continue
        lines.append(normalized)
        if len(lines) >= max(1, int(max_lines)):
            truncated = True
            break
    if not lines:
        return ["- No changelog details provided."]
    if truncated:
        lines.append("- ...")
    return lines


def _format_update_changelog(releases):
    if not releases:
        return "No newer release notes available."
    shown = releases[: max(1, int(GITHUB_MAX_CHANGELOG_RELEASES))]
    output = []
    for release in shown:
        header = f"{release['tag_name']} ({_format_release_date(release.get('published_at'))})"
        release_name = release.get("name", "")
        if release_name and release_name != release["tag_name"]:
            header += f" - {release_name}"
        output.append(f"* {header}")
        if release.get("html_url"):
            output.append(f"  URL: {release['html_url']}")
        output.append("  Changes:")
        for line in _release_changelog_lines(release.get("body", "")):
            output.append(f"    {line}")
    remaining = len(releases) - len(shown)
    if remaining > 0:
        output.append(f"* ... and {remaining} more newer release(s).")
    return "\n".join(output)


def _prompt_continue_with_update(local_version, newer_releases):
    latest = newer_releases[0]
    print("A newer HyperX Alpha release is available on GitHub than this local source.")
    print(f"Current source version: v{local_version}")
    print(f"Latest available release: {latest['tag_name']}")
    print("")
    print("Changelog for newer releases:")
    print(_format_update_changelog(newer_releases))
    if not sys.stdin.isatty():
        print("Non-interactive mode detected; continuing with current source version.")
        return True
    reply = input("Continue installation with the current source anyway? [y/N] ").strip()
    return reply.lower().startswith("y")


def _check_for_github_updates_before_install():
    local_version = _read_local_version()
    local_semver = _parse_semver(local_version)
    if local_semver is None:
        print(
            f"Update check skipped: local version '{local_version}' "
            "is not a semantic version."
        )
        return True

    repo = _resolve_github_repo()
    payload, error = _fetch_github_releases(repo)
    if error is not None:
        print(f"Update check skipped: {error}.")
        return True

    releases = _collect_stable_semver_releases(payload)
    if not releases:
        print("Update check: no stable semantic GitHub releases found.")
        return True

    newer = _newer_releases(local_version, releases)
    if not newer:
        return True
    return _prompt_continue_with_update(local_version, newer)


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


def _install_runtime_files():
    source_package_dir = SOURCE_PACKAGE_DIR
    if not source_package_dir.is_dir():
        print(f"Source package directory not found: {source_package_dir}")
        return False

    discovered_modules = set(_source_python_modules(source_package_dir))
    whitelist_modules = set(RUNTIME_MODULE_FILES)
    missing_from_whitelist = sorted(discovered_modules - whitelist_modules)
    if missing_from_whitelist:
        print(
            "Runtime module whitelist is outdated. "
            "Please add these modules to RUNTIME_MODULE_FILES:"
        )
        for module_name in missing_from_whitelist:
            print(f"  - {module_name}")
        return False

    staging_root = None
    staging_package_dir = None
    backup_dir = RUNTIME_ROOT / "hyperxalpha-backup"
    moved_existing = False

    try:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix="hyperxalpha-staging-", dir=RUNTIME_ROOT))
        staging_package_dir = staging_root / RUNTIME_PACKAGE_DIR.name
        staging_package_dir.mkdir(parents=True, exist_ok=True)

        for module_name in RUNTIME_MODULE_FILES:
            src_file = source_package_dir / module_name
            if not src_file.is_file():
                print(f"Missing runtime module file: {src_file}")
                return False
            shutil.copy2(src_file, staging_package_dir / module_name)

        for resource_dir in RUNTIME_RESOURCE_DIRS:
            src_dir = source_package_dir / resource_dir
            if not src_dir.is_dir():
                print(f"Missing runtime resource directory: {src_dir}")
                return False
            shutil.copytree(src_dir, staging_package_dir / resource_dir)

        if backup_dir.exists():
            shutil.rmtree(backup_dir)

        if RUNTIME_PACKAGE_DIR.exists():
            os.replace(RUNTIME_PACKAGE_DIR, backup_dir)
            moved_existing = True

        try:
            os.replace(staging_package_dir, RUNTIME_PACKAGE_DIR)
        except OSError:
            if moved_existing and backup_dir.exists() and not RUNTIME_PACKAGE_DIR.exists():
                os.replace(backup_dir, RUNTIME_PACKAGE_DIR)
            raise

        if backup_dir.exists():
            try:
                shutil.rmtree(backup_dir)
            except OSError as exc:
                print(f"Warning: installed runtime but could not remove backup {backup_dir}: {exc}")
    except OSError as exc:
        print(f"Failed to install runtime files in {RUNTIME_ROOT}: {exc}")
        return False
    finally:
        if staging_package_dir is not None and staging_package_dir.exists():
            try:
                shutil.rmtree(staging_package_dir)
            except OSError:
                pass
        if staging_root is not None and staging_root.exists():
            try:
                staging_root.rmdir()
            except OSError:
                pass

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
        SOURCE_PACKAGE_DIR / "assets" / "img" / "hyperx.png",
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


def _prompt_install_scope(default_scope="user"):
    if default_scope not in {"system", "user"}:
        default_scope = "user"
    if not sys.stdin.isatty():
        print(
            "Non-interactive mode detected; "
            f"defaulting desktop entry scope to '{default_scope}'."
        )
        return default_scope
    if default_scope == "system":
        prompt = "Install desktop entry for all users? [Y/n] "
        default_reply = "Y"
    else:
        prompt = "Install desktop entry for all users? [y/N] "
        default_reply = "N"
    reply = input(prompt).strip() or default_reply
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


def _install_user_home(scope, desktop_entry_path):
    if scope != "user":
        return None
    if desktop_entry_path:
        try:
            resolved = Path(desktop_entry_path).resolve(strict=False)
            if resolved.name == "hyperxalpha.desktop":
                applications = resolved.parent
                if applications.name == "applications":
                    share = applications.parent
                    if share.name == "share":
                        local = share.parent
                        if local.name == ".local":
                            home = local.parent
                            if home.is_absolute():
                                return str(home)
        except (OSError, RuntimeError):
            pass
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return pwd.getpwnam(sudo_user).pw_dir
        except KeyError:
            pass
    return str(Path.home())


def _check_hidraw_lib():
    for name in ("libhidapi-hidraw.so.0", "libhidapi-hidraw.so"):
        try:
            ctypes.CDLL(name)
            return True
        except OSError:
            continue
    return False


def _read_cmdline_tokens(pid):
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return []
    if not raw:
        return []
    return [
        token for token in raw.decode("utf-8", errors="ignore").split("\x00") if token
    ]


def _is_python_command(token):
    return Path(token).name.lower().startswith("python")


def _candidate_launcher_tokens():
    tokens = {str(LAUNCHER_PATH)}
    found = shutil.which("hyperxalpha")
    if found:
        tokens.add(found)
    return tokens


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


def _stop_running_app():
    if os.geteuid() != 0:
        return False, False

    launcher_tokens = _candidate_launcher_tokens()
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


def _write_install_receipt(data):
    temp_path = None
    receipt_path = Path(RECEIPT_PATH)
    try:
        Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=STATE_DIR,
            prefix="install-receipt-",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, receipt_path)
        print(f"Install receipt written to {receipt_path}.")
        return True
    except OSError as exc:
        print(f"Failed to write install receipt: {exc}")
        return False
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def install_all(scope=None):
    if os.geteuid() != 0:
        print("Please run the installer with sudo.")
        return False

    if not _check_for_github_updates_before_install():
        print("Installation cancelled by user.")
        return False

    ok = True
    package_manager = None
    packages_requested = []
    install_scope = scope or _prompt_install_scope(default_scope="user")

    had_running, stopped = _stop_running_app()
    if had_running and stopped:
        print("Stopped running HyperX Alpha instance.")
    elif had_running and not stopped:
        print("Cannot continue install while HyperX Alpha is still running.")
        return False

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
        package_manager = "manual"
        print("Package install skipped (unsupported distro).")
        print("Please install the dependencies listed in README.md.")

    if not _install_udev_rule():
        ok = False

    if not _install_runtime_files():
        ok = False

    if not _install_launcher():
        ok = False

    desktop_entry_path = _install_desktop_entry_with_scope(install_scope)
    if desktop_entry_path is None:
        ok = False

    qt_ok, qt_reason = _check_qt()
    if qt_ok:
        print("PySide6 is available.")
    else:
        print(f"PySide6 not available: {qt_reason}")
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
        "install_user_home": _install_user_home(install_scope, desktop_entry_path),
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
        help=(
            "Check runtime prerequisites (Qt, hidraw, udev rule) and "
            "report launcher presence."
        ),
    )
    parser.add_argument(
        "--scope",
        choices=("user", "system"),
        help="Desktop entry install scope (used by full install mode).",
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
            ok = False
        if LAUNCHER_PATH.is_file():
            print(f"Launcher present: {LAUNCHER_PATH}")
        else:
            print(f"Launcher missing: {LAUNCHER_PATH}")
        return 0 if ok else 1

    return 0 if install_all(scope=args.scope) else 1


if __name__ == "__main__":
    raise SystemExit(main())
