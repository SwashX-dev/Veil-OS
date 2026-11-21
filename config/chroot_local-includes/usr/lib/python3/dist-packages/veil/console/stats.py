"""Local system statistics for Veil Console (no network access, /proc only)."""

from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path


class CpuSampler:
    def __init__(self):
        self.prev = self._read()

    @staticmethod
    def _read():
        try:
            parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
            vals = [int(x) for x in parts]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            return sum(vals), idle
        except (OSError, ValueError, IndexError):
            return 0, 0

    def sample(self) -> float:
        cur = self._read()
        dt, di = cur[0] - self.prev[0], cur[1] - self.prev[1]
        self.prev = cur
        return 0.0 if dt <= 0 else max(0.0, min(100.0, 100.0 * (dt - di) / dt))


def memory() -> tuple[int, int]:
    info = {}
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            k, v = ln.split(":", 1)
            info[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError):
        return 0, 0
    total = info.get("MemTotal", 0)
    return total - info.get("MemAvailable", 0), total


class NetSampler:
    """Aggregate rx/tx rate over physical + wireless interfaces (no addresses)."""

    def __init__(self):
        self.prev = self._read()
        self.t = time.monotonic()

    @staticmethod
    def _read() -> dict[str, tuple[int, int]]:
        out = {}
        try:
            for ln in Path("/proc/net/dev").read_text().splitlines()[2:]:
                name, rest = ln.split(":", 1)
                name = name.strip()
                if name == "lo" or name.startswith(("veth", "lab")):
                    continue
                f = rest.split()
                out[name] = (int(f[0]), int(f[8]))
        except (OSError, ValueError, IndexError):
            pass
        return out

    def sample(self) -> tuple[float, float]:
        cur, now = self._read(), time.monotonic()
        dt = max(now - self.t, 1e-3)
        rx = sum(max(0, cur[k][0] - self.prev.get(k, cur[k])[0]) for k in cur) / dt
        tx = sum(max(0, cur[k][1] - self.prev.get(k, cur[k])[1]) for k in cur) / dt
        self.prev, self.t = cur, now
        return rx, tx


def interfaces() -> list[dict]:
    rows = []
    base = Path("/sys/class/net")
    if not base.is_dir():
        return rows
    for d in sorted(base.iterdir()):
        if d.name == "lo":
            continue
        try:
            state = (d / "operstate").read_text().strip()
        except OSError:
            state = "?"
        kind = "wifi" if (d / "wireless").exists() else ("virtual" if not (d / "device").exists() else "wired")
        try:
            mac = (d / "address").read_text().strip()
        except OSError:
            mac = ""
        rows.append({"name": d.name, "state": state, "kind": kind, "mac": mac})
    return rows


def top_processes(n: int = 8) -> list[tuple[str, float, int]]:
    try:
        import psutil
    except ImportError:
        return []
    rows = []
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        i = p.info
        rows.append((i["name"] or "?", i["cpu_percent"] or 0.0, i["memory_info"].rss if i["memory_info"] else 0))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:n]


def uptime() -> str:
    try:
        s = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError):
        return "?"
    h, m = divmod(s // 60, 60)
    return f"{h}h {m:02d}m"


def load() -> str:
    try:
        return " ".join(Path("/proc/loadavg").read_text().split()[:3])
    except OSError:
        return "?"


class History:
    def __init__(self, n: int = 90):
        self.v = deque([0.0] * n, maxlen=n)

    def push(self, x: float) -> None:
        self.v.append(x)


def human_rate(b: float) -> str:
    for u in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if b < 1024:
            return f"{b:5.1f} {u}"
        b /= 1024
    return f"{b:.1f} TiB/s"


def cwd_entries(path: str, limit: int = 60) -> list[tuple[str, bool]]:
    try:
        with os.scandir(path) as it:
            items = [(e.name, e.is_dir(follow_symlinks=False)) for e in it if not e.name.startswith(".")]
    except OSError:
        return []
    items.sort(key=lambda t: (not t[1], t[0].lower()))
    return items[:limit]
