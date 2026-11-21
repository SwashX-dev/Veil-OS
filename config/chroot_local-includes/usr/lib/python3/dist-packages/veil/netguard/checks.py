"""Privacy and network-policy checks.

Each check returns a Check(id, title, status, detail, data). Status is one of
pass / warn / fail / info / skip. Parsing helpers are pure functions so they
can be unit-tested against recorded fixtures.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import os
import re
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from veil import common

HELPER = "/usr/local/lib/veil/netguard-helper"

# UIDs/users that the Tails firewall lets talk to the outside world.
EXPECTED_EGRESS_USERS = {"debian-tor"}
# Users that may egress only when the person explicitly enabled them
# (Unsafe Browser in the Welcome Screen).
OPT_IN_EGRESS_USERS = {"clearnet"}
LAN_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
# RFC 5737 documentation range: never routed on the Internet, so active
# probes towards it cannot reach a real third party even if a leak exists.
PROBE_ADDR = "192.0.2.53"


@dataclass
class Check:
    id: str
    title: str
    status: str
    detail: str
    data: dict = field(default_factory=dict)
    category: str = "network"

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Privileged data collection (via the read-only helper)
# --------------------------------------------------------------------------


def helper(cmd: str) -> dict | None:
    """Call the root helper through sudo. Returns None if unavailable."""
    if not os.path.exists(HELPER):
        return None
    argv = [HELPER, cmd] if os.geteuid() == 0 else ["sudo", "-n", HELPER, cmd]
    r = common.run(argv, timeout=30)
    if not r.ok:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# Pure parsers
# --------------------------------------------------------------------------


def parse_iptables_save(text: str) -> dict:
    """Parse iptables-save output into {table: {"policies": {}, "rules": []}}."""
    tables: dict[str, dict] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("*"):
            current = line[1:]
            tables[current] = {"policies": {}, "rules": []}
        elif line.startswith(":") and current:
            m = re.match(r":(\S+)\s+(\S+)\s+\[(\d+):(\d+)\]", line)
            if m:
                tables[current]["policies"][m.group(1)] = m.group(2)
        elif line.startswith("COMMIT"):
            current = None
        elif current:
            counters = None
            m = re.match(r"\[(\d+):(\d+)\]\s+(.*)", line)
            if m:
                counters = (int(m.group(1)), int(m.group(2)))
                line = m.group(3)
            tables[current]["rules"].append({"rule": line, "counters": counters})
    return tables


def _rules(tables: dict, table: str, chain: str) -> list[dict]:
    return [r for r in tables.get(table, {}).get("rules", [])
            if r["rule"].startswith(f"-A {chain} ")]


def uid_name_map(*tables: dict) -> dict[str, str]:
    """Map numeric --uid-owner values found in the rules to user names."""
    import pwd
    out = {}
    for t in tables:
        for tab in t.values():
            for r in tab.get("rules", []):
                for m in re.finditer(r"--uid-owner (\d+)", r["rule"]):
                    try:
                        out[m.group(1)] = pwd.getpwuid(int(m.group(1))).pw_name
                    except (KeyError, ValueError):
                        pass
    return out


def _owner(rule: str, names: dict[str, str]) -> str | None:
    m = re.search(r"--uid-owner (\S+)", rule)
    if not m:
        return None
    return names.get(m.group(1), m.group(1))


def evaluate_firewall(v4: dict, v6: dict, uid_names: dict[str, str] | None = None) -> list[Check]:
    """iptables-save prints numeric UIDs for users; uid_names maps them back."""
    names = uid_names or {}
    checks: list[Check] = []
    filt = v4.get("filter", {}).get("policies", {})
    filt6 = v6.get("filter", {}).get("policies", {})

    pol_ok = filt.get("OUTPUT") == "DROP" and filt.get("INPUT") == "DROP" and filt.get("FORWARD") == "DROP"
    pol6_ok = filt6.get("OUTPUT") == "DROP" and filt6.get("INPUT") == "DROP" and filt6.get("FORWARD") == "DROP"
    checks.append(Check(
        "firewall.default_deny", "Default-deny firewall policies",
        "pass" if pol_ok and pol6_ok else "fail",
        "IPv4 and IPv6 INPUT/OUTPUT/FORWARD policies are DROP" if pol_ok and pol6_ok
        else f"Unexpected policies: IPv4 {filt}, IPv6 {filt6}",
        {"ipv4": filt, "ipv6": filt6}, "firewall"))

    out = _rules(v4, "filter", "OUTPUT")
    last = out[-1]["rule"] if out else ""
    ends_reject = last.endswith("-j log_reject") or last.endswith("-j REJECT")
    checks.append(Check(
        "firewall.final_reject", "Everything not allowed is logged and rejected",
        "pass" if ends_reject else "fail",
        "OUTPUT chain ends with a catch-all reject" if ends_reject else f"Last OUTPUT rule is: {last!r}",
        {}, "firewall"))

    # Kill switch: no rule may ACCEPT non-loopback egress unless it is bound to
    # an expected owner, established state, the LAN subchain, or the TransPort.
    suspicious = []
    for r in out:
        rule = r["rule"]
        if "-j ACCEPT" not in rule:
            continue
        if "-o lo" in rule and "! -o lo" not in rule:
            continue
        if "--state ESTABLISHED" in rule or "--state RELATED" in rule:
            continue
        if "--dport 9040" in rule and "127.0.0.1" in rule:
            continue  # amnesia -> local TransPort (Tor)
        owner = _owner(rule, names)
        if owner and owner in EXPECTED_EGRESS_USERS | OPT_IN_EGRESS_USERS:
            continue
        suspicious.append(rule)
    lan_rules = [r["rule"] for r in v4.get("filter", {}).get("rules", []) if r["rule"].startswith("-A lan ")]
    checks.append(Check(
        "firewall.kill_switch", "Tor kill switch (no direct egress)",
        "pass" if not suspicious else "fail",
        "Only Tor (debian-tor) and the opt-in Unsafe Browser user may reach the network; "
        "if Tor stops, other traffic has nowhere to go" if not suspicious
        else f"{len(suspicious)} rule(s) allow direct egress",
        {"suspicious_rules": suspicious, "lan_rules": lan_rules}, "firewall"))

    nat = [r["rule"] for r in _rules(v4, "nat", "OUTPUT")]
    dns_redirect = any("--dport 53" in r and "--to-ports 5353" in r for r in nat)
    trans_redirect = any("--to-ports 9040" in r for r in nat)
    checks.append(Check(
        "dns.redirect", "System DNS is answered by Tor",
        "pass" if dns_redirect else "fail",
        "UDP/53 to 127.0.0.1 is redirected to Tor's DNSPort (5353)" if dns_redirect
        else "No DNS redirect to Tor's DNSPort found",
        {}, "dns"))
    checks.append(Check(
        "firewall.transproxy", "Live user's TCP is transparently routed through Tor",
        "pass" if trans_redirect else "warn",
        "amnesia's outgoing TCP is redirected to Tor's TransPort (9040)" if trans_redirect
        else "No TransPort redirect found (apps must use SOCKS explicitly)",
        {}, "firewall"))

    rejected = 0
    for r in v4.get("filter", {}).get("rules", []):
        if r["rule"].startswith("-A log_reject") and "-j REJECT" in r["rule"] and r["counters"]:
            rejected += r["counters"][0]
    checks.append(Check(
        "firewall.blocked_attempts", "Blocked outbound attempts since boot",
        "info",
        "No outbound packet has been rejected" if rejected == 0
        else f"{rejected} outbound packet(s) were rejected and logged ('Dropped outbound packet' in the journal). "
             "This includes Network Guard's own leak probes.",
        {"rejected_packets": rejected}, "firewall"))

    socks = {}
    for r in _rules(v4, "filter", "OUTPUT") + v4.get("filter", {}).get("rules", []):
        m_port = re.search(r"--dport (\d+)", r["rule"])
        owner = _owner(r["rule"], names)
        if m_port and owner and "-j ACCEPT" in r["rule"] and m_port.group(1) in ("9050", "9062", "9063"):
            socks.setdefault(m_port.group(1), set()).add(owner)
    checks.append(Check(
        "policy.socks_access", "Who may use Tor SOCKS ports",
        "info", "; ".join(f"{p}: {', '.join(sorted(u))}" for p, u in sorted(socks.items())) or "n/a",
        {k: sorted(v) for k, v in socks.items()}, "policy"))
    return checks


def evaluate_vpn(v4: dict, uid_names: dict[str, str] | None = None, state: dict | None = None) -> list[Check]:
    """VPN before Tor must be fail-closed: Tor only through WireGuard, nothing else through it."""
    names = uid_names or {}
    out = _rules(v4, "filter", "OUTPUT")
    chain = _rules(v4, "filter", "VEIL_VPN")
    active = bool(state and state.get("active"))
    if not active and not chain:
        return [Check("vpn.before_tor", "VPN before Tor", "info",
                      "Off: Tor connects directly (or through bridges)", {}, "firewall")]
    problems = []
    if not out or out[0]["rule"] != "-A OUTPUT -j VEIL_VPN":
        problems.append("the VPN rules are not the first OUTPUT rule")
    for r in chain:
        rule = r["rule"]
        if "-j ACCEPT" in rule and not ("-p udp" in rule and "--mark" in rule
                                        and re.search(r"-d \d+\.\d+\.\d+\.\d+/32", rule)):
            problems.append(f"unexpected ACCEPT rule: {rule}")
    if not any(_owner(r["rule"], names) == "debian-tor" and "-j REJECT" in r["rule"] and " -o " not in r["rule"]
               for r in chain):
        problems.append("Tor is not blocked outside the VPN")
    if active and not state.get("tor_routed_through_vpn"):
        problems.append("Tor is not routed through the VPN")
    if active and not state.get("blackhole_fallback"):
        problems.append("no blackhole fallback route (Tor could go direct if the VPN interface vanished)")
    if not active and chain:
        problems.append("VPN rules are loaded but the VPN interface is missing: Tor cannot connect")
    detail = (f"On: Tor connects only through the VPN ({state.get('endpoint', '?')})" if not problems
              else "; ".join(problems))
    return [Check("vpn.before_tor", "VPN before Tor (fail-closed)", "pass" if not problems else "fail",
                  detail, {"state": state or {}}, "firewall")]


def classify_peer(peer: str) -> str:
    """Return 'none', 'loopback', 'lan' or 'internet' for an ss peer string."""
    if peer in ("*", "0.0.0.0:*", "[::]:*", "*:*") or peer.endswith(":*"):
        return "none"
    host = peer.rsplit(":", 1)[0].strip("[]")
    host = host.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "none"
    if ip.is_loopback:
        return "loopback"
    if isinstance(ip, ipaddress.IPv4Address) and any(ip in n for n in LAN_NETS):
        return "lan"
    if ip.is_link_local or (ip.version == 4 and ip in ipaddress.ip_network("10.200.1.0/24")):
        return "lan"
    return "internet"


def _port(addr: str) -> str:
    return addr.rsplit(":", 1)[-1] if ":" in addr else ""


def evaluate_sockets(rows: list[dict], transproxy_users: set[str] | None = None) -> list[Check]:
    """transproxy_users: users whose outgoing TCP the firewall REDIRECTs to
    Tor's TransPort (ss still shows the original destination for those)."""
    transproxy_users = transproxy_users or set()
    direct, lan, optin, torified = [], [], [], 0
    for s in rows:
        kind = classify_peer(s.get("peer", ""))
        if kind in ("none", "loopback"):
            continue
        # ss omits "uid:" for root-owned sockets.
        user = s.get("user") or "root"
        # DHCP is how the computer gets an address on the local network; it
        # is part of Tails' design (NetworkManager, as root, UDP 68 -> 67).
        if s.get("proto") == "udp" and (_port(s.get("local", "")) == "68" or _port(s.get("peer", "")) == "67") \
                and set(s.get("processes", [])) <= {"NetworkManager", "dhclient", "dhcpcd"}:
            continue
        entry = {"user": user, "proto": s.get("proto"), "state": s.get("state"),
                 "processes": s.get("processes", [])}
        if kind == "internet":
            if user in EXPECTED_EGRESS_USERS:
                continue
            if s.get("proto") == "tcp" and user in transproxy_users:
                torified += 1
                continue
            (optin if user in OPT_IN_EGRESS_USERS else direct).append(entry)
        elif kind == "lan":
            if user in EXPECTED_EGRESS_USERS:
                continue
            lan.append(entry)
    checks = [Check(
        "connections.direct", "Unexpected direct Internet connections",
        "pass" if not direct else "fail",
        ("Only Tor holds connections to the Internet"
         + (f"; {torified} app connection(s) transparently routed through Tor (TransPort)" if torified else ""))
        if not direct else f"{len(direct)} socket(s) talk to the Internet without Tor",
        {"sockets": direct, "transparently_torified": torified}, "connections")]
    if optin:
        checks.append(Check(
            "connections.unsafe_browser", "Unsafe Browser connections",
            "warn", f"{len(optin)} connection(s) from the Unsafe Browser (not anonymous, enabled by you)",
            {"sockets": optin}, "connections"))
    checks.append(Check(
        "connections.lan", "Local network connections",
        "pass" if not lan else "warn",
        "No local-network connections" if not lan
        else f"{len(lan)} local-network connection(s): LAN traffic does not go through Tor",
        {"sockets": lan}, "connections"))
    return checks


def evaluate_tor(info: dict | None, bootstrapped_flag: bool) -> list[Check]:
    if info is None or not info.get("available"):
        status = "pass" if bootstrapped_flag else "warn"
        return [Check("tor.bootstrap", "Tor connection", status,
                      "Tor is connected" if bootstrapped_flag else "Tor is not connected yet",
                      {"detail": (info or {}).get("error")}, "tor")]
    p = info.get("bootstrap_progress", 0)
    checks = [Check("tor.bootstrap", "Tor connection",
                    "pass" if p == 100 else ("warn" if info.get("disable_network") else "fail"),
                    "Tor is connected (100%)" if p == 100 else
                    ("Tor is disabled until you connect (Tor Connection)" if info.get("disable_network")
                     else f"Tor bootstrap stuck at {p}%"),
                    {"progress": p, "version": info.get("version")}, "tor")]
    if p == 100:
        built = info.get("circuits_general", 0)
        live = info.get("network_liveness") == "up"
        checks.append(Check("tor.circuits", "Tor circuit health",
                            "pass" if built > 0 and live else "warn",
                            f"{built} general-purpose circuit(s) built, network {info.get('network_liveness')}",
                            {k: info.get(k) for k in ("circuits_built", "circuits_general",
                                                      "circuits_total", "streams_open",
                                                      "network_liveness")}, "tor"))
    return checks


# --------------------------------------------------------------------------
# Unprivileged checks
# --------------------------------------------------------------------------


def check_ipv6() -> Check:
    vals = {}
    for name in ("all", "default"):
        try:
            vals[name] = Path(f"/proc/sys/net/ipv6/conf/{name}/disable_ipv6").read_text().strip()
        except OSError:
            vals[name] = "n/a"
    ok = all(v == "1" for v in vals.values())
    return Check("ipv6.disabled", "IPv6 disabled (no IPv6 leak path)", "pass" if ok else "fail",
                 "IPv6 is disabled on all non-loopback interfaces" if ok else f"disable_ipv6 = {vals}",
                 vals, "ipv6")


def check_resolv_conf(path: str = "/etc/resolv.conf") -> Check:
    try:
        servers = [ln.split()[1] for ln in Path(path).read_text().splitlines()
                   if ln.strip().startswith("nameserver") and len(ln.split()) > 1]
    except OSError:
        servers = []
    ok = bool(servers) and all(s in ("127.0.0.1", "::1") for s in servers)
    return Check("dns.resolv_conf", "Resolver points to Tor", "pass" if ok else "fail",
                 "Only 127.0.0.1 is configured as nameserver" if ok else f"Nameservers: {servers or 'none'}",
                 {"nameservers": servers}, "dns")


def probe_udp(port: int, payload: bytes, timeout: float = 2.0) -> str:
    """Send a UDP datagram to the RFC 5737 probe address.

    'blocked'  -> the local firewall rejected it (expected on Veil/Tails)
    'leaked'   -> no local rejection: the packet left the machine
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.connect((PROBE_ADDR, port))
        s.send(payload)
        try:
            s.recv(512)
            return "leaked"
        except ConnectionRefusedError:
            return "blocked"
        except socket.timeout:
            # Some kernels report the ICMP error only on the next send.
            try:
                s.send(payload)
            except OSError as e:
                if e.errno in (errno.ECONNREFUSED, errno.EPERM, errno.EACCES):
                    return "blocked"
            return "leaked"
    except OSError as e:
        if e.errno in (errno.ECONNREFUSED, errno.EPERM, errno.EACCES, errno.ENETUNREACH):
            return "blocked"
        return f"error:{e.errno}"
    finally:
        s.close()


def check_dns_leak_probe() -> Check:
    q = (b"\x13\x37\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
         b"\x07example\x03com\x00\x00\x01\x00\x01")
    res = probe_udp(53, q)
    return Check("dns.leak_probe", "Active DNS leak test (direct UDP/53)",
                 {"blocked": "pass", "leaked": "fail"}.get(res, "warn"),
                 "A direct DNS query to an outside resolver was rejected locally" if res == "blocked"
                 else ("A direct DNS query left the machine: DNS LEAK" if res == "leaked" else f"Probe error {res}"),
                 {"result": res, "target": f"{PROBE_ADDR}:53 (RFC 5737, unroutable)"}, "dns")


def check_udp_leak_probe() -> Check:
    res = probe_udp(123, b"\x1b" + 47 * b"\x00")
    return Check("udp.leak_probe", "Active non-Tor UDP test (direct UDP/123)",
                 {"blocked": "pass", "leaked": "fail"}.get(res, "warn"),
                 "Direct UDP was rejected locally (Tor carries TCP only)" if res == "blocked"
                 else ("Direct UDP left the machine: LEAK" if res == "leaked" else f"Probe error {res}"),
                 {"result": res}, "network")


def check_mac_spoofing() -> Check:
    setting = "unknown"
    try:
        for line in Path("/var/lib/live/config/tails.macspoof").read_text().splitlines():
            if line.startswith("TAILS_MACSPOOF_ENABLED="):
                setting = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    r = common.run(["ip", "-j", "-d", "link", "show"], timeout=10)
    nics = []
    try:
        for link in json.loads(r.stdout or "[]"):
            name = link.get("ifname", "")
            if not os.path.exists(f"/sys/class/net/{name}/device"):
                continue  # virtual interface
            nics.append({"ifname": name, "randomized": bool(link.get("permaddr")),
                         "state": link.get("operstate")})
    except json.JSONDecodeError:
        pass
    if setting == "false":
        return Check("mac.spoofing", "MAC address anonymization", "warn",
                     "Disabled in the Welcome Screen: your hardware MAC address is visible on the local network",
                     {"setting": setting, "nics": nics}, "network")
    if not nics:
        return Check("mac.spoofing", "MAC address anonymization", "info",
                     "No physical network interface found", {"setting": setting}, "network")
    bad = [n["ifname"] for n in nics if not n["randomized"]]
    return Check("mac.spoofing", "MAC address anonymization",
                 "pass" if not bad else "warn",
                 "All physical interfaces use a randomized MAC address" if not bad
                 else f"Not randomized (or driver hides the permanent address): {', '.join(bad)}",
                 {"setting": setting, "nics": nics}, "network")


def check_hostname_timezone() -> Check:
    host = socket.gethostname()
    tz = time.tzname[0]
    ok = host in ("localhost", "amnesia") and tz in ("UTC", "GMT")
    return Check("metadata.identity", "Generic hostname and UTC clock",
                 "pass" if ok else "warn",
                 f"hostname={host}, timezone={tz}" + ("" if ok else " (may fingerprint this session)"),
                 {"hostname": host, "timezone": tz}, "metadata")


def check_metadata_tools() -> Check:
    have = common.have("mat2")
    return Check("metadata.tools", "Metadata cleaning available",
                 "info" if have else "warn",
                 "Use `mat2 FILE` before sharing documents/images: files carry author, device and GPS metadata"
                 if have else "mat2 not found: shared files may leak metadata",
                 {}, "metadata")


def check_agent_policy() -> Check:
    try:
        from veil.agent import policy
        p = policy.load_defaults()
        default_deny = not p.get("network", False)
    except Exception:  # noqa: BLE001
        default_deny = True
    granted = []
    rt = common.amnesic_state_dir("agent") / "network-grants"
    if rt.exists():
        granted = [x.name for x in rt.iterdir()]
    status = "pass" if default_deny and not granted else "warn"
    return Check("agent.network_policy", "Veil Agent network policy", status,
                 "Agent is offline by default; no session holds a network grant" if status == "pass"
                 else f"Agent network grants active: {granted or 'default=allow'}",
                 {"default_deny": default_deny, "grants": granted}, "policy")


def check_nix_policy(path: str = "/etc/nix/nix.conf") -> Check:
    try:
        text = Path(path).read_text()
    except OSError:
        return Check("nix.policy", "Nix substituter policy", "skip", "Nix not configured", {}, "policy")
    conf = {}
    for ln in text.splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, v = ln.split("=", 1)
            conf[k.strip()] = v.strip()
    subs = conf.get("substituters", "").split()
    sigs = conf.get("require-sigs", "true") != "false"
    ok = sigs and all(s == "https://cache.nixos.org" or s.startswith("https://cache.nixos.org/") for s in subs)
    return Check("nix.policy", "Nix only accepts signed binaries from the official cache",
                 "pass" if ok else "fail",
                 "require-sigs on; substituter https://cache.nixos.org; downloads run as amnesia through Tor"
                 if ok else f"require-sigs={sigs}, substituters={subs}",
                 {"substituters": subs, "require_sigs": sigs}, "policy")


def run_all(active_probes: bool = True) -> list[Check]:
    checks: list[Check] = []
    fw = helper("firewall")
    if fw:
        v4 = parse_iptables_save(fw["ipv4"]["out"])
        v6 = parse_iptables_save(fw["ipv6"]["out"])
        checks += evaluate_firewall(v4, v6, uid_name_map(v4, v6))
        vpn_state = None
        if Path("/sys/class/net/wg-veil").exists() or _rules(v4, "filter", "VEIL_VPN"):
            from veil import vpn
            vpn_state = vpn.status()
        checks += evaluate_vpn(v4, uid_name_map(v4), vpn_state)
    else:
        checks.append(Check("firewall.default_deny", "Firewall inspection", "skip",
                            "Root helper unavailable (not running on Veil OS?)", {}, "firewall"))
    checks.append(check_resolv_conf())
    checks.append(check_ipv6())
    if active_probes:
        checks.append(check_dns_leak_probe())
        checks.append(check_udp_leak_probe())
    checks += evaluate_tor(helper("tor"), common.tor_bootstrapped())
    socks = helper("sockets")
    if socks:
        tp = set()
        if fw:
            names = uid_name_map(v4)
            for r in _rules(v4, "nat", "OUTPUT"):
                if "--to-ports 9040" in r["rule"] and "! -o lo" in r["rule"]:
                    owner = _owner(r["rule"], names)
                    if owner:
                        tp.add(owner)
        checks += evaluate_sockets(socks.get("sockets", []), tp)
    else:
        checks.append(Check("connections.direct", "Connection audit", "skip",
                            "Root helper unavailable", {}, "connections"))
    checks.append(check_mac_spoofing())
    checks.append(check_hostname_timezone())
    checks.append(check_metadata_tools())
    checks.append(check_agent_policy())
    checks.append(check_nix_policy())
    return checks
