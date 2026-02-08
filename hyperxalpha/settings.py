import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / (
    "hyperxalpha"
)
CONFIG_PATH = CONFIG_DIR / "settings.json"
AUTOSTART_DIR = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"
)
AUTOSTART_PATH = AUTOSTART_DIR / "hyperxalpha.desktop"
SYSTEM_LAUNCHER_PATH = Path("/usr/local/bin/hyperxalpha")
USER_LAUNCHER_PATH = Path.home() / ".local" / "bin" / "hyperxalpha"
SYSTEM_ICON_PATH = Path("/opt/hyperxalpha/hyperxalpha/assets/img/hyperx.png")
LOCAL_ICON_PATH = Path(__file__).resolve().parent / "assets" / "img" / "hyperx.png"
SOURCE_ROOT = Path(__file__).resolve().parent.parent
VALID_THEME_MODES = {"system", "light", "dark"}


@dataclass
class AppSettings:
    start_on_login: bool = False
    start_hidden: bool = False
    mic_monitor_state: Optional[bool] = None
    selected_device_key: Optional[str] = None
    tray_notifications: bool = True
    theme_mode: str = "system"


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _normalize_device_key(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return str(value)


def _normalize_theme_mode(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in VALID_THEME_MODES:
            return normalized
    return "system"


def load_settings():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return AppSettings()
    except (OSError, json.JSONDecodeError):
        return AppSettings()

    start_on_login = _parse_bool(data.get("start_on_login"))
    start_hidden = _parse_bool(data.get("start_hidden"))
    mic_monitor_state = _parse_bool(data.get("mic_monitor_state"))
    tray_raw = (
        data.get("tray_notifications")
        if "tray_notifications" in data
        else data.get("low_battery_notifications")
    )
    tray_notifications = _parse_bool(tray_raw)

    return AppSettings(
        start_on_login=False if start_on_login is None else start_on_login,
        start_hidden=False if start_hidden is None else start_hidden,
        mic_monitor_state=mic_monitor_state,
        selected_device_key=_normalize_device_key(data.get("selected_device_key")),
        tray_notifications=True if tray_notifications is None else tray_notifications,
        theme_mode=_normalize_theme_mode(data.get("theme_mode")),
    )


def save_settings(settings: AppSettings):
    temp_path = None
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "start_on_login": bool(settings.start_on_login),
            "start_hidden": bool(settings.start_hidden),
        }
        mic_monitor_state = _parse_bool(settings.mic_monitor_state)
        if mic_monitor_state is not None:
            payload["mic_monitor_state"] = mic_monitor_state
        selected_device_key = _normalize_device_key(settings.selected_device_key)
        if selected_device_key:
            payload["selected_device_key"] = selected_device_key
        payload["tray_notifications"] = bool(settings.tray_notifications)
        payload["theme_mode"] = _normalize_theme_mode(settings.theme_mode)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CONFIG_DIR,
            prefix="settings-",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, CONFIG_PATH)
        return True
    except OSError:
        return False
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def autostart_enabled():
    return AUTOSTART_PATH.exists()


def _escape_desktop_value(value):
    return value.replace("\\", "\\\\").replace(" ", "\\ ")


def _user_launcher_script_content():
    return (
        "#!/usr/bin/env python3\n"
        "import runpy\n"
        "import sys\n"
        f"sys.path.insert(0, {str(SOURCE_ROOT)!r})\n"
        "runpy.run_module('hyperxalpha', run_name='__main__')\n"
    )


def _ensure_user_launcher():
    try:
        USER_LAUNCHER_PATH.parent.mkdir(parents=True, exist_ok=True)
        expected = _user_launcher_script_content()
        needs_write = True
        if USER_LAUNCHER_PATH.is_file():
            current = USER_LAUNCHER_PATH.read_text(encoding="utf-8")
            needs_write = current != expected
        if needs_write:
            USER_LAUNCHER_PATH.write_text(expected, encoding="utf-8")
        USER_LAUNCHER_PATH.chmod(0o755)
        return USER_LAUNCHER_PATH
    except OSError:
        return None


def _resolve_exec_command(start_hidden=False):
    launcher = None
    for candidate in (SYSTEM_LAUNCHER_PATH, USER_LAUNCHER_PATH):
        if candidate.is_file():
            launcher = candidate
            break
    if launcher is None:
        found = shutil.which("hyperxalpha")
        if found:
            launcher = Path(found)
    if launcher is None:
        launcher = _ensure_user_launcher()

    if launcher is not None:
        command = _escape_desktop_value(str(launcher))
    else:
        command = "python3 -m hyperxalpha"

    if start_hidden:
        command += " --start-hidden"
    return command


def _resolve_icon_path():
    for candidate in (SYSTEM_ICON_PATH, LOCAL_ICON_PATH):
        if candidate.is_file():
            return candidate
    return LOCAL_ICON_PATH


def _autostart_desktop_entry(start_hidden=False):
    icon_value = _escape_desktop_value(str(_resolve_icon_path()))
    exec_value = _resolve_exec_command(start_hidden=bool(start_hidden))
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=HyperX Alpha\n"
        "Comment=HyperX Cloud Alpha Wireless control\n"
        f"Exec={exec_value}\n"
        f"Icon={icon_value}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def set_autostart(enabled: bool, start_hidden=False):
    if enabled:
        try:
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            AUTOSTART_PATH.write_text(
                _autostart_desktop_entry(start_hidden=start_hidden),
                encoding="utf-8",
            )
            return True
        except OSError:
            return False

    try:
        AUTOSTART_PATH.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False
