import json
import os
import shutil
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


@dataclass
class AppSettings:
    start_in_tray: bool = False
    mic_monitor_state: Optional[bool] = None
    low_battery_notifications: bool = True
    theme_mode: str = "system"


def load_settings():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return AppSettings()
    except (OSError, json.JSONDecodeError):
        return AppSettings()

    return AppSettings(
        start_in_tray=bool(data.get("start_in_tray", False)),
        mic_monitor_state=data.get("mic_monitor_state"),
        low_battery_notifications=bool(data.get("low_battery_notifications", True)),
        theme_mode=str(data.get("theme_mode", "system")),
    )


def save_settings(settings: AppSettings):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"start_in_tray": settings.start_in_tray}
        if settings.mic_monitor_state is not None:
            payload["mic_monitor_state"] = bool(settings.mic_monitor_state)
        payload["low_battery_notifications"] = bool(
            settings.low_battery_notifications
        )
        payload["theme_mode"] = settings.theme_mode
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return True
    except OSError:
        return False


def autostart_enabled():
    return AUTOSTART_PATH.exists()


def _escape_desktop_value(value):
    return value.replace("\\", "\\\\").replace(" ", "\\ ")


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


def _autostart_desktop_entry():
    icon_value = _escape_desktop_value(str(_resolve_icon_path()))
    exec_value = _resolve_exec_command(start_hidden=True)
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


def set_autostart(enabled: bool):
    if enabled:
        try:
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            AUTOSTART_PATH.write_text(_autostart_desktop_entry(), encoding="utf-8")
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
