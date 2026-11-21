#!/usr/bin/env python3
"""Veil OS image test runner (QEMU/KVM + Tails remote shell).

    python3 tests/vm/run_tests.py --img veil.img --iso veil.iso --out results/ [--flavor veil|tails]

Every check records PASS/FAIL with the command output as evidence.
Results go to <out>/results.json and screenshots to <out>/*.png.
--flavor tails runs the comparable subset against an upstream Tails image
(baseline for boot-time and regression comparisons).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import veilvm  # noqa: E402

RESULTS: list[dict] = []


def record(rid: str, name: str, ok: bool | None, evidence: str, **extra) -> None:
    status = "PASS" if ok else ("UNTESTED" if ok is None else "FAIL")
    RESULTS.append({"id": rid, "name": name, "status": status, "evidence": evidence.strip()[:3000], **extra})
    print(f"[{status:8}] {rid} {name}: {evidence.strip().splitlines()[0][:150] if evidence.strip() else ''}",
          flush=True)


def guard(rid: str, name: str):
    def deco(fn):
        def run(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:  # noqa: BLE001
                record(rid, name, False, f"exception: {type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}")
                return None
        return run
    return deco


def sh(vm, cmd, user="root", timeout=120):
    rc, out, err = vm.rs.sh(cmd, user=user, timeout=timeout)
    return rc, out + (("\n" + err) if err.strip() else "")


def amnesia(vm, cmd, timeout=300):
    """Run as the live user with the graphical session environment."""
    return sh(vm, cmd, user="amnesia", timeout=timeout)


def wait_for(vm, cmd, timeout, user="root", interval=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            rc, out = sh(vm, cmd, user=user, timeout=30)
            if rc == 0:
                return True, out
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    return False, ""


SESSION_ENV = 'export $(systemctl --user show-environment | grep -E "^(WAYLAND_DISPLAY|XDG_RUNTIME_DIR)=" | xargs); '


def session_screenshot(vm, name):
    """Screenshot through the compositor (wlr-screencopy): QEMU's screendump
    can return stale frames with labwc on virtio-gpu."""
    out = Path(vm.workdir) / f"{name}.png"
    try:
        rc, _ = sh(vm, SESSION_ENV + f"grim /tmp/{name}.png", user="amnesia", timeout=60)
        if rc == 0:
            out.write_bytes(vm.rs.read_file(f"/tmp/{name}.png"))
            return str(out)
    except Exception:  # noqa: BLE001
        pass
    return vm.screenshot(name)


def connect_tor(vm, timeout=420):
    """Enable Tor the way Tor Connection does (DisableNetwork=0) and wait for bootstrap."""
    code = ("from stem.control import Controller\n"
            "c=Controller.from_port(port=9052); c.authenticate(); c.set_conf('DisableNetwork','0'); print('ok')")
    sh(vm, f"python3 -c {shlex.quote(code)}")
    return wait_for(vm, "test -e /run/tor-has-bootstrapped/done || "
                        "python3 -c \"from stem.control import Controller;c=Controller.from_port(port=9052);"
                        "c.authenticate();import sys;sys.exit(0 if 'PROGRESS=100' in c.get_info('status/bootstrap-phase') else 1)\"",
                    timeout, interval=5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True)
    ap.add_argument("--iso", help="ISO (optional: boot files are read from the .img otherwise)")
    ap.add_argument("--out", default="vm-results")
    ap.add_argument("--flavor", choices=["veil", "tails"], default="veil")
    ap.add_argument("--memory", default="6144")
    ap.add_argument("--skip-network-tests", action="store_true")
    ap.add_argument("--agent-model", default="qwen3-0.6b")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    veil = a.flavor == "veil"
    title = "Welcome to Veil OS" if veil else "Welcome to Tails!"
    button = "Start Veil OS" if veil else "Start Tails"

    # Blank "internal disk" to prove nothing is written to computer disks.
    disk = out / "internal-disk.raw"
    subprocess.run(["truncate", "-s", "2G", str(disk)], check=True)
    subprocess.run(["sh", "-c", f"printf 'VEIL-INTERNAL-DISK-CANARY' | dd of={disk} bs=1 seek=1048576 conv=notrunc 2>/dev/null"],
                   check=True)
    disk_sum_before = subprocess.run(["sha256sum", str(disk)], capture_output=True, text=True).stdout.split()[0]

    kernel, initrd, cmdline = veilvm.boot_files(a.img, a.iso, str(out / "boot"))
    vm = veilvm.VM(a.img, str(out / "vm"), mode="usb", firmware="uefi", memory=a.memory, cpus=2,
                   kernel=kernel, initrd=initrd, cmdline=cmdline + " autotest_never_use_this_option")
    # Attach the internal disk as raw (sha256 comparable after shutdown).
    orig_argv = vm.argv

    def argv_with_disk():
        av = orig_argv()
        return av + ["-drive", f"file={disk},if=none,id=internal,format=raw",
                     "-device", "ahci,id=ahci", "-device", "ide-hd,drive=internal,bus=ahci.0"]
    vm.argv = argv_with_disk
    vm.start()
    t0 = time.time()
    try:
        vm.rs.wait_up(300)
        vm.rs.signal_ready()
        record("boot.remote_shell", "Image boots (UEFI, USB) and the system reaches early userspace", True,
               f"remote shell up after {time.time() - t0:.1f}s")

        ok, _ = wait_for(vm, "pgrep -f '[t]ails-greeter.py' >/dev/null", 300)
        t_greeter = time.time() - t0
        time.sleep(5)
        vm.screenshot("01-welcome-screen")
        record("20.boot_to_greeter", "Boot performance: power-on to Welcome Screen", ok, f"{t_greeter:.1f}s",
               seconds=round(t_greeter, 1))

        t1 = time.time()
        veilvm.login_via_greeter(vm.rs, title=title, button_re=button)
        sess_proc = "xfce4-panel" if veil else "gnome-shell --mode=user|gnome-shell$"
        ok, _ = wait_for(vm, f"pgrep -u amnesia -f '{sess_proc}' >/dev/null", 240)
        t_desktop = time.time() - t1
        time.sleep(8)
        session_screenshot(vm, "02-desktop") if veil else vm.screenshot("02-desktop")
        record("20.greeter_to_desktop", "Boot performance: Start → desktop ready", ok, f"{t_desktop:.1f}s",
               seconds=round(t_desktop, 1))

        run_session_checks(vm, veil)
        run_amnesia_checks(vm)
        if not a.skip_network_tests:
            run_network_checks(vm, veil)
        if veil:
            run_veil_checks(vm, out, a)
        run_app_checks(vm, veil)
    finally:
        t_sd = time.time()
        vm.stop(timeout=180)
        record("23.shutdown", "Shutdown completes", vm.proc.returncode is not None,
               f"powered off in {time.time() - t_sd:.1f}s (rc={vm.proc.returncode})", seconds=round(time.time() - t_sd, 1))
        disk_sum_after = subprocess.run(["sha256sum", str(disk)], capture_output=True, text=True).stdout.split()[0]
        record("4.no_internal_disk_writes", "No writes to the computer's internal disk",
               disk_sum_before == disk_sum_after, f"sha256 before={disk_sum_before[:16]} after={disk_sum_after[:16]}")
        (out / "results.json").write_text(json.dumps({"flavor": a.flavor, "image": a.img,
                                                       "results": RESULTS}, indent=1))
        n = {s: sum(1 for r in RESULTS if r["status"] == s) for s in ("PASS", "FAIL", "UNTESTED")}
        print(f"\nSUMMARY {n}")
    return 0 if not any(r["status"] == "FAIL" for r in RESULTS) else 1


@guard("10.session", "Desktop session")
def run_session_checks(vm, veil):
    rc, osr = sh(vm, "cat /etc/os-release")
    if veil:
        record("branding.os_release", "os-release identifies Veil OS (Tails base kept)",
               'ID="veil"' in osr and 'VERSION="7.17.1"' in osr, osr)
        rc, out = sh(vm, "for p in labwc xfce4-panel swaybg polkit-mate-authentication-agent-1; do "
                         "printf '%s=' $p; pgrep -u amnesia -c -f $p; done; "
                         "printf 'Xorg='; pgrep -c -x Xorg; ls /usr/bin/Xorg 2>&1 | tail -1")
        ok = all(f"{p}=0" not in out for p in ("labwc", "xfce4-panel", "swaybg")) and "Xorg=0" in out
        record("10.xfce_wayland", "XFCE panel on labwc (Wayland), no X.Org", ok, out)
        rc, out = sh(vm, "systemctl --user -M amnesia@ is-active veil-session.target tails-dump-user-env.service "
                         "tails-ibus-proxy.service tails-a11y-bus-proxy.service; systemctl is-active gnome-session.target; "
                         "test -s /run/user/1000/user-env && echo user-env-ok")
        record("10.tails_session_units", "Tails session plumbing works in the Veil session (user env, IBus/a11y proxies)",
               out.split().count("active") >= 5 and "user-env-ok" in out, out)
        rc, out = sh(vm, "for i in $(seq 1 30); do systemctl --user -M amnesia@ is-active --quiet tca.service && "
                         "{ echo tca-active; break; }; sleep 2; done; systemctl --user -M amnesia@ is-active tca.service")
        record("5.tor_connection_app", "Tor Connection (tca) starts in the Veil session", "tca-active" in out, out)
        rc, out = sh(vm, "ls /usr/share/wayland-sessions/; systemctl --user -M amnesia@ is-enabled tails-upgrade-frontend.service; true")
        record("10.only_veil_session", "Only the Veil session is installed; Tails upgrader masked",
               "veil.desktop" in out and "gnome.desktop" not in out and "masked" in out, out)


@guard("3.amnesia", "Amnesia")
def run_amnesia_checks(vm):
    rc, out = sh(vm, "findmnt -no SOURCE,FSTYPE,OPTIONS /; echo ---; findmnt -rno FSTYPE,TARGET | grep -E 'live/(mount/)?overlay'; "
                     "echo ---; cat /proc/swaps; echo ---; findmnt -lno SOURCE,TARGET | grep -E '^/dev/(sd|nvme|vd|hd)' || true; "
                     "echo ---; lsblk -dno NAME,TRAN,RM,SIZE")
    parts = out.split("---")
    overlay = "overlay" in parts[0] and "tmpfs" in parts[1]
    swaps = [ln for ln in parts[2].strip().splitlines()[1:] if ln.strip()]
    no_swap = all(ln.split()[0].startswith("/dev/zram") for ln in swaps)
    record("3.ram_overlay", "Root filesystem is an overlay whose writable layer is tmpfs (RAM)", overlay, out)
    record("3.no_swap", "No swap on disk (zram, i.e. compressed RAM, only)", no_swap, parts[2])
    mounted = [ln for ln in parts[3].strip().splitlines() if ln and "/run/live/medium" not in ln
               and "/lib/live" not in ln]
    record("4.internal_disk_not_mounted", "Internal disk is not mounted automatically", not mounted, parts[3] + parts[4])
    rc, out = sh(vm, "journalctl --header | grep -m1 -i 'file path'; findmnt -no FSTYPE -T /var/log/journal")
    record("3.volatile_logs", "Journal lives in RAM (/run, or /var/log on the tmpfs-backed overlay)",
           "/run/log/journal" in out or (overlay and "overlay" in out), out)


@guard("5.network", "Network")
def run_network_checks(vm, veil):
    rc, out = sh(vm, "iptables -S OUTPUT | head -3; ip6tables -S OUTPUT | head -2; cat /etc/resolv.conf; "
                     "sysctl net.ipv6.conf.all.disable_ipv6")
    record("7.firewall_loaded", "Default-deny firewall loaded (IPv4+IPv6), resolver 127.0.0.1, IPv6 disabled",
           "-P OUTPUT DROP" in out and "nameserver 127.0.0.1" in out and "disable_ipv6 = 1" in out, out)
    # Leak probes BEFORE Tor is connected: nothing may leave.
    rc, out = amnesia(vm, "curl -sS -m 10 https://check.torproject.org/api/ip; echo rc=$?")
    record("8.kill_switch_before_tor", "Before Tor connects, the live user cannot reach the Internet",
           "IsTor" not in out, out)
    ok, _ = connect_tor(vm)
    session_screenshot(vm, "03-tor-connected") if veil else vm.screenshot("03-tor-connected")
    record("5.tor_bootstrap", "Tor connects and bootstraps to 100%", ok, "bootstrap flag present" if ok else "timeout")
    if not ok:
        return
    out = ""
    for _attempt in range(4):  # a single exit relay can fail; Tor retries on a new circuit
        rc, o = amnesia(vm, "curl -sS -m 60 https://check.torproject.org/api/ip")
        out += o + "\n"
        if '"IsTor":true' in o.replace(" ", ""):
            break
        time.sleep(10)
    record("5.tor_routing", "Live user's web traffic exits through Tor (check.torproject.org)",
           '"IsTor":true' in out.replace(" ", ""), out)
    rc, out = amnesia(vm, "getent hosts example.com; echo rc=$?; "
                          "python3 -c \"import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
                          "s.settimeout(5);s.sendto(b'x',('8.8.8.8',53));print(s.recvfrom(100))\" 2>&1 | tail -1")
    record("6.dns_leak", "DNS resolves through Tor; direct UDP/53 to 8.8.8.8 is rejected",
           "rc=0" in out and ("refused" in out.lower() or "not permitted" in out.lower()), out)
    rc, out = amnesia(vm, "curl -6 -sS -m 10 https://ipv6.google.com -o /dev/null; echo rc=$?")
    record("6.ipv6_leak", "No IPv6 connectivity (IPv6 leak path closed)", "rc=0" not in out, out)
    if veil:
        rc, out = amnesia(vm, "veil privacy status --json", timeout=300)
        try:
            rep = json.loads(out[out.index("{"):])
            fails = [c["id"] for c in rep["checks"] if c["status"] == "fail"]
            record("network_guard.cli", "veil privacy status: no failing checks with Tor connected",
                   not fails, json.dumps(rep["summary"]) + f" failing={fails}")
            (Path(vm.workdir).parent / "network-guard-report.json").write_text(out[out.index("{"):])
        except (ValueError, KeyError) as e:
            record("network_guard.cli", "veil privacy status", False, f"{e}: {out[:1500]}")
        rc, out = sh(vm, "iptables-save -c; echo '#####'; ip6tables-save -c")
        (Path(vm.workdir).parent / "iptables-save.txt").write_text(out)
    # Kill switch: stop Tor, the live user must lose connectivity (no fallback).
    sh(vm, "systemctl stop tor@default.service")
    time.sleep(3)
    rc, out = amnesia(vm, "curl -sS -m 15 https://check.torproject.org/api/ip; echo rc=$?")
    rc2, cnt = sh(vm, "iptables -L log_reject -v -n -x | awk 'NR==3{print $1}'")
    record("8.kill_switch", "With Tor stopped, traffic is blocked (no clearnet fallback)",
           "IsTor" not in out and "rc=0" not in out, out + f"\nlog_reject packets: {cnt}")
    sh(vm, "systemctl start tor@default.service")
    connect_tor(vm, timeout=300)


@guard("veil", "Veil components")
def run_veil_checks(vm, out_dir, a):
    rc, out = amnesia(vm, "veil security audit --json --no-probes", timeout=300)
    try:
        rep = json.loads(out[out.index("{"):])
        by = {c["id"]: c for c in rep["checks"]}
        sb = by.get("sandbox.self_test", {})
        record("18.agent_sandbox", "Veil sandbox in the image: no net, no DNS, no session bus, RO root, seccomp",
               sb.get("status") == "pass", json.dumps(sb.get("data", {})))
        fails = [c["id"] for c in rep["checks"] if c["status"] == "fail"]
        record("audit.cli", "veil security audit: no failing checks", not fails,
               json.dumps(rep["summary"]) + f" failing={fails}")
        (out_dir / "security-audit.json").write_text(out[out.index("{"):])
    except (ValueError, KeyError) as e:
        record("audit.cli", "veil security audit", False, f"{e}: {out[:1500]}")

    rc, out = amnesia(vm, "llama-server --version 2>&1 | head -3; ls /usr/local/lib/veil/llama.cpp/bin | head -20")
    record("15.llama_present", "llama.cpp server installed (portable CPU variants + Vulkan backend)",
           rc == 0 and "version" in out, out)

    rc, out = amnesia(vm, "veil tools | head -5; veil tools nmap; for t in nmap tshark tcpdump gdb yara lynis "
                          "aircrack-ng age binwalk john sqlmap strace ltrace socat ngrep; do command -v $t >/dev/null && echo \"$t ok\" "
                          "|| echo \"$t MISSING\"; done; nmap --version | head -1; yara --version")
    record("13.tools_base", "Base security tools present and runnable", "MISSING" not in out and rc == 0, out)

    rc, out = amnesia(vm, "veil lab up t >/dev/null && veil lab target t -- sh -c "
                          "'python3 -m http.server --bind 10.66.0.2 8080 >/dev/null 2>&1 &'; sleep 2; "
                          "veil lab run t -- sh -c 'id -u; nmap -sS -Pn -p 22,8080 10.66.0.2 | grep /tcp; "
                          "/usr/bin/curl -sS -m 5 https://example.com >/dev/null 2>&1 && echo INTERNET || echo NO-INTERNET'; "
                          "veil lab down t", timeout=180)
    record("13.lab_raw_scan", "Offline lab: raw SYN scan of a lab target works, no Internet",
           "8080/tcp open" in out and "NO-INTERNET" in out, out)

    rc, out = amnesia(vm, "nix --version && veil install hello 2>&1 | tail -3; ~/.local/state/nix/profile/bin/hello; "
                          "veil list; ls -d /nix/store/*-hello-* | head -1", timeout=1800)
    record("14.nix_install_over_tor", "Nix installs a package (signed binary cache) as the live user, through Tor",
           "Hello, world!" in out, out)
    rc, out = amnesia(vm, "veil search ghidra")
    record("14.veil_search", "veil search finds bundles offline", "reverse-engineering" in out, out)

    # Agent: offline by default, real local inference with a pulled model.
    code = ("from veil.agent import models\n"
            f"p = models.pull({a.agent_model!r}, consent=lambda *x: True)\n"
            "print('MODEL', p, models.verify(str(p)))")
    rc, out = amnesia(vm, f"python3 -c {shlex.quote(code)}", timeout=3600)
    record("15.model_pull", "Model download over Tor with SHA-256 verification (explicit consent API)",
           "MODEL" in out and "True" in out, out)
    rc, out = amnesia(vm, "mkdir -p ~/ws && cd ~/ws && timeout 1500 veil agent --no-stream --level workspace "
                          f"--workspace ~/ws --model {a.agent_model} --threads 2 --context 4096 "
                          "'Use the write_file tool to create hello.py containing print(6*7). Then use the shell tool "
                          "to run python3 hello.py and report the output.'; echo; echo ---; ls ~/ws; python3 ~/ws/hello.py",
                      timeout=1700)
    record("15.agent_inference", "Veil Agent runs local inference and executes tools (workspace)",
           "42" in out.split("---")[-1], out[-2500:])
    rc, out = amnesia(vm, "cd ~/ws && timeout 900 veil agent --no-stream --level workspace --workspace ~/ws "
                          f"--model {a.agent_model} --threads 2 --context 4096 --mode terminal "
                          "'Run: curl -sS -m 10 https://check.torproject.org/api/ip and show the result.'",
                      timeout=1000)
    record("16.agent_offline", "Agent tool commands have no network unless granted (even with Tor up)",
           "IsTor" not in out, out[-2000:])
    rc, out = amnesia(vm, "cat $XDG_RUNTIME_DIR/veil/agent/sessions/*/execution.jsonl 2>/dev/null | tail -3; "
                          "ls ~/Persistent/.veil 2>&1 | head -2")
    record("17.agent_permissions_log", "Agent sessions are logged in RAM only (no persistence by default)",
           "No such file" in out or "cannot access" in out, out)


@guard("11.apps", "Applications")
def run_app_checks(vm, veil):
    apps = [("tor-browser", "firefox.real|torbrowser"), ("tails-persistent-storage", "tps-frontend|persistent-storage")]
    if veil:
        apps = [("veil-console", "veil.console|veil-console"), ("veil-network-guard", "veil-network-guard"),
                ("veil-agent-gui", "veil-agent-gui"), ("veil-package-center", "veil-package-center")] + apps
    for i, (cmd, pat) in enumerate(apps):
        unit = f"t-app-{i}"
        sh(vm, f"systemd-run --user --unit={unit} {cmd}", user="amnesia", timeout=30)
        ok, out = wait_for(vm, f"pgrep -u amnesia -f '{pat}' | grep -v pgrep >/dev/null", 90)
        time.sleep(25 if cmd == "tor-browser" else 8)
        shot = session_screenshot(vm, f"1{i}-{cmd}") if veil else vm.screenshot(f"1{i}-{cmd}")
        record(f"11.app.{cmd}", f"{cmd} starts in the session", ok, f"screenshot {os.path.basename(shot)}")
        sh(vm, f"systemctl --user stop {unit}; true", user="amnesia", timeout=60)


if __name__ == "__main__":
    sys.exit(main())
