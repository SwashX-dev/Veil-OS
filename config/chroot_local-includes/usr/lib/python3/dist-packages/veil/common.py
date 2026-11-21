"""Shared helpers for Veil command-line and graphical tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

PERSISTENCE_MOUNT = Path("/live/persistence/TailsData_unlocked")
PERSISTENT_DIR = Path.home() / "Persistent"
TOR_BOOTSTRAPPED_FLAG = Path("/run/tor-has-bootstrapped/done")


# --------------------------------------------------------------------------
# Terminal output
# --------------------------------------------------------------------------

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def violet(text: str) -> str:
    return _c("38;5;141", text)


def cyan(text: str) -> str:
    return _c("38;5;80", text)


def dim(text: str) -> str:
    return _c("2", text)


def red(text: str) -> str:
    return _c("38;5;203", text)


def yellow(text: str) -> str:
    return _c("38;5;221", text)


def green(text: str) -> str:
    return _c("38;5;114", text)


def bold(text: str) -> str:
    return _c("1", text)


STATUS_STYLE = {
    "pass": (green, "PASS"),
    "warn": (yellow, "WARN"),
    "fail": (red, "FAIL"),
    "info": (cyan, "INFO"),
    "skip": (dim, "SKIP"),
}


def fmt_status(status: str) -> str:
    style, label = STATUS_STYLE.get(status, (dim, status.upper()))
    return style(f"[{label}]")


def die(msg: str, code: int = 1) -> None:
    print(red(f"veil: {msg}"), file=sys.stderr)
    raise SystemExit(code)


# --------------------------------------------------------------------------
# Process helpers
# --------------------------------------------------------------------------


@dataclass
class CmdResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(argv: list[str], timeout: float = 30, input: str | None = None,
        env: dict | None = None) -> CmdResult:
    """Run a command without a shell and capture its output.

    Never raises for a missing binary or a timeout: those are reported as
    failed results so callers can degrade gracefully.
    """
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           input=input, env=env, check=False)
        return CmdResult(argv, p.returncode, p.stdout, p.stderr)
    except FileNotFoundError:
        return CmdResult(argv, 127, "", f"{argv[0]}: not found")
    except subprocess.TimeoutExpired:
        return CmdResult(argv, 124, "", f"{argv[0]}: timed out after {timeout}s")


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


# --------------------------------------------------------------------------
# System facts
# --------------------------------------------------------------------------


def read_os_release(path: str = "/etc/os-release") -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k] = v.strip().strip('"')
    except OSError:
        pass
    return data


def is_veil() -> bool:
    return read_os_release().get("ID") == "veil"


def tor_bootstrapped() -> bool:
    """True when Tails reports that Tor has finished bootstrapping."""
    if TOR_BOOTSTRAPPED_FLAG.exists():
        return True
    r = run(["systemctl", "is-active", "--quiet", "tails-tor-has-bootstrapped.target"], timeout=5)
    return r.ok


def persistence_unlocked() -> bool:
    return PERSISTENCE_MOUNT.is_dir() and os.path.ismount(PERSISTENCE_MOUNT)


def amnesic_state_dir(app: str) -> Path:
    """Per-session state that lives in RAM and vanishes at shutdown."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not os.path.isdir(base):
        base = "/tmp"  # noqa: S108 - /tmp is RAM-backed on Tails/Veil
    p = Path(base) / "veil" / app
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def persistent_state_dir(app: str) -> Path | None:
    """State directory inside the encrypted Persistent Storage, if unlocked."""
    if not persistence_unlocked():
        return None
    p = PERSISTENT_DIR / ".veil" / app
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def mem_info() -> dict[str, int]:
    """Return /proc/meminfo values in bytes."""
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, rest = line.split(":", 1)
                parts = rest.split()
                if parts:
                    val = int(parts[0])
                    if len(parts) > 1 and parts[1] == "kB":
                        val *= 1024
                    out[key] = val
    except OSError:
        pass
    return out


def human_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"


@dataclass
class Report:
    """Generic machine-readable report container."""

    title: str
    items: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=False)
