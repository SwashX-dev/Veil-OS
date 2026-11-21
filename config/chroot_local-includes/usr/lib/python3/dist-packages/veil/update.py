"""Veil OS release check (notify-only).

Tails' automatic upgrader downloads Tails incremental upgrades signed by the
Tails project; those cannot be applied to a derivative, so Veil disables it
and instead checks the Veil GitHub releases (through Tor, as the live user)
and tells the person to install a new, verified image.

Tails' security advisories check (tails-security-check) stays enabled: Veil
is based on a specific Tails release and those advisories apply to it.
"""

from __future__ import annotations

import json
import re
import urllib.request

from veil import __version__, common

REPO = "SwashX-dev/Veil-OS"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=10"


def _parse(v: str) -> tuple:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)(?:-?(alpha|beta|rc)\.?(\d+))?", v)
    if not m:
        return (0,)
    major, minor, patch, pre, n = m.groups()
    rank = {"alpha": 0, "beta": 1, "rc": 2, None: 3}[pre]
    return (int(major), int(minor), int(patch), rank, int(n or 0))


def current() -> str:
    return common.read_os_release().get("VEIL_VERSION", __version__)


def latest(timeout: float = 60) -> dict | None:
    if not common.tor_bootstrapped():
        return None
    req = urllib.request.Request(API, headers={"User-Agent": "veil-update-check",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URL
        rels = json.loads(r.read())
    rels = [r for r in rels if not r.get("draft")]
    if not rels:
        return None
    best = max(rels, key=lambda r: _parse(r.get("tag_name", "")))
    return {"tag": best["tag_name"], "url": best["html_url"], "prerelease": best.get("prerelease", False)}


def describe() -> str:
    try:
        rel = latest()
    except OSError as e:
        return f"update check failed: {e}"
    if rel is None:
        return "update check skipped: Tor is not connected (or no releases yet)"
    if _parse(rel["tag"]) > _parse(current()):
        return (f"Veil OS {rel['tag']} is available ({rel['url']}). Download it, verify the signature "
                "and SHA256SUMS, and install it to your USB stick.")
    return f"Veil OS {current()} is up to date (latest release: {rel['tag']})."
