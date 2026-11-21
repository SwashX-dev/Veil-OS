"""Unit tests for the 1.2 privacy features (Privacy Center, VPN before Tor, media, diagnostics).

Run from the repository root:
    python3 -m unittest discover -s tests/veil -v
"""

from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
INC = ROOT / "config" / "chroot_local-includes"
LIB = INC / "usr/local/lib/veil"
sys.path.insert(0, str(INC / "usr/lib/python3/dist-packages"))
os.environ.setdefault("VEIL_SHARE_DIR", str(INC / "usr/share/veil"))

from veil import diagnostics, media, privacy_settings as ps, vpn  # noqa: E402
from veil.netguard import checks  # noqa: E402

KEY_A = base64.b64encode(bytes(range(32))).decode()
KEY_B = base64.b64encode(bytes(range(1, 33))).decode()
PROTON_SAMPLE = f"""[Interface]
# Key for veil-test
# Bouncing = 1
# NetShield = 1
# Moderate NAT = off
# NAT-PMP (Port Forwarding) = off
# VPN Accelerator = on
PrivateKey = {KEY_A}
Address = 10.2.0.2/32, 2a07:b944::2:2/128
DNS = 10.2.0.1

[Peer]
# NL-FREE#1
PublicKey = {KEY_B}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 185.107.56.10:51820
"""


def load_script(name: str):
    """Import an extension-less script from /usr/local/lib/veil as a module."""
    path = LIB / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TempConfigHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)


class TestPrivacySettings(TempConfigHome):
    def test_defaults_when_missing_or_corrupt(self):
        self.assertEqual(ps.load(), ps.DEFAULTS)
        path = ps.settings_path()
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        self.assertEqual(ps.load(), ps.DEFAULTS)
        path.write_text(json.dumps(["a list"]))
        self.assertEqual(ps.load(), ps.DEFAULTS)

    def test_invalid_values_fall_back_individually(self):
        path = ps.settings_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"auto_lock_minutes": -5, "clipboard_clear_seconds": 30,
                                    "tor_browser_security_level": "paranoid", "camera_blocked": "yes"}))
        loaded = ps.load()
        self.assertEqual(loaded["auto_lock_minutes"], ps.DEFAULTS["auto_lock_minutes"])
        self.assertEqual(loaded["clipboard_clear_seconds"], 30)
        self.assertEqual(loaded["tor_browser_security_level"], "standard")
        self.assertIs(loaded["camera_blocked"], False)

    def test_roundtrip_and_permissions(self):
        ps.update("tor_browser_security_level", "safest")
        ps.update("clipboard_clear_seconds", 60)
        self.assertEqual(ps.load()["tor_browser_security_level"], "safest")
        self.assertEqual(ps.load()["clipboard_clear_seconds"], 60)
        self.assertEqual(ps.settings_path().stat().st_mode & 0o777, 0o600)

    def test_cli_parsing(self):
        self.assertIs(ps.parse_cli_value("emergency_shutdown_hotkey", "off"), False)
        self.assertEqual(ps.parse_cli_value("auto_lock_minutes", "15"), 15)
        self.assertEqual(ps.parse_cli_value("video_upscaling", "Enhanced"), "enhanced")
        for key, raw in (("auto_lock_minutes", "x"), ("auto_lock_minutes", "999"), ("clipboard_clear_seconds", "3"),
                         ("camera_blocked", "maybe"), ("nope", "1"), ("tor_browser_security_level", "max")):
            with self.assertRaises(ValueError, msg=(key, raw)):
                ps.parse_cli_value(key, raw)

    def test_tor_browser_user_js_uses_tor_browsers_own_levels(self):
        self.assertIn('"browser.security_level.security_slider", 1)', ps.tor_browser_user_js("safest"))
        self.assertIn('"browser.security_level.security_slider", 2)', ps.tor_browser_user_js("safer"))
        # Nothing else: custom prefs would make Veil users fingerprintable.
        prefs = [line for line in ps.tor_browser_user_js("safer").splitlines() if line.startswith("user_pref")]
        self.assertEqual(len(prefs), 2)


class TestTorBrowserConfigScript(TempConfigHome):
    def test_writes_and_removes_user_js(self):
        script = load_script("veil-tor-browser-config")
        profile = Path(self.tmp.name) / "profile"
        profile.mkdir()
        (profile / "user.js").write_text('user_pref("other.pref", true);\n')
        ps.update("tor_browser_security_level", "safest")
        self.assertEqual(script.main([str(profile)]), 0)
        text = (profile / "user.js").read_text()
        self.assertIn('"other.pref", true', text)
        self.assertEqual(text.count("security_slider"), 1)
        self.assertEqual(script.main([str(profile)]), 0)  # idempotent
        self.assertEqual((profile / "user.js").read_text().count("security_slider"), 1)
        ps.update("tor_browser_security_level", "standard")
        script.main([str(profile)])
        self.assertNotIn("security_slider", (profile / "user.js").read_text())


class TestWireGuardParsing(unittest.TestCase):
    def test_proton_config(self):
        cfg = vpn.parse_config(PROTON_SAMPLE)
        self.assertEqual(cfg.address, "10.2.0.2/32")
        self.assertEqual((cfg.endpoint_ip, cfg.endpoint_port), ("185.107.56.10", 51820))
        self.assertEqual(cfg.mtu, 1420)
        self.assertTrue(any("DNS" in note for note in cfg.ignored))
        self.assertTrue(any("IPv6" in note for note in cfg.ignored))
        conf = cfg.setconf()
        self.assertIn("FwMark = 0x5e11", conf)
        self.assertIn("AllowedIPs = 0.0.0.0/0\n", conf)
        for forbidden in ("Address", "DNS", "::/0"):
            self.assertNotIn(forbidden, conf)
        self.assertNotIn(KEY_A, json.dumps(cfg.summary()))

    def test_rejections(self):
        bad = {
            "hostname endpoint": PROTON_SAMPLE.replace("185.107.56.10", "nl-free-1.protonvpn.net"),
            "ipv6 endpoint": PROTON_SAMPLE.replace("185.107.56.10:51820", "[2a07:b944::1]:51820"),
            "hook": PROTON_SAMPLE.replace("DNS = 10.2.0.1", "PostUp = curl http://example.com"),
            "bad key": PROTON_SAMPLE.replace(KEY_B, "not-a-key"),
            "short key": PROTON_SAMPLE.replace(KEY_B, base64.b64encode(b"x" * 16).decode()),
            "two peers": PROTON_SAMPLE + f"\n[Peer]\nPublicKey = {KEY_A}\nEndpoint = 1.2.3.4:1\n",
            "partial routes": PROTON_SAMPLE.replace("AllowedIPs = 0.0.0.0/0, ::/0", "AllowedIPs = 10.0.0.0/8"),
            "no ipv4 address": PROTON_SAMPLE.replace("10.2.0.2/32, ", ""),
            "loopback endpoint": PROTON_SAMPLE.replace("185.107.56.10", "127.0.0.1"),
            "mtu": PROTON_SAMPLE.replace("DNS = 10.2.0.1", "MTU = 9000"),
            "unknown key": PROTON_SAMPLE.replace("DNS = 10.2.0.1", "Table = off"),
            "missing endpoint": PROTON_SAMPLE.replace("Endpoint = 185.107.56.10:51820", ""),
            "huge": PROTON_SAMPLE + "#" * 20000,
        }
        for what, text in bad.items():
            with self.assertRaises(vpn.ConfigError, msg=what):
                vpn.parse_config(text)

    def test_import_is_private(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            src = Path(tmp, "nl-free-1.conf")
            src.write_text(PROTON_SAMPLE)
            name, _cfg = vpn.import_config(src)
            self.assertEqual(name, "nl-free-1")
            self.assertEqual(vpn.list_configs(), ["nl-free-1"])
            self.assertEqual((vpn.config_dir() / "nl-free-1.conf").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(vpn.ConfigError):
                vpn.import_config(src, "../escape")


class TestVpnHelperRules(unittest.TestCase):
    def test_chain_is_fail_closed(self):
        helper = load_script("veil-vpn-helper")
        cfg = vpn.parse_config(PROTON_SAMPLE)
        rules = [" ".join(r) for r in helper.chain_rules(cfg)]
        accepts = [r for r in rules if r.endswith("-j ACCEPT")]
        marked = "-p udp -d 185.107.56.10/32 --dport 51820 -m mark --mark 0x5e11 -j ACCEPT"
        self.assertEqual(accepts, [marked])
        tor_reject = rules.index("-m owner --uid-owner debian-tor -j REJECT --reject-with icmp-admin-prohibited")
        # WireGuard's outer packets inherit Tor's socket owner: the marked accept must precede the owner reject.
        self.assertLess(rules.index(marked), tor_reject)
        self.assertLess(rules.index("-o wg-veil -m owner --uid-owner debian-tor -j RETURN"), tor_reject)
        lines = helper.session_lines(cfg)
        self.assertEqual(lines[0], "-N VEIL_VPN")
        self.assertEqual(lines[-1], "-I OUTPUT 1 -j VEIL_VPN")

    def test_setconf_without_keepalive(self):
        cfg = vpn.parse_config(PROTON_SAMPLE)
        self.assertNotIn("PersistentKeepalive", cfg.setconf(keepalive=False))
        self.assertIn("PersistentKeepalive = 25", cfg.setconf())

    def test_session_rules_keep_other_lines(self):
        helper = load_script("veil-vpn-helper")
        with tempfile.TemporaryDirectory() as tmp:
            helper.SESSION_RULES = Path(tmp, "session-rules")
            helper.SESSION_RULES.write_text("-A OUTPUT -o veth-x -j ACCEPT\n")
            helper.write_session_rules(helper.session_lines(vpn.parse_config(PROTON_SAMPLE)))
            text = helper.SESSION_RULES.read_text()
            self.assertIn("-A OUTPUT -o veth-x -j ACCEPT", text)
            self.assertIn("-I OUTPUT 1 -j VEIL_VPN", text)
            helper.write_session_rules([])
            self.assertEqual(helper.SESSION_RULES.read_text(), "-A OUTPUT -o veth-x -j ACCEPT\n")


GOOD_SAVE = """*filter
:INPUT DROP [0:0]
:FORWARD DROP [0:0]
:OUTPUT DROP [0:0]
:VEIL_VPN - [0:0]
-A OUTPUT -j VEIL_VPN
-A OUTPUT -m state --state ESTABLISHED -j ACCEPT
-A OUTPUT -j log_reject
-A VEIL_VPN -o lo -j RETURN
-A VEIL_VPN -o wg-veil -m owner --uid-owner 114 -j RETURN
-A VEIL_VPN -o wg-veil -j REJECT --reject-with icmp-admin-prohibited
-A VEIL_VPN -m owner --uid-owner 114 -j REJECT --reject-with icmp-admin-prohibited
-A VEIL_VPN -d 185.107.56.10/32 -p udp -m udp --dport 51820 -m mark --mark 0x5e11 -j ACCEPT
-A VEIL_VPN -j RETURN
COMMIT
"""
STATE = {"active": True, "endpoint": "185.107.56.10:51820", "tor_routed_through_vpn": True,
         "firewall": True, "blackhole_fallback": True}


class TestNetworkGuardVpn(unittest.TestCase):
    names = {"114": "debian-tor"}

    def verdict(self, text, state):
        return checks.evaluate_vpn(checks.parse_iptables_save(text), self.names, state)[0].status

    def test_off(self):
        self.assertEqual(self.verdict(GOOD_SAVE.replace("-A OUTPUT -j VEIL_VPN\n", "").replace(
            "".join(line + "\n" for line in GOOD_SAVE.splitlines() if line.startswith("-A VEIL_VPN")), ""), None),
            "info")

    def test_good(self):
        self.assertEqual(self.verdict(GOOD_SAVE, STATE), "pass")

    def test_failures(self):
        self.assertEqual(self.verdict(GOOD_SAVE.replace("-A VEIL_VPN -m owner --uid-owner 114 -j REJECT "
                                                        "--reject-with icmp-admin-prohibited\n", ""), STATE), "fail")
        self.assertEqual(self.verdict(GOOD_SAVE.replace("-A VEIL_VPN -j RETURN", "-A VEIL_VPN -j ACCEPT"), STATE),
                         "fail")
        self.assertEqual(self.verdict(GOOD_SAVE.replace("-A OUTPUT -j VEIL_VPN\n-A OUTPUT -m state",
                                                        "-A OUTPUT -m state"), STATE), "fail")
        self.assertEqual(self.verdict(GOOD_SAVE, {**STATE, "blackhole_fallback": False}), "fail")
        self.assertEqual(self.verdict(GOOD_SAVE, {**STATE, "tor_routed_through_vpn": False}), "fail")
        self.assertEqual(self.verdict(GOOD_SAVE, {"active": False}), "fail")


class TestMedia(unittest.TestCase):
    def test_presets(self):
        self.assertEqual(media.mpv_options("off", env={}), [])
        self.assertEqual(media.mpv_options("quality", env={}), ["--profile=high-quality"])
        self.assertIn(f"--glsl-shaders={media.SHADER}", media.mpv_options("enhanced", env={}))
        safe = media.mpv_options("off", env={"VEIL_SAFE_GRAPHICS": "nouveau"})
        self.assertEqual(safe, ["--gpu-api=opengl", "--gpu-sw"])  # mpv rejects llvmpipe without --gpu-sw
        with self.assertRaises(ValueError):
            media.mpv_options("ultra", env={})

    def test_shader_is_vendored(self):
        shader = INC / media.SHADER.lstrip("/")
        self.assertTrue(shader.is_file())
        self.assertIn("//!HOOK LUMA", shader.read_text())

    def test_player_sandbox_has_no_network_and_no_gpu_in_safe_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp, "a.mkv")
            video.write_bytes(b"\0")
            env = {"XDG_RUNTIME_DIR": tmp, "WAYLAND_DISPLAY": "wayland-9", "VEIL_SAFE_GRAPHICS": "nouveau",
                   "LIBGL_ALWAYS_SOFTWARE": "1"}
            spec = media.player_spec([video], env=env)
            self.assertFalse(spec.network)
            self.assertEqual(spec.devices, [])
            self.assertEqual(spec.extra_env["LIBGL_ALWAYS_SOFTWARE"], "1")
            self.assertIn(str(video.resolve()), spec.readonly)
            # Only the render node, never the display (card*) device.
            self.assertEqual(media.player_spec([video], env={"XDG_RUNTIME_DIR": tmp}).devices, ["/dev/dri/renderD*"])
            argv = media.play_argv([video], "quality", env={})
            self.assertIn("--ytdl=no", argv)
            self.assertEqual(argv[-1], str(video.resolve()))

    def test_upscale_argv(self):
        argv = media.upscale_argv(Path("/tmp/in.mp4"), Path("/tmp/out.mkv"), 1080)
        self.assertIn("scale=-2:1080:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv420p", argv)
        enhanced = media.upscale_argv(Path("/tmp/in.mp4"), Path("/tmp/out.mkv"), 2160, enhanced=True)
        self.assertTrue(any(media.SHADER in a and "libplacebo" in a for a in enhanced))


class TestDiagnostics(unittest.TestCase):
    def test_gpu_inventory_from_fake_sysfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            drm = Path(tmp, "drm")
            pci = Path(tmp, "pci/0000:01:00.0")
            (pci / "drv").mkdir(parents=True)
            Path(tmp, "nouveau").mkdir()
            (pci / "vendor").write_text("0x10de\n")
            (pci / "device").write_text("0x28e1\n")
            (pci / "boot_vga").write_text("1\n")
            os.symlink(Path(tmp, "nouveau"), pci / "driver")
            (drm / "card1").mkdir(parents=True)
            os.symlink(pci, drm / "card1/device")
            (drm / "card1-eDP-1").mkdir()
            (drm / "card1-eDP-1/status").write_text("connected\n")
            inv = diagnostics.gpu_inventory(drm)
            self.assertEqual(inv, [{"card": "card1", "vendor": "0x10de", "device": "0x28e1", "driver": "nouveau",
                                    "boot_vga": "1", "connectors": ["eDP-1=connected"]}])

    def test_graphics_mode(self):
        self.assertIn("safe graphics: nouveau", diagnostics.graphics_mode({"VEIL_SAFE_GRAPHICS": "nouveau"}))
        self.assertEqual(diagnostics.graphics_mode({}), "GPU rendering")

    def test_helper_redacts_identifiers(self):
        helper = load_script("veil-diag-helper")
        line = helper.redact("usb 1-2: SerialNumber: 070A894DB74B6306 eth0 aa:bb:cc:dd:ee:ff 192.168.1.20 "
                             "127.0.0.1 nouveau 0000:01:00.0: gsp: rc engn:00000001")
        self.assertNotIn("070A894DB74B6306", line)
        self.assertNotIn("aa:bb:cc:dd:ee:ff", line)
        self.assertNotIn("192.168.1.20", line)
        self.assertIn("127.0.0.1", line)
        self.assertIn("0000:01:00.0", line)


class TestSafeGraphicsDetection(unittest.TestCase):
    """Run veil-session's real detection code against fake /sys/class/drm trees."""

    SESSION = INC / "usr/local/bin/veil-session"

    def decide(self, driver: str | None, cmdline: str) -> str:
        import re
        import shutil
        import subprocess
        if not shutil.which("unshare"):
            self.skipTest("unshare not available")
        text = self.SESSION.read_text()
        fn = re.search(r"^nouveau_in_use\(\) \{.*?^\}", text, re.S | re.M).group(0)
        block = re.search(r'^cmdline=" \$\(cat /proc/cmdline\) "\n.*?^esac$', text, re.S | re.M).group(0)
        block = block.replace("$(cat /proc/cmdline)", "$CMD")
        with tempfile.TemporaryDirectory() as tmp:
            drm = Path(tmp, "drm")
            (drm / "card1/device").mkdir(parents=True)
            if driver:
                (Path(tmp, "drivers") / driver).mkdir(parents=True)
                os.symlink(Path(tmp, "drivers") / driver, drm / "card1/device/driver")
            script = f"mount --bind {drm} /sys/class/drm && CMD='{cmdline}' && {fn}\n{block}\necho \"${{VEIL_SAFE_GRAPHICS:-}}\""
            r = subprocess.run(["unshare", "-rm", "sh", "-c", script], capture_output=True, text=True, check=False)
        if r.returncode != 0:
            self.skipTest(f"cannot create a mount namespace here: {r.stderr.strip()[:100]}")
        return r.stdout.strip()

    def test_decisions(self):
        self.assertEqual(self.decide("nouveau", "quiet"), "nouveau")
        self.assertEqual(self.decide("amdgpu", "quiet"), "")
        self.assertEqual(self.decide("nvidia", "quiet"), "")
        self.assertEqual(self.decide(None, "quiet"), "")
        self.assertEqual(self.decide("nouveau", "quiet veil.gpu-rendering"), "")
        self.assertEqual(self.decide("amdgpu", "quiet nomodeset"), "nomodeset")
        self.assertEqual(self.decide("nouveau", "nomodeset"), "nomodeset")


class TestDeviceGuard(unittest.TestCase):
    def test_rules(self):
        guard = load_script("veil-device-guard")
        with tempfile.TemporaryDirectory() as tmp:
            guard.RULES = Path(tmp, "71-veil-device-guard.rules")
            self.assertEqual(guard.blocked_classes(), [])
            guard.write_rules(["microphone", "camera"])
            self.assertEqual(sorted(guard.blocked_classes()), ["camera", "microphone"])
            text = guard.RULES.read_text()
            self.assertIn('SUBSYSTEM=="video4linux", MODE="0000", TAG-="uaccess"', text)
            self.assertIn('KERNEL=="pcmC[0-9]*D[0-9]*c"', text)  # capture devices only, not playback
            guard.write_rules([])
            self.assertFalse(guard.RULES.exists())


if __name__ == "__main__":
    unittest.main()
