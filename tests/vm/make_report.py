#!/usr/bin/env python3
"""Turn tests/vm results.json files into the requirement table of TEST-REPORT.md.

    make_report.py --veil RESULTS.json [--persistence RESULTS.json] [--tails RESULTS.json]
                   [--manual manual.json] > table.md

A requirement is PASS only if every mapped check passed, FAIL if any mapped
check failed, and UNTESTED if no check covers it (manual entries may add
evidence or override with a written justification).
"""

import argparse
import json
import sys

REQUIREMENTS = [
    (1, "USB live boot", ["boot.remote_shell", "20.boot_to_greeter"]),
    (2, "Amnesic behavior", ["2.amnesia_reboot", "3.ram_overlay"]),
    (3, "RAM-backed temporary storage", ["3.ram_overlay", "3.no_swap", "3.volatile_logs"]),
    (4, "No unintended internal-disk writes", ["4.no_internal_disk_writes", "4.internal_disk_not_mounted"]),
    (5, "Tor connectivity", ["5.tor_bootstrap", "5.tor_routing", "5.tor_connection_app"]),
    (6, "DNS leak prevention", ["6.dns_leak", "6.ipv6_leak", "network_guard.cli"]),
    (7, "Firewall enforcement", ["7.firewall_loaded", "network_guard.cli"]),
    (8, "Kill-switch behavior", ["8.kill_switch_before_tor", "8.kill_switch"]),
    (9, "Persistent storage", ["9.create", "9.veil_nix_feature", "9.unlock", "9.persisted", "9.boot_without_unlock"]),
    (10, "XFCE desktop integration", ["10.xfce_wayland", "10.tails_session_units", "10.only_veil_session"]),
    (11, "Tails application compatibility", ["11.app.tor-browser", "11.app.tails-persistent-storage",
                                             "5.tor_connection_app"]),
    (12, "eDEX-style terminal (Veil Console)", ["11.app.veil-console"]),
    (13, "Cybersecurity tool execution", ["13.tools_base", "13.lab_raw_scan"]),
    (14, "Nix integration", ["14.nix_install_over_tor", "14.veil_search", "9.veil_nix_feature"]),
    (15, "Veil Agent local inference", ["15.llama_present", "15.model_pull", "15.agent_inference"]),
    (16, "Veil Agent offline behavior", ["16.agent_offline"]),
    (17, "Agent tool permissions", ["17.agent_permissions_log", "15.agent_inference"]),
    (18, "Agent sandboxing", ["18.agent_sandbox"]),
    (19, "Boot compatibility", ["boot.remote_shell"]),
    (20, "Boot performance", ["20.boot_to_greeter", "20.greeter_to_desktop"]),
    (21, "Update integrity", []),
    (22, "Image reproducibility", []),
    (23, "Shutdown behavior", ["23.shutdown"]),
    (24, "Recovery procedures", []),
]


def load(path):
    if not path:
        return {}
    data = json.load(open(path))
    return {r["id"]: r for r in data["results"]}


def short(ev, n=110):
    ev = " ".join(ev.split())
    return ev if len(ev) <= n else ev[: n - 1] + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--veil", required=True)
    ap.add_argument("--persistence")
    ap.add_argument("--tails")
    ap.add_argument("--manual")
    a = ap.parse_args()
    res = {**load(a.veil), **load(a.persistence)}
    tails = load(a.tails)
    manual = json.load(open(a.manual)) if a.manual else {}
    out = ["| # | Requirement | Status | Evidence |", "|---|---|---|---|"]
    counts = {"PASS": 0, "FAIL": 0, "PARTIAL": 0, "UNTESTED": 0}
    for num, name, ids in REQUIREMENTS:
        found = [res[i] for i in ids if i in res]
        if not found:
            status = "UNTESTED"
        elif any(r["status"] == "FAIL" for r in found):
            status = "FAIL"
        else:
            status = "PASS" if len(found) == len(ids) else "PARTIAL"
        m = manual.get(str(num))
        if m and m.get("status"):
            status = m["status"]
        ev = "; ".join(f"`{r['id']}` {r['status']}: {short(r['evidence'], 80)}" for r in found)
        if num == 20 and tails:
            tg, td = tails.get("20.boot_to_greeter", {}), tails.get("20.greeter_to_desktop", {})
            ev += f"; official Tails 7.17.1 in the same harness: greeter {tg.get('evidence', '?')}, " \
                  f"desktop {td.get('evidence', '?')}"
        if m and m.get("note"):
            ev = (ev + "; " if ev else "") + m["note"]
        counts[status] += 1
        out.append(f"| {num} | {name} | **{status}** | {ev or '—'} |")
    out.append("")
    out.append(f"Totals: {counts['PASS']} PASS · {counts['PARTIAL']} PARTIAL · {counts['FAIL']} FAIL · "
               f"{counts['UNTESTED']} UNTESTED")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
