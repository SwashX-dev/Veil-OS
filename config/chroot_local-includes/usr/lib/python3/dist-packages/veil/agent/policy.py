"""Permission model for Veil Agent.

Levels control what the agent may *write*; network and secrets are
independent grants. Classification of risky commands is a second layer on
top of the kernel sandbox: the sandbox is what actually prevents network
use, privilege escalation and writes outside the allowed paths; the
classifier exists so that the human is asked before anything destructive
is even attempted.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from veil.sandbox import SECRET_PATHS, SandboxSpec

LEVELS = ("read-only", "workspace", "full-control")
DEFAULTS_FILE = Path("/etc/veil/agent.toml")


def load_defaults(path: Path = DEFAULTS_FILE) -> dict:
    defaults = {"level": "read-only", "mode": "agent", "network": False, "secrets": False,
                "max_steps": 24, "temperature": 0.3, "context": 8192}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        defaults.update({k: v for k, v in data.get("agent", {}).items() if k in defaults})
    except (OSError, tomllib.TOMLDecodeError):
        pass
    # Hard rule: the configured default can never turn networking on.
    defaults["network"] = False
    defaults["secrets"] = False
    if defaults["level"] == "full-control":
        defaults["level"] = "workspace"
    return defaults


@dataclass
class Risk:
    kind: str
    reason: str


# Patterns are deliberately broad: a false positive costs one confirmation.
_DESTRUCTIVE_DISK = re.compile(
    r"(\b(dd|mkfs(\.\w+)?|mke2fs|wipefs|shred|sgdisk|sfdisk|fdisk|gdisk|parted|blkdiscard|"
    r"cryptsetup|zpool|mdadm|badblocks|nvme\s+format|hdparm\s+.*--security-erase)\b"
    r"|of=/dev/|>\s*/dev/(sd|nvme|mmcblk|vd|hd|dm-|loop))")
_SECURITY_BOUNDARY = re.compile(
    r"\b(sudo|su|pkexec|doas|iptables(-restore)?|ip6tables(-restore)?|nft|ferm|setcap|chattr|"
    r"modprobe|insmod|rmmod|apparmor_parser|aa-(disable|complain|enforce)|sysctl\s+-w|"
    r"systemctl\s+(stop|disable|mask|kill)|mount|umount|swapon|chown\s+root|"
    r"passwd|usermod|visudo)\b|chmod\s+[0-7]*[4-7][0-7]{3}\b|chmod\s+[ugoa]*\+s|/etc/(tor|ferm|sudoers)")
_CREDENTIALS = re.compile(
    r"(\.gnupg|\.ssh/id_|private-keys-v1\.d|\.kdbx\b|\.password-store|keyrings/|"
    r"--export-secret|secret-tool|\.tor-browser|cookies\.sqlite|logins\.json|key4\.db|"
    r"\.electrum|wallet)", re.IGNORECASE)
_MASS_DELETE = re.compile(r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*\s+)+(/|~|\$HOME|\*|\.\.?)(\s|$|/)")
_NETWORK = re.compile(
    r"\b(curl|wget|ssh|scp|sftp|rsync|nc|ncat|netcat|socat|nmap|telnet|ftp|torsocks|"
    r"git\s+(clone|fetch|pull|push|ls-remote|submodule)|pip3?\s+install|npm\s+(install|i)\b|"
    r"nix\s+(build|shell|profile|run|develop|flake)|apt(-get)?\s+(install|update)|"
    r"onionshare|gpg\s+--(recv|send|search|refresh)-keys?)\b")


def classify(command: str) -> list[Risk]:
    risks = []
    if _DESTRUCTIVE_DISK.search(command):
        risks.append(Risk("destructive-disk", "may overwrite or erase a disk or partition"))
    if _SECURITY_BOUNDARY.search(command):
        risks.append(Risk("security-boundary", "touches privileges, firewall, services or system policy"))
    if _CREDENTIALS.search(command):
        risks.append(Risk("credentials", "accesses keys, passwords, wallets or browser secrets"))
    if _MASS_DELETE.search(command):
        risks.append(Risk("mass-delete", "recursive delete of a broad path"))
    return risks


def mentions_network(command: str) -> bool:
    return bool(_NETWORK.search(command))


@dataclass
class Policy:
    level: str = "read-only"
    workspace: str | None = None
    network: bool = False
    network_scope: list[str] = field(default_factory=list)
    secrets: bool = False
    max_steps: int = 24

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"unknown permission level {self.level!r}")
        if self.level == "workspace" and not self.workspace:
            self.workspace = os.getcwd()

    # ---- filesystem ------------------------------------------------------

    def writable_roots(self) -> list[str]:
        if self.level == "workspace" and self.workspace:
            return [os.path.realpath(os.path.expanduser(self.workspace))]
        if self.level == "full-control":
            return [os.path.realpath(os.path.expanduser("~")), "/tmp"]  # noqa: S108
        return []

    def can_write(self, path: str) -> tuple[bool, str]:
        real = os.path.realpath(os.path.expanduser(path))
        if not self.secrets:
            for raw in SECRET_PATHS:
                sp = os.path.realpath(os.path.expanduser(raw))
                if real == sp or real.startswith(sp + os.sep):
                    return False, f"{path} is a credential store (grant secrets to allow)"
        for root in self.writable_roots():
            if real == root or real.startswith(root.rstrip(os.sep) + os.sep):
                return True, ""
        if self.level == "read-only":
            return False, "read-only mode: switch to workspace mode to edit files"
        return False, f"{path} is outside the writable scope {self.writable_roots()}"

    def can_read(self, path: str) -> tuple[bool, str]:
        real = os.path.realpath(os.path.expanduser(path))
        if not self.secrets:
            for raw in SECRET_PATHS:
                sp = os.path.realpath(os.path.expanduser(raw))
                if real == sp or real.startswith(sp + os.sep):
                    return False, f"{path} is a credential store (grant secrets to allow)"
        return True, ""

    # ---- sandbox ---------------------------------------------------------

    def sandbox_spec(self, cwd: str | None = None) -> SandboxSpec:
        return SandboxSpec(
            network=self.network,
            readonly_root=True,
            writable=self.writable_roots(),
            hide_secrets=not self.secrets,
            hide_session=True,
            unshare_pid=self.level != "full-control",
            cwd=cwd or self.workspace or os.path.expanduser("~"),
            hostname="veil-agent",
        )

    def describe(self) -> str:
        parts = [f"level={self.level}"]
        if self.workspace:
            parts.append(f"workspace={self.workspace}")
        parts.append("network=" + ("GRANTED" + (f" scope={self.network_scope}" if self.network_scope else "")
                                   if self.network else "denied"))
        parts.append("secrets=" + ("GRANTED" if self.secrets else "hidden"))
        return " ".join(parts)

    def out_of_scope_hosts(self, command: str) -> list[str]:
        """Hosts named in a command that are outside the granted network scope."""
        if not self.network or not self.network_scope:
            return []
        hosts = set(re.findall(r"(?:https?://|@)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|\d{1,3}(?:\.\d{1,3}){3})", command))
        scoped = []
        for h in hosts:
            if not any(h == s or h.endswith("." + s) for s in self.network_scope):
                scoped.append(h)
        return sorted(scoped)
