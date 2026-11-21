#!/usr/bin/env python3
"""VM tests for the Privacy Center features, Veil Player and diagnostics.

    privacy_test.py --img IMG --out DIR [--overlay-base COMMIT --debs A.deb B.deb --sudoers FILE]
                    [--safe-graphics] [--no-network]

Covers: privacy daemon, clipboard clearing, idle lock (with and without a
password), camera and microphone kill switches, Tor Browser security level,
video upscaling (real time and offline), diagnostics, Network Guard, TCP
timestamps, and finally the emergency shutdown hotkey (which ends the VM).
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import overlay  # noqa: E402
import veilvm  # noqa: E402
from run_tests import RESULTS, SESSION_ENV, connect_tor, record, session_screenshot, sh, wait_for  # noqa: E402

PASSWORD = "veiltestonly"  # exists only in the disposable VM's RAM
VEIL = "/usr/local/bin/veil"


def user(vm, cmd, timeout=300):
    return sh(vm, SESSION_ENV + cmd, user="amnesia", timeout=timeout)


def setting(vm, key, value):
    rc, out = user(vm, f"{VEIL} privacy settings set {key} {value}")
    if rc:
        raise RuntimeError(out)


def type_password(vm):
    for char in PASSWORD:
        vm.qmp.cmd("send-key", keys=[{"type": "qcode", "data": char}], **{"hold-time": 60})
        time.sleep(0.09)
    vm.qmp.cmd("send-key", keys=[{"type": "qcode", "data": "ret"}])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--overlay-base")
    ap.add_argument("--debs", nargs="*", default=[])
    ap.add_argument("--sudoers", nargs="*", default=[])
    ap.add_argument("--safe-graphics", action="store_true", help="boot like the Safe Graphics entry")
    ap.add_argument("--no-network", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[2]

    kernel, initrd, cmdline = veilvm.boot_files(a.img, None, str(out / "boot"))
    if a.safe_graphics:
        cmdline += " nomodeset veil.software-rendering"
    vm = veilvm.VM(a.img, str(out / "vm"), memory="6144", network=not a.no_network,
                   kernel=kernel, initrd=initrd, cmdline=cmdline + " autotest_never_use_this_option")
    base_argv = vm.argv
    vm.argv = lambda: base_argv() + ["-audiodev", "none,id=snd0", "-device", "intel-hda",
                                     "-device", "hda-duplex,audiodev=snd0"]
    vm.start()
    powered_off = False
    try:
        vm.rs.wait_up(300)
        vm.rs.signal_ready()
        if a.overlay_base:
            log = overlay.apply(vm.rs, repo, a.overlay_base, [Path(d) for d in a.debs], [Path(s) for s in a.sudoers])
            (out / "overlay.log").write_text("\n".join(log) + "\n")
        veilvm.login_via_greeter(vm.rs)
        ok, _ = wait_for(vm, "pgrep -u amnesia -x xfce4-panel", 120)
        record("privacy.session", "Desktop session ready", ok, "xfce4-panel running" if ok else "no panel")

        # Graphics mode
        rc, env = sh(vm, "tr '\\0' '\\n' < /proc/$(pgrep -u amnesia -x labwc)/environ | "
                         "grep -E '^(VEIL_SAFE_GRAPHICS|WLR_RENDERER|LIBGL_ALWAYS_SOFTWARE|GSK_RENDERER|VK_LOADER)' | sort")
        if a.safe_graphics:
            rc2, drv = sh(vm, "for d in /sys/class/drm/card[0-9]*/device/driver; do basename $(readlink -f $d); done")
            ok = "VEIL_SAFE_GRAPHICS=nomodeset" in env and "WLR_RENDERER=pixman" in env and \
                "LIBGL_ALWAYS_SOFTWARE=1" in env and "virtio" not in drv
            record("graphics.safe_mode", "Safe Graphics (nomodeset): firmware framebuffer + software rendering", ok,
                   f"{env.strip()} | drivers: {drv.strip()}")
            session_screenshot(vm, "safe-graphics-desktop")
        else:
            record("graphics.default_mode", "Non-NVIDIA GPU keeps GPU rendering (no safe-graphics variables)",
                   "VEIL_SAFE_GRAPHICS" not in env, env.strip() or "(none set)")

        # Privacy daemon and settings
        ok, _ = wait_for(vm, "pgrep -u amnesia -f veil-privacy-daemon", 60)
        rc, js = user(vm, f"{VEIL} privacy settings --json")
        record("privacy.daemon", "Privacy daemon running; settings readable", ok and rc == 0, js)

        # TCP timestamps
        rc, val = sh(vm, "sysctl -n net.ipv4.tcp_timestamps")
        record("privacy.tcp_timestamps", "TCP timestamps disabled (no clock-skew fingerprint)", val.strip() == "0",
               val)

        # Clipboard clearing
        setting(vm, "clipboard_clear_seconds", 5)
        time.sleep(3)
        # wl-copy serves the selection from a background process: run it as a
        # user unit, because the test shell's login session is closed (and its
        # processes killed) when each command returns.
        user(vm, "systemd-run --user --collect --quiet wl-copy --foreground veil-secret-123")
        time.sleep(1)
        rc, first = user(vm, "wl-paste --no-newline")
        ok, _ = wait_for(vm, SESSION_ENV + "! wl-paste --no-newline 2>/dev/null | grep -q veil-secret-123", 20,
                         user="amnesia", interval=1)
        record("privacy.clipboard_clear", "Clipboard is cleared 5 s after a copy",
               first.strip() == "veil-secret-123" and ok, f"before={first.strip()!r} cleared={ok}")
        setting(vm, "clipboard_clear_seconds", 0)

        # Idle lock without a password: must not lock (no way to unlock)
        setting(vm, "auto_lock_minutes", 1)
        time.sleep(80)
        rc_lock, _ = sh(vm, "pgrep -u amnesia -x swaylock")
        rc_flag, _ = sh(vm, "ls /run/user/1000/veil-autolock-no-password")
        record("privacy.autolock_no_password", "Idle lock never locks a session without a password",
               rc_lock != 0 and rc_flag == 0, f"swaylock running={rc_lock == 0} notice flag={rc_flag == 0}")

        # Idle lock with a password
        sh(vm, f"printf '%s\\n' {shlex.quote('amnesia:' + PASSWORD)} | chpasswd")
        setting(vm, "auto_lock_minutes", 2)
        time.sleep(2)
        setting(vm, "auto_lock_minutes", 1)
        ok, _ = wait_for(vm, "pgrep -u amnesia -x swaylock", 120, interval=2)
        rc_m, _ = sh(vm, "ls /run/user/1000/veil-session.*/locked")
        session_screenshot(vm, "autolock")
        type_password(vm)
        unlocked, _ = wait_for(vm, "! pgrep -u amnesia -x swaylock", 30, interval=1)
        record("privacy.autolock", "Idle lock after 1 minute when a password is set; unlock works",
               ok and unlocked, f"locked={ok} supervisor marker={rc_m == 0} unlocked={unlocked}")
        setting(vm, "auto_lock_minutes", 0)

        # Camera kill switch (vivid: a virtual camera driver)
        sh(vm, "modprobe vivid n_devs=1 node_types=0x1")
        wait_for(vm, "test -e /dev/video0", 30)
        rc_before, _ = user(vm, "test -r /dev/video0")
        rc_block, out_block = user(vm, "sudo -n /usr/local/lib/veil/veil-device-guard block camera")
        time.sleep(2)
        rc_after, _ = user(vm, "test -r /dev/video0")
        rc_status, status = user(vm, "sudo -n /usr/local/lib/veil/veil-device-guard status")
        rc_allow, _ = user(vm, "sudo -n /usr/local/lib/veil/veil-device-guard allow camera")
        sh(vm, "/usr/local/lib/veil/veil-device-guard allow camera")
        time.sleep(2)
        rc_again, _ = user(vm, "test -r /dev/video0")
        record("privacy.camera_block", "Camera kill switch: block without password, unblock needs authorization",
               rc_before == 0 and rc_block == 0 and rc_after != 0 and rc_allow != 0 and rc_again == 0,
               f"readable before={rc_before == 0} block rc={rc_block} after={rc_after == 0} "
               f"passwordless allow refused={rc_allow != 0} after root allow={rc_again == 0} {status[:200]}")

        # Microphone kill switch (QEMU HDA duplex codec)
        rc_dev, dev = sh(vm, "ls /dev/snd/pcmC*c | head -1")
        dev = dev.strip()
        if rc_dev == 0 and dev:
            rc_before, _ = user(vm, f"test -r {dev}")
            rc_block, _ = user(vm, "sudo -n /usr/local/lib/veil/veil-device-guard block microphone")
            time.sleep(2)
            rc_after, _ = user(vm, f"test -r {dev}")
            rc_play, play = sh(vm, "ls -l /dev/snd/ | grep pcm")
            sh(vm, "/usr/local/lib/veil/veil-device-guard allow microphone")
            time.sleep(2)
            rc_again, _ = user(vm, f"test -r {dev}")
            record("privacy.microphone_block", "Microphone kill switch blocks capture devices only",
                   rc_before == 0 and rc_block == 0 and rc_after != 0 and rc_again == 0,
                   f"{dev}: before={rc_before == 0} after={rc_after == 0} restored={rc_again == 0}\n{play}")
        else:
            record("privacy.microphone_block", "Microphone kill switch", None, "no capture device in the VM")

        # Policy for privileged actions
        rc, pol = sh(vm, "pkaction --verbose --action-id org.veil.vpn.manage; "
                         "pkaction --verbose --action-id org.veil.device-guard")
        record("privacy.polkit", "VPN and device unblocking require authentication (auth_self/auth_admin)",
               rc == 0 and "implicit active:   auth_self" in pol and "yes" not in pol.split("implicit active:")[1][:20],
               pol)

        # Diagnostics
        rc, rep = user(vm, f"{VEIL} diagnostics")
        rc_s, saved = user(vm, f"{VEIL} diagnostics --save")
        ok = rc == 0 and "== Graphics devices ==" in rep and "== Kernel" in rep and rc_s == 0
        record("diagnostics.report", "veil diagnostics: graphics, session and kernel sections; saved to a file",
               ok, rep[:1500] + "\n" + saved)
        (out / "diagnostics-sample.txt").write_text(rep)

        # Media: offline upscaling and real-time presets
        rc, gen = user(vm, "ffmpeg -nostdin -loglevel error -y -f lavfi -i testsrc2=size=1280x720:rate=30 -t 2 "
                           "-pix_fmt yuv420p -c:v libx264 ~/v720.mp4")
        rc, up = user(vm, f"{VEIL} media upscale ~/v720.mp4 --to 1080p", timeout=600)
        rc_p, dims = user(vm, "ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "
                              "~/v720-1080p.mkv")
        record("media.upscale_offline", "Offline upscale 720p → 1080p (sandboxed ffmpeg, Lanczos)",
               rc == 0 and dims.strip() == "1920,1080", f"{up.strip()} dims={dims.strip()}")
        rc, up = user(vm, f"{VEIL} media upscale ~/v720.mp4 --to 1440p --enhanced -o ~/v720-1440p-enhanced.mkv",
                      timeout=1200)
        rc_p, dims = user(vm, "ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "
                              "~/v720-1440p-enhanced.mkv")
        record("media.upscale_enhanced", "Offline neural upscale (FSRCNNX via libplacebo, software Vulkan)",
               rc == 0 and dims.strip() == "2560,1440", f"{up.strip()[-400:]} dims={dims.strip()}")
        results = {}
        for mode in ("off", "quality", "enhanced"):
            rc, log = user(vm, f"timeout 180 veil-player --upscale {mode} ~/v720.mp4 --frames=45 "
                               "--msg-level=all=v --really-quiet=no 2>&1 | tail -400", timeout=240)
            results[mode] = (rc, log)
        enh = results["enhanced"][1]
        ok = all(r[0] == 0 for r in results.values()) and "FSRCNNX" in enh
        record("media.player_presets", "Veil Player plays with off/quality/enhanced presets in the no-network sandbox",
               ok, " | ".join(f"{m}: rc={r[0]}" for m, r in results.items()) + "\n" +
               "\n".join(line for line in enh.splitlines() if "shader" in line.lower() or "scale" in line.lower())[:1500])
        rc, sandbox_net = user(vm, "timeout 60 veil-player --upscale off ~/v720.mp4 --frames=1 "
                                   "--script-opts=x=1 --msg-level=all=error 2>&1; "
                                   "python3 -c \"from veil import media; from pathlib import Path; "
                                   "s=media.player_spec([Path('/home/amnesia/v720.mp4')]); print(s.network, s.devices)\"")
        record("media.player_sandbox", "Player sandbox has no network", "False" in sandbox_net.splitlines()[-1],
               sandbox_net.strip()[-200:])
        # Network Guard with the new VPN check (VPN off)
        if not a.no_network:
            connect_tor(vm)
        rc, ng = user(vm, f"{VEIL} privacy status --json --no-probes", timeout=180)
        try:
            data = json.loads(ng[ng.index("{"):])
            failing = [c["id"] for c in data.get("checks", []) if c["status"] == "fail"]
            vpn_check = [c for c in data.get("checks", []) if c["id"] == "vpn.before_tor"]
            ok = not failing and vpn_check and vpn_check[0]["status"] == "info"
            record("netguard.vpn_off", "Network Guard: no failures; VPN check reports 'off'", ok,
                   f"failing={failing} vpn={vpn_check[0]['detail'] if vpn_check else None}")
        except (ValueError, KeyError) as exc:
            record("netguard.vpn_off", "Network Guard", False, f"{exc}: {ng[:500]}")

        # Tor Browser security level (one of Tor Browser's own levels)
        if not a.no_network:
            setting(vm, "tor_browser_security_level", "safest")
            vm.rs.spawn(SESSION_ENV + "TOR_BROWSER_SKIP_OFFLINE_WARNING=yes tor-browser", user="amnesia")
            ok, _ = wait_for(vm, "pgrep -u amnesia -f 'firefox' >/dev/null", 120, interval=3)
            time.sleep(40)
            session_screenshot(vm, "tor-browser-safest")
            rc, ujs = sh(vm, "cat /home/amnesia/.tor-browser/profile.default/user.js")
            sh(vm, "pkill -TERM -u amnesia -f 'firefox' ; sleep 15")
            rc, prefs = sh(vm, "grep -E 'security_level|svg.disabled|javascript.options.ion\"' "
                               "/home/amnesia/.tor-browser/profile.default/prefs.js")
            record("torbrowser.security_level", "Tor Browser starts at the chosen security level (Safest)",
                   ok and '"browser.security_level.security_slider", 1' in ujs and
                   '"browser.security_level.security_slider", 1' in prefs,
                   f"user.js:\n{ujs}\nprefs.js:\n{prefs}")

        # Emergency shutdown hotkey (ends the VM)
        shots = []
        vm.qmp.cmd("send-key", keys=[{"type": "qcode", "data": k} for k in ("ctrl", "alt", "shift", "delete")])
        t0 = time.time()
        while time.time() - t0 < 90 and vm.proc.poll() is None:
            try:
                if len(shots) < 12:
                    shots.append(vm.screenshot(f"emergency-{len(shots):02d}"))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        try:
            rc = vm.proc.wait(timeout=60)
            powered_off = True
        except Exception:  # noqa: BLE001
            rc = None
        record("privacy.emergency_hotkey", "Ctrl+Alt+Shift+Delete shuts the computer down",
               powered_off, f"powered off after {time.time() - t0:.1f}s (rc={rc}); screenshots {len(shots)}")
    finally:
        if not powered_off:
            vm.stop(60)
        (out / "results.json").write_text(json.dumps(RESULTS, indent=2))
        summary = {s: sum(1 for r in RESULTS if r["status"] == s) for s in ("PASS", "FAIL", "UNTESTED")}
        print("SUMMARY", summary, flush=True)
    return 0 if not any(r["status"] == "FAIL" for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
