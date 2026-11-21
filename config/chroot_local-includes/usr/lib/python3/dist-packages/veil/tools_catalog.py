"""Cybersecurity tool catalog: network policy and privacy status per tool."""

from __future__ import annotations

import json
from pathlib import Path

from veil import SHARE_DIR

NETWORK_POLICIES = {
    "none": "No network use.",
    "tor-tcp": "TCP only; transparently routed through Tor as the live user. UDP/ICMP/raw packets are blocked.",
    "lan-direct": "Talks to the local network directly (allowed by Tails for LAN addresses; not anonymous).",
    "offline-lab": "Needs raw sockets/UDP: run it inside `veil lab` against local targets (no Internet route).",
    "root-capture": "Packet capture needs root (set an administration password in the Welcome Screen).",
    "radio": "Uses Wi-Fi/radio hardware directly (monitor mode): exposes nothing over IP but is visible over the air.",
}

PRIVACY_STATUS = {
    "compatible": "Works within the Tails/Veil privacy model.",
    "tor-limited": "Works over Tor with reduced functionality (TCP connect only, slower).",
    "lab-only": "Incompatible with Tor; Veil confines it to offline labs.",
    "caution": "Can reveal identifying information (hardware, radio, or LAN presence). Read the notes.",
}


def load() -> list[dict]:
    try:
        return json.loads((Path(SHARE_DIR) / "tools.json").read_text())["tools"]
    except (OSError, json.JSONDecodeError, KeyError):
        return []


def get(name: str) -> dict | None:
    return next((t for t in load() if t["name"].lower() == name.lower()), None)


def search(term: str) -> list[dict]:
    t = term.lower()
    return [x for x in load() if t in x["name"].lower() or t in x["category"].lower()
            or t in x.get("description", "").lower()]
