"""Nix-based package management for Veil OS (`veil install/remove/search/update`).

Privacy model:
* Nix runs single-user as the live user (amnesia). Its downloads are plain
  TCP connections from that user, which the unchanged Tails firewall
  transparently routes through Tor. Nix never runs as root here, so an
  installed package can neither change the firewall nor add system services
  or setuid binaries.
* Only binaries signed by the official cache.nixos.org key are accepted
  (require-sigs = true, see /etc/nix/nix.conf).
* Debian/APT and the Tails "Additional Software" feature are untouched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from veil import common, tools_catalog

FLAKE = os.environ.get("VEIL_NIX_FLAKE", "/usr/share/veil/nix")
NIX_ENV = {**os.environ, "NIX_REMOTE": "", "NIX_CONFIG": "experimental-features = nix-command flakes"}


def _nix(args: list[str], timeout: float = 3600) -> common.CmdResult:
    return common.run(["nix", *args], timeout=timeout, env=NIX_ENV)


def bundles() -> dict[str, dict]:
    try:
        return json.loads((Path(FLAKE) / "bundles.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def preflight(installing: bool = True) -> list[str]:
    """Return warnings to show before touching the network."""
    warns = []
    if not common.have("nix"):
        warns.append("ERROR: nix is not installed")
    if not common.tor_bootstrapped():
        warns.append("ERROR: Tor is not connected; downloads would fail (connect with Tor Connection first)")
    if installing and not os.path.ismount("/nix") and not common.persistence_unlocked():
        warns.append("Packages are installed to RAM and will be gone after shutdown. "
                     "Turn on 'Nix Packages' in Persistent Storage to keep them.")
    elif installing and not os.path.ismount("/nix"):
        warns.append("Nix packages are not persistent: turn on 'Nix Packages' in Persistent Storage to keep them.")
    return warns


def _ref(name: str) -> str:
    """Map a user-facing name to a flake reference."""
    if name.startswith(("nixpkgs#", "path:", "github:", "/")):
        return name
    b = name.removeprefix("bundle:")
    if b in bundles():
        return f"{FLAKE}#bundle-{b}"
    return f"nixpkgs#{name}"


def unfree_bundles(names: list[str]) -> list[dict]:
    """Bundles among `names` whose license the user must accept first."""
    out = []
    for n in names:
        b = bundles().get(n.removeprefix("bundle:"))
        if b and b.get("unfree"):
            out.append({"name": n.removeprefix("bundle:"), **b})
    return out


def install(names: list[str]) -> common.CmdResult:
    """Install packages/bundles. Callers must have obtained license consent
    for unfree bundles (see unfree_bundles); the flake only enables unfree
    packages for those bundles, never for arbitrary nixpkgs names."""
    refs = [_ref(n) for n in names]
    return _nix(["profile", "install", *refs])


def remove(names: list[str]) -> common.CmdResult:
    out = common.CmdResult([], 0, "", "")
    for n in names:
        b = n.removeprefix("bundle:")
        target = f"bundle-{b}" if b in bundles() else n
        r = _nix(["profile", "remove", target], timeout=600)
        out = common.CmdResult(r.argv, r.returncode or out.returncode, out.stdout + r.stdout, out.stderr + r.stderr)
    return out


def update() -> common.CmdResult:
    return _nix(["profile", "upgrade", "--all"])


def installed() -> list[dict]:
    r = _nix(["profile", "list", "--json"], timeout=60)
    if not r.ok:
        return []
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    elems = data.get("elements", {})
    if isinstance(elems, list):
        return [{"name": e.get("attrPath", "?"), "ref": e.get("originalUrl", "")} for e in elems]
    return [{"name": k, "ref": v.get("originalUrl", "")} for k, v in elems.items()]


def search_remote(term: str) -> dict:
    r = _nix(["search", "nixpkgs", term, "--json"], timeout=900)
    if not r.ok:
        return {}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


def describe_search(term: str, remote: bool = False) -> str:
    lines = []
    for name, b in bundles().items():
        if term.lower() in name.lower() or term.lower() in b.get("description", "").lower():
            lines.append(f"bundle:{name:<16} {b.get('description', '')}  [{', '.join(b.get('packages', []))}]")
    for t in tools_catalog.search(term):
        lines.append(f"{t['name']:<22} {t['category']:<18} source={t['source']} network={t['network_policy']}")
    if remote:
        for attr, info in list(search_remote(term).items())[:30]:
            lines.append(f"nixpkgs#{attr.split('.')[-1]:<20} {info.get('version', '')}  {info.get('description', '')[:70]}")
    return "\n".join(lines) or f"no local match for {term!r} (try `veil search --remote {term}`, uses Tor)"


def describe_installed() -> str:
    rows = installed()
    return "\n".join(f"{r['name']:<28} {r['ref']}" for r in rows) or "no Nix packages installed"
