#!/usr/bin/env python3
"""End-to-end test of VPN before Tor with a real WireGuard server.

    vpn_test.py --img IMG --out DIR --config test.conf --server-ssh CMD [--overlay-base ...]

The VM's QEMU network is 198.18.0.0/24 (outside Tails' LAN exception), so
the only way out for WireGuard is Veil's firewall-mark rule. The WireGuard
server (e.g. the build VM) is reached at 198.18.0.2:51820 through a host
port forward; it masquerades to the Internet. `--server-ssh` is a command
prefix that runs shell commands on that server (to take it down/up).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import overlay  # noqa: E402
import veilvm  # noqa: E402
from run_tests import RESULTS, SESSION_ENV, connect_tor, record, session_screenshot, sh, wait_for  # noqa: E402

HELPER = "/usr/local/lib/veil/veil-vpn-helper"
CHECK = "curl -s --max-time 45 https://check.torproject.org/api/ip"


def user(vm, cmd, timeout=300):
    return sh(vm, SESSION_ENV + cmd, user="amnesia", timeout=timeout)


def is_tor(vm) -> tuple[bool, str]:
    rc, out = user(vm, CHECK, timeout=90)
    return rc == 0 and '"IsTor":true' in out.replace(" ", ""), out.strip()


def tcp_on_uplink(vm, seconds: int, during: str = "") -> int:
    """Count TCP packets on the physical interface while `during` runs."""
    sh(vm, f"rm -f /tmp/up.pcap; (timeout {seconds} tcpdump -ni $(ip -4 route show default | awk '{{print $5; exit}}') "
           f"-w /tmp/up.pcap 'tcp and not port 22' >/dev/null 2>&1 &) ; sleep 2")
    if during:
        user(vm, during, timeout=seconds + 30)
    time.sleep(seconds)
    rc, out = sh(vm, "tcpdump -nr /tmp/up.pcap 2>/dev/null | wc -l")
    return int(out.strip() or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", required=True, help="WireGuard client configuration for the test server")
    ap.add_argument("--server-ssh", required=True, help="command prefix to run commands on the WireGuard server")
    ap.add_argument("--overlay-base")
    ap.add_argument("--debs", nargs="*", default=[])
    ap.add_argument("--sudoers", nargs="*", default=[])
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[2]
    server = shlex.split(a.server_ssh)

    def on_server(cmd: str) -> str:
        return subprocess.run([*server, cmd], capture_output=True, text=True, timeout=60).stdout

    kernel, initrd, cmdline = veilvm.boot_files(a.img, None, str(out / "boot"))
    vm = veilvm.VM(a.img, str(out / "vm"), memory="6144", kernel=kernel, initrd=initrd,
                   cmdline=cmdline + " autotest_never_use_this_option")
    base_argv = vm.argv

    def argv():
        av = base_argv()
        i = av.index("user,model=virtio-net-pci")
        av[i] = "user,model=virtio-net-pci,net=198.18.0.0/24,host=198.18.0.2,dns=198.18.0.3"
        return av
    vm.argv = argv
    vm.start()
    try:
        vm.rs.wait_up(300)
        vm.rs.signal_ready()
        if a.overlay_base:
            log = overlay.apply(vm.rs, repo, a.overlay_base, [Path(d) for d in a.debs], [Path(s) for s in a.sudoers])
            (out / "overlay.log").write_text("\n".join(log) + "\n")
        veilvm.login_via_greeter(vm.rs)
        wait_for(vm, "pgrep -u amnesia -x xfce4-panel", 120)
        on_server("sudo -n ip link set wg0 up")

        # Import as the live user (strict parser, private file)
        overlay.push(vm.rs, Path(a.config).read_bytes(), "/home/amnesia/test-vpn.conf", 0o600)
        sh(vm, "chown amnesia:amnesia /home/amnesia/test-vpn.conf")
        rc, imp = user(vm, "veil vpn import ~/test-vpn.conf --name test-vpn && "
                           "stat -c %a ~/.config/veil/vpn/test-vpn.conf && rm ~/test-vpn.conf")
        record("vpn.import", "Proton-style WireGuard file imported (DNS/IPv6 ignored, stored 0600)",
               rc == 0 and "600" in imp and "DNS" in imp, imp)

        # Without the administration password nothing happens
        rc, denied = user(vm, "timeout 25 veil vpn connect test-vpn", timeout=60)
        session_screenshot(vm, "vpn-auth-prompt")
        sh(vm, "pkill -x pkexec; true")
        rc_link, _ = sh(vm, "ip link show wg-veil")
        record("vpn.requires_auth", "Connecting asks for authentication; nothing changes without it",
               rc != 0 and rc_link != 0, denied.strip()[-300:])

        # Connect before Tor (as root, standing in for a successful pkexec)
        rc, up = sh(vm, f"{HELPER} up < /home/amnesia/.config/veil/vpn/test-vpn.conf")
        state = json.loads(up[up.index("{"):]) if "{" in up else {}
        record("vpn.up", "VPN interface, Tor-only routing rule, firewall and blackhole fallback",
               rc == 0 and state.get("active") and state.get("tor_routed_through_vpn") and state.get("firewall")
               and state.get("blackhole_fallback"), up)
        t0 = time.time()
        ok, _ = connect_tor(vm)
        record("vpn.tor_bootstrap", "Tor bootstraps through the VPN", ok, f"{time.time() - t0:.0f}s")
        ok, tor = is_tor(vm)
        record("vpn.tor_works", "Web traffic still exits through Tor", ok, tor)
        rc, socks = sh(vm, "ss -tnpH state established | grep '\"tor\"' | awk '{print $3}' | sort -u")
        local = [s for s in socks.split() if not s.startswith("127.")]
        record("vpn.tor_sockets_in_tunnel", "Tor's connections originate from the VPN address",
               bool(local) and all(s.startswith("10.66.66.2:") for s in local), socks)
        tcp = tcp_on_uplink(vm, 25, during=CHECK)
        server_stats = on_server("sudo -n wg show wg0 transfer")
        record("vpn.no_direct_tcp", "Only encrypted WireGuard UDP on the physical interface (no TCP)",
               tcp == 0, f"TCP packets on uplink: {tcp}; server transfer: {server_stats.strip()}")
        rc, ng = user(vm, "veil privacy status --json --no-probes", timeout=180)
        try:
            data = json.loads(ng[ng.index("{"):])
            vpn_check = next(c for c in data["checks"] if c["id"] == "vpn.before_tor")
            failing = [c["id"] for c in data["checks"] if c["status"] == "fail"]
            record("vpn.netguard", "Network Guard verifies the fail-closed VPN rules",
                   vpn_check["status"] == "pass" and not failing, f"{vpn_check['detail']} failing={failing}")
        except (ValueError, KeyError, StopIteration) as exc:
            record("vpn.netguard", "Network Guard", False, f"{exc}: {ng[:400]}")

        # Tails reloads its firewall when an interface comes up
        sh(vm, "/etc/NetworkManager/dispatcher.d/00-firewall.sh $(ip -4 route show default | awk '{print $5}') up")
        rc, first = sh(vm, "iptables -S OUTPUT | sed -n 2p")
        ok, tor = is_tor(vm)
        record("vpn.survives_firewall_reload", "VPN rules come back after Tails reloads its firewall",
               first.strip() == "-A OUTPUT -j VEIL_VPN" and ok, f"first rule: {first.strip()} tor={ok}")

        # Kill switch 1: the VPN server goes away
        on_server("sudo -n ip link set wg0 down")
        time.sleep(5)
        rc, _ = user(vm, "curl -s --max-time 40 https://check.torproject.org/api/ip", timeout=90)
        tcp = tcp_on_uplink(vm, 30, during="curl -s --max-time 25 https://www.torproject.org/ >/dev/null")
        record("vpn.killswitch_server_down", "VPN server down: Tor cannot connect and nothing goes direct",
               tcp == 0, f"TCP packets on uplink while retrying: {tcp}")
        on_server("sudo -n ip link set wg0 up")
        ok, _ = wait_for(vm, SESSION_ENV + CHECK + " | grep -q 'IsTor\":true'", 240, user="amnesia", interval=10)
        record("vpn.recovers", "Tor recovers when the VPN server is back", ok, "")

        # Kill switch 2: the WireGuard interface disappears
        sh(vm, "ip link del wg-veil")
        rc, route = sh(vm, "ip route get 1.1.1.1 uid $(id -u debian-tor) 2>&1")
        tcp = tcp_on_uplink(vm, 25, during="curl -s --max-time 20 https://www.torproject.org/ >/dev/null")
        record("vpn.killswitch_interface_gone", "WireGuard interface removed: Tor's route is a blackhole",
               ("blackhole" in route or "unreachable" in route.lower()) and tcp == 0, f"{route.strip()} tcp={tcp}")
        rc, up = sh(vm, f"{HELPER} up < /home/amnesia/.config/veil/vpn/test-vpn.conf")
        ok, _ = wait_for(vm, SESSION_ENV + CHECK + " | grep -q 'IsTor\":true'", 240, user="amnesia", interval=10)
        record("vpn.reconnect", "Reconnecting restores Tor through the VPN", rc == 0 and ok, "")

        # Disconnect: back to Tails' normal direct Tor connection
        rc, down = sh(vm, f"{HELPER} down")
        rc_chain, _ = sh(vm, "iptables -S VEIL_VPN")
        rc_rule, rules = sh(vm, "ip rule show priority 5300")
        rc_sr, sr = sh(vm, "grep -c VEIL_VPN /var/lib/iptables/session-rules || true")
        ok, tor = (False, "")
        deadline = time.time() + 240
        while time.time() < deadline and not ok:
            ok, tor = is_tor(vm)
        tcp = tcp_on_uplink(vm, 20, during=CHECK)
        record("vpn.down", "Disconnect removes every rule; Tor connects directly again",
               rc == 0 and rc_chain != 0 and not rules.strip() and sr.strip() == "0" and ok and tcp > 0,
               f"chain removed={rc_chain != 0} rule='{rules.strip()}' session-rules lines={sr.strip()} "
               f"tor={ok} direct TCP packets={tcp}")
    finally:
        vm.stop(60)
        (out / "results.json").write_text(json.dumps(RESULTS, indent=2))
        summary = {s: sum(1 for r in RESULTS if r["status"] == s) for s in ("PASS", "FAIL", "UNTESTED")}
        print("SUMMARY", summary, flush=True)
    return 0 if not any(r["status"] == "FAIL" for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
