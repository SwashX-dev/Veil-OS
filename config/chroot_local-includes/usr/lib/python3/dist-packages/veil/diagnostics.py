"""`veil diagnostics`: a crash report for display and stability problems.

The report contains the Veil version, the boot options, the graphics
hardware and driver, the session's rendering mode, Veil's privacy settings,
and redacted log lines about graphics, the compositor, crashes, memory and
USB disconnects (from veil-diag-helper). It contains no browsing data, no
network addresses and no Tor information.
"""

from __future__ import annotations

import datetime as _dt
import os
import platform
import subprocess
from pathlib import Path

from veil import __version__
from veil import privacy_settings

HELPER = "/usr/local/lib/veil/veil-diag-helper"
DRM = Path("/sys/class/drm")


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return "?"


def gpu_inventory(drm: Path = DRM) -> list[dict]:
    cards = []
    for card in sorted(drm.glob("card[0-9]*")):
        if "-" in card.name:
            continue
        dev = card / "device"
        driver = dev / "driver"
        connectors = []
        for conn in sorted(drm.glob(f"{card.name}-*")):
            connectors.append(f"{conn.name.split('-', 1)[1]}={_read(conn / 'status')}")
        cards.append({
            "card": card.name,
            "vendor": _read(dev / "vendor"),
            "device": _read(dev / "device"),
            "driver": driver.resolve().name if driver.exists() else "none",
            "boot_vga": _read(dev / "boot_vga"),
            "connectors": connectors,
        })
    return cards


def graphics_mode(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    if env.get("VEIL_SAFE_GRAPHICS"):
        return f"software rendering (safe graphics: {env['VEIL_SAFE_GRAPHICS']})"
    if env.get("WLR_RENDERER") == "pixman":
        return "software rendering (Pixman)"
    return "GPU rendering"


def build_report() -> str:
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"Veil OS diagnostics report ({now})", "",
             f"Veil version: {__version__}",
             f"Kernel: {platform.release()}",
             f"Boot options: {_read(Path('/proc/cmdline'))}",
             f"Desktop rendering: {graphics_mode()}",
             f"Compositor restarts this session: {os.environ.get('VEIL_COMPOSITOR_RESTARTS', '0')}", ""]
    lines.append("== Graphics devices ==")
    for card in gpu_inventory():
        lines.append(f"{card['card']}: vendor {card['vendor']} device {card['device']} driver {card['driver']} "
                     f"boot_vga {card['boot_vga']} connectors {', '.join(card['connectors']) or '-'}")
    lines += ["", "== Privacy settings =="]
    for key, value in privacy_settings.load().items():
        lines.append(f"{key}: {value}")
    lines.append("")
    try:
        r = subprocess.run(["sudo", "-n", HELPER, "report"], capture_output=True, text=True, timeout=120, check=False)
        lines.append(r.stdout.rstrip() if r.returncode == 0 else f"(log helper failed: {r.stderr.strip()})")
    except (OSError, subprocess.TimeoutExpired) as exc:
        lines.append(f"(log helper unavailable: {exc})")
    return "\n".join(lines) + "\n"


def default_destination(now: _dt.datetime | None = None) -> Path:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    name = f"veil-diagnostics-{now.strftime('%Y%m%dT%H%M%SZ')}.txt"
    persistent = Path.home() / "Persistent"
    if persistent.is_dir() and os.access(persistent, os.W_OK) and persistent.resolve() != Path.home().resolve():
        return persistent / name
    return Path.home() / name


def save(report: str, dest: Path | None = None) -> Path:
    dest = dest or default_destination()
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(report)
    return dest
