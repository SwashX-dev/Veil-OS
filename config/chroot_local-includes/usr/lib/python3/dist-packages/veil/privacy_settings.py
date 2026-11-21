"""Per-user privacy settings (Veil Privacy Center, `veil privacy settings`).

Settings live in ~/.config/veil/privacy.json. Like the rest of the home
folder they are in RAM unless the user enables the *Veil Desktop Settings*
Persistent Storage feature. A missing, unreadable or partly invalid file
never breaks the session: every invalid value falls back to its default.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Tor Browser's own security levels (browser.security_level.security_slider).
# Each level is shared by every Tor Browser user who picks it, so choosing one
# does not make Veil users stand out the way custom preferences would.
SECURITY_LEVELS = {"standard": 4, "safer": 2, "safest": 1}
UPSCALING_MODES = ("off", "quality", "enhanced")
AUTO_LOCK_CHOICES = (0, 1, 2, 5, 10, 15, 30, 60)
CLIPBOARD_CHOICES = (0, 15, 30, 60, 120, 300)

DEFAULTS: dict = {
    # Tails' default.
    "tor_browser_security_level": "standard",
    # Locks only if a screen-lock password exists (never locks the user out).
    "auto_lock_minutes": 10,
    # 0 = never clear the clipboard automatically.
    "clipboard_clear_seconds": 0,
    # Ctrl+Alt+Shift+Delete: immediate shutdown with memory erasure.
    "emergency_shutdown_hotkey": True,
    # Re-applied at every login (blocking needs no password, unblocking does).
    "camera_blocked": False,
    "microphone_blocked": False,
    "video_upscaling": "off",
}

DESCRIPTIONS = {
    "tor_browser_security_level": "Tor Browser security level at startup: standard, safer or safest",
    "auto_lock_minutes": "Lock the screen after this many idle minutes (0 = never)",
    "clipboard_clear_seconds": "Clear the clipboard this many seconds after a copy (0 = never)",
    "emergency_shutdown_hotkey": "Ctrl+Alt+Shift+Delete shuts down immediately and erases memory",
    "camera_blocked": "Block every camera for new applications",
    "microphone_blocked": "Block every microphone (audio capture) for new applications",
    "video_upscaling": "Video upscaling in Veil Player and Celluloid: off, quality or enhanced",
}


def settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "veil" / "privacy.json"


def _coerce(key: str, value):
    """Return a valid value for key, or raise ValueError."""
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting {key!r}")
    if key == "tor_browser_security_level":
        if value not in SECURITY_LEVELS:
            raise ValueError(f"{key} must be one of {', '.join(SECURITY_LEVELS)}")
        return value
    if key == "video_upscaling":
        if value not in UPSCALING_MODES:
            raise ValueError(f"{key} must be one of {', '.join(UPSCALING_MODES)}")
        return value
    if key in ("emergency_shutdown_hotkey", "camera_blocked", "microphone_blocked"):
        if isinstance(value, bool):
            return value
        raise ValueError(f"{key} must be true or false")
    if key == "auto_lock_minutes":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 240:
            raise ValueError(f"{key} must be a whole number of minutes from 0 to 240")
        return value
    if key == "clipboard_clear_seconds":
        if isinstance(value, bool) or not isinstance(value, int) or not (value == 0 or 5 <= value <= 3600):
            raise ValueError(f"{key} must be 0 or a number of seconds from 5 to 3600")
        return value
    raise ValueError(f"unknown setting {key!r}")  # pragma: no cover


def parse_cli_value(key: str, raw: str):
    """Convert a command-line string to the setting's type and validate it."""
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting {key!r}; known: {', '.join(DEFAULTS)}")
    default = DEFAULTS[key]
    if isinstance(default, bool):
        lowered = raw.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"{key} must be on or off")
    if isinstance(default, int):
        try:
            return _coerce(key, int(raw))
        except ValueError as exc:
            if "must" in str(exc):
                raise
            raise ValueError(f"{key} must be a whole number") from None
    return _coerce(key, raw.strip().lower())


def load(path: Path | None = None) -> dict:
    path = path or settings_path()
    result = dict(DEFAULTS)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return result
    if not isinstance(data, dict):
        return result
    for key in DEFAULTS:
        if key in data:
            try:
                result[key] = _coerce(key, data[key])
            except ValueError:
                pass
    return result


def save(settings: dict, path: Path | None = None) -> None:
    path = path or settings_path()
    clean = {key: _coerce(key, settings.get(key, DEFAULTS[key])) for key in DEFAULTS}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".privacy.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(clean, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def update(key: str, value, path: Path | None = None) -> dict:
    settings = load(path)
    settings[key] = _coerce(key, value)
    save(settings, path)
    return settings


def tor_browser_user_js(level: str) -> str:
    """user.js lines that start Tor Browser at one of its own security levels."""
    slider = SECURITY_LEVELS[_coerce("tor_browser_security_level", level)]
    return ("// Written by Veil OS (Privacy Center: Tor Browser security level).\n"
            f'user_pref("browser.security_level.security_slider", {slider});\n'
            'user_pref("browser.security_level.security_custom", false);\n')


def reload_daemon() -> None:
    """Ask the running privacy daemon to apply new settings (no-op if absent)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    try:
        pid = int(Path(runtime, "veil-privacy-daemon.pid").read_text().strip())
        # Only signal our own daemon, never a recycled PID owned by someone else.
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        if b"veil-privacy-daemon" in cmdline and os.stat(f"/proc/{pid}").st_uid == os.getuid():
            os.kill(pid, 1)  # SIGHUP
    except (OSError, ValueError):
        pass
