"""VPN before Tor: WireGuard configurations (for example from Proton VPN).

Only Tor's own connections to the Tor network use the VPN:

    applications -> Tor (unchanged) -> WireGuard (VPN) -> Internet

The VPN provider sees your real IP address and that you use Tor, but not
what you do over Tor. Your local network sees only encrypted WireGuard
traffic to the VPN server. Nothing else is routed through the VPN, and while
the VPN is enabled Tor cannot connect at all except through it (fail-closed).

Parsing is deliberately strict: only the keys below are accepted, endpoints
must be IPv4 addresses (resolving a host name would need DNS before Tor is
up), and wg-quick hooks such as PostUp are rejected, never executed.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

HELPER = "/usr/local/lib/veil/veil-vpn-helper"
INTERFACE = "wg-veil"
FWMARK = 0x5E11
TABLE = 51820
RULE_PRIORITY = 5300
MAX_CONFIG_BYTES = 16384

_INTERFACE_KEYS = {"privatekey", "address", "dns", "mtu", "listenport"}
_PEER_KEYS = {"publickey", "presharedkey", "allowedips", "endpoint", "persistentkeepalive"}
_FORBIDDEN = {"preup", "postup", "predown", "postdown", "table", "saveconfig", "fwmark"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class WireGuardConfig:
    private_key: str
    address: str            # IPv4 interface address, with prefix (e.g. 10.2.0.2/32)
    public_key: str
    endpoint_ip: str
    endpoint_port: int
    preshared_key: str | None = None
    mtu: int = 1420
    keepalive: int = 25
    ignored: tuple[str, ...] = ()

    def setconf(self, keepalive: bool = True) -> str:
        """Text for `wg setconf` (secrets: pass on stdin, never on argv)."""
        lines = ["[Interface]", f"PrivateKey = {self.private_key}", f"FwMark = {hex(FWMARK)}", "",
                 "[Peer]", f"PublicKey = {self.public_key}"]
        if self.preshared_key:
            lines.append(f"PresharedKey = {self.preshared_key}")
        lines += ["AllowedIPs = 0.0.0.0/0", f"Endpoint = {self.endpoint_ip}:{self.endpoint_port}"]
        if keepalive:
            lines.append(f"PersistentKeepalive = {self.keepalive}")
        return "\n".join(lines + [""])

    def summary(self) -> dict:
        return {"endpoint": f"{self.endpoint_ip}:{self.endpoint_port}", "address": self.address,
                "mtu": self.mtu, "ignored": list(self.ignored)}


def _key(value: str, what: str) -> str:
    value = value.strip()
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ConfigError(f"{what} is not valid base64") from None
    if len(raw) != 32:
        raise ConfigError(f"{what} must be a 32-byte WireGuard key")
    return value


def parse_config(text: str) -> WireGuardConfig:
    if len(text.encode()) > MAX_CONFIG_BYTES:
        raise ConfigError("configuration file is too large")
    section = None
    seen_peer = 0
    iface: dict[str, str] = {}
    peer: dict[str, str] = {}
    ignored: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section == "peer":
                seen_peer += 1
                if seen_peer > 1:
                    raise ConfigError("only one [Peer] is supported")
            elif section != "interface":
                raise ConfigError(f"line {lineno}: unknown section [{line[1:-1]}]")
            continue
        if "=" not in line:
            raise ConfigError(f"line {lineno}: expected 'Key = Value'")
        key, value = (part.strip() for part in line.split("=", 1))
        lkey = key.lower()
        if lkey in _FORBIDDEN:
            raise ConfigError(f"line {lineno}: {key} is not allowed (Veil never runs configuration hooks)")
        if section == "interface" and lkey in _INTERFACE_KEYS:
            target = iface
        elif section == "peer" and lkey in _PEER_KEYS:
            target = peer
        else:
            raise ConfigError(f"line {lineno}: unexpected key {key!r}")
        if lkey in target:
            if lkey in ("address", "allowedips", "dns"):
                target[lkey] += "," + value
                continue
            raise ConfigError(f"line {lineno}: duplicate {key}")
        target[lkey] = value

    if "privatekey" not in iface:
        raise ConfigError("[Interface] PrivateKey is missing")
    if not seen_peer or "publickey" not in peer:
        raise ConfigError("[Peer] PublicKey is missing")
    if "endpoint" not in peer:
        raise ConfigError("[Peer] Endpoint is missing")

    address = None
    for item in iface.get("address", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            net = ipaddress.ip_interface(item)
        except ValueError:
            raise ConfigError(f"invalid Address {item!r}") from None
        if net.version == 4 and address is None:
            address = str(net)
        elif net.version == 6:
            ignored.append(f"IPv6 address {item} (IPv6 is disabled)")
    if address is None:
        raise ConfigError("[Interface] needs an IPv4 Address")
    if "dns" in iface:
        ignored.append("DNS (names are resolved through Tor)")
    if "listenport" in iface:
        ignored.append("ListenPort")

    endpoint = peer["endpoint"]
    match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})", endpoint)
    if not match:
        raise ConfigError("Endpoint must be an IPv4 address with a port, like 203.0.113.5:51820 "
                          "(host names would need DNS outside Tor)")
    try:
        ip = ipaddress.IPv4Address(match.group(1))
    except ValueError:
        raise ConfigError(f"invalid Endpoint address {match.group(1)!r}") from None
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise ConfigError(f"Endpoint {ip} is not a usable server address")
    port = int(match.group(2))
    if not 1 <= port <= 65535:
        raise ConfigError("Endpoint port must be 1-65535")

    allowed = [a.strip() for a in peer.get("allowedips", "0.0.0.0/0").split(",") if a.strip()]
    if "0.0.0.0/0" not in allowed:
        raise ConfigError("AllowedIPs must include 0.0.0.0/0 (Tor must be able to reach any relay)")

    mtu = 1420
    if "mtu" in iface:
        try:
            mtu = int(iface["mtu"])
        except ValueError:
            raise ConfigError("MTU must be a number") from None
        if not 1280 <= mtu <= 1500:
            raise ConfigError("MTU must be between 1280 and 1500")
    keepalive = 25
    if "persistentkeepalive" in peer:
        value = peer["persistentkeepalive"].lower()
        keepalive = 0 if value == "off" else int(value) if value.isdigit() else -1
        if not 0 <= keepalive <= 65535:
            raise ConfigError("PersistentKeepalive must be 0-65535 or off")

    return WireGuardConfig(
        private_key=_key(iface["privatekey"], "PrivateKey"),
        address=address,
        public_key=_key(peer["publickey"], "PublicKey"),
        endpoint_ip=str(ip),
        endpoint_port=port,
        preshared_key=_key(peer["presharedkey"], "PresharedKey") if "presharedkey" in peer else None,
        mtu=mtu,
        keepalive=keepalive,
        ignored=tuple(ignored),
    )


# ---------------------------------------------------------------- user side


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "veil" / "vpn"


def list_configs() -> list[str]:
    d = config_dir()
    return sorted(p.stem for p in d.glob("*.conf")) if d.is_dir() else []


def import_config(source: Path, name: str | None = None) -> tuple[str, WireGuardConfig]:
    text = source.read_text()
    cfg = parse_config(text)
    name = name or source.stem
    if not _NAME_RE.fullmatch(name):
        raise ConfigError("name may contain only letters, digits, '.', '_' and '-'")
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    dest = d / f"{name}.conf"
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return name, cfg


def remove_config(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise ConfigError("invalid name")
    (config_dir() / f"{name}.conf").unlink()


def _read_named(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise ConfigError("invalid name")
    path = config_dir() / f"{name}.conf"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        return f.read(MAX_CONFIG_BYTES + 1)


def connect(name: str) -> subprocess.CompletedProcess:
    """Bring the VPN up through pkexec (asks for the administration password)."""
    text = _read_named(name)
    parse_config(text)  # fail early with a clear message
    return subprocess.run(["pkexec", HELPER, "up"], input=text, text=True, capture_output=True, check=False)


def disconnect() -> subprocess.CompletedProcess:
    return subprocess.run(["pkexec", HELPER, "down"], text=True, capture_output=True, check=False)


def status() -> dict:
    try:
        r = subprocess.run(["sudo", "-n", HELPER, "status"], capture_output=True, text=True, timeout=20, check=False)
        if r.returncode == 0:
            return json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return {"active": Path(f"/sys/class/net/{INTERFACE}").exists(), "error": "status helper unavailable"}
