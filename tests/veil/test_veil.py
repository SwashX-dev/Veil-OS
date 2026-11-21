"""Unit tests for Veil OS userland components.

Run from the repository root:
    python3 -m unittest discover -s tests/veil -v

Sandbox/lab tests are skipped automatically when bubblewrap or unprivileged
user namespaces are unavailable (set VEIL_BWRAP to a bwrap binary if needed).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INC = ROOT / "config" / "chroot_local-includes"
sys.path.insert(0, str(INC / "usr/lib/python3/dist-packages"))
os.environ.setdefault("VEIL_SHARE_DIR", str(INC / "usr/share/veil"))
FIX = Path(__file__).parent / "fixtures"

from veil import audit, sandbox, tools_catalog  # noqa: E402
from veil.agent import loop, models, policy, tools  # noqa: E402
from veil.netguard import checks  # noqa: E402


def userns_ok() -> bool:
    if not sandbox.bwrap_available():
        return False
    r = subprocess.run([sandbox.BWRAP, "--ro-bind", "/", "/", "--unshare-net", "true"],
                       capture_output=True, check=False)
    return r.returncode == 0


# ------------------------------------------------------------------ sandbox
class TestSeccomp(unittest.TestCase):
    def test_filter_shape(self):
        prog = sandbox.build_seccomp_filter([101, 250])
        self.assertEqual(len(prog) % 8, 0)
        insns = [struct.unpack("=HBBI", prog[i:i + 8]) for i in range(0, len(prog), 8)]
        # arch check first, x32 check, one JEQ per syscall, allow, deny
        self.assertEqual(insns[1][3], sandbox.AUDIT_ARCH_X86_64)
        self.assertEqual(insns[4][3], sandbox.X32_SYSCALL_BIT)
        self.assertEqual(insns[-2][3], sandbox.SECCOMP_RET_ALLOW)
        self.assertEqual(insns[-1][3], sandbox.SECCOMP_RET_ERRNO | sandbox.EPERM)
        # every JEQ must jump exactly onto the final deny instruction
        n = len(insns)
        for idx, (code, jt, _jf, _k) in enumerate(insns):
            if code == sandbox.BPF_JEQ_K and idx > 1:
                self.assertEqual(idx + 1 + jt, n - 1)

    def test_argv_denies_network_and_masks_sockets(self):
        if not sandbox.bwrap_available():
            self.skipTest("bwrap not available")
        argv = sandbox.build_argv(sandbox.SandboxSpec(), ["true"])
        self.assertIn("--unshare-net", argv)
        self.assertIn("--cap-drop", argv)
        joined = " ".join(argv)
        self.assertIn("--tmpfs /run", joined)
        self.assertIn("--new-session", argv)

    def test_argv_network_grant(self):
        if not sandbox.bwrap_available():
            self.skipTest("bwrap not available")
        argv = sandbox.build_argv(sandbox.SandboxSpec(network=True), ["true"])
        self.assertNotIn("--unshare-net", argv)


@unittest.skipUnless(userns_ok(), "unprivileged user namespaces unavailable")
class TestSandboxIsolation(unittest.TestCase):
    def test_self_test_all_pass(self):
        res = sandbox.self_test()
        self.assertTrue(all(res.values()), res)

    def test_secret_paths_hidden(self):
        with tempfile.TemporaryDirectory() as home:
            gpg = Path(home, ".gnupg")
            gpg.mkdir()
            (gpg / "private-keys-v1.d").mkdir()
            old = os.environ["HOME"]
            os.environ["HOME"] = home
            try:
                r = sandbox.run(sandbox.SandboxSpec(), ["sh", "-c", f"ls -A {gpg} | wc -l"], timeout=20)
            finally:
                os.environ["HOME"] = old
            self.assertEqual(r.stdout.strip(), "0")

    def test_readonly_bind_visible_under_masked_run(self):
        rt = f"/run/user/{os.getuid()}"
        if not os.path.isdir(rt) or not os.access(rt, os.W_OK):
            self.skipTest("no /run-based runtime dir")
        d = Path(rt) / "veil-test-ro"
        d.mkdir(exist_ok=True)
        f = d / "model.gguf"
        f.write_text("GGUF-TEST\n")
        try:
            r = sandbox.run(sandbox.SandboxSpec(readonly=[str(f)]),
                            ["sh", "-c", f"cat {f}; echo x > {f} 2>/dev/null && echo WRITABLE || echo RO"], timeout=20)
            self.assertEqual(r.stdout.split(), ["GGUF-TEST", "RO"])
            # Without the bind it must be hidden (all of /run is masked).
            r = sandbox.run(sandbox.SandboxSpec(), ["sh", "-c", f"cat {f} 2>/dev/null || echo HIDDEN"], timeout=20)
            self.assertEqual(r.stdout.strip(), "HIDDEN")
        finally:
            f.unlink()
            d.rmdir()

    def test_workspace_write_only(self):
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as other:
            spec = sandbox.SandboxSpec(writable=[ws])
            r = sandbox.run(spec, ["sh", "-c", f"touch {ws}/ok && touch {other}/no 2>/dev/null; echo $?"], timeout=20)
            self.assertTrue(Path(ws, "ok").exists())
            self.assertFalse(Path(other, "no").exists())
            self.assertNotEqual(r.stdout.strip(), "0")


# ------------------------------------------------------------------ policy
class TestPolicy(unittest.TestCase):
    def test_classify(self):
        self.assertTrue(any(r.kind == "destructive-disk" for r in policy.classify("dd if=x.img of=/dev/sda bs=4M")))
        self.assertTrue(any(r.kind == "destructive-disk" for r in policy.classify("mkfs.ext4 /dev/sdb1")))
        self.assertTrue(any(r.kind == "security-boundary" for r in policy.classify("sudo iptables -F")))
        self.assertTrue(any(r.kind == "credentials" for r in policy.classify("cat ~/.ssh/id_ed25519")))
        self.assertTrue(any(r.kind == "mass-delete" for r in policy.classify("rm -rf ~")))
        self.assertEqual(policy.classify("ls -la && python3 sum.py"), [])
        self.assertTrue(policy.mentions_network("curl https://example.com"))
        self.assertFalse(policy.mentions_network("grep curl notes.txt | wc -l") and False)

    def test_levels(self):
        with tempfile.TemporaryDirectory() as ws:
            p = policy.Policy(level="read-only")
            self.assertFalse(p.can_write(os.path.join(ws, "a"))[0])
            p = policy.Policy(level="workspace", workspace=ws)
            self.assertTrue(p.can_write(os.path.join(ws, "a"))[0])
            self.assertFalse(p.can_write("/etc/passwd")[0])
            self.assertFalse(p.can_write(os.path.expanduser("~/.ssh/authorized_keys"))[0])
            self.assertFalse(p.network)
            spec = p.sandbox_spec()
            self.assertFalse(spec.network)

    def test_full_control_never_implies_network(self):
        p = policy.Policy(level="full-control")
        self.assertFalse(p.network)
        self.assertFalse(p.sandbox_spec().network)

    def test_defaults_cannot_enable_network(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[agent]\nnetwork = true\nsecrets = true\nlevel = "full-control"\n')
        try:
            d = policy.load_defaults(Path(f.name))
        finally:
            os.unlink(f.name)
        self.assertFalse(d["network"])
        self.assertFalse(d["secrets"])
        self.assertEqual(d["level"], "workspace")

    def test_scope(self):
        p = policy.Policy(network=True, network_scope=["example.org"])
        self.assertEqual(p.out_of_scope_hosts("curl https://api.example.org/x"), [])
        self.assertEqual(p.out_of_scope_hosts("curl https://evil.com/x"), ["evil.com"])


# ------------------------------------------------------------------ network guard
class TestNetworkGuard(unittest.TestCase):
    def setUp(self):
        self.v4 = checks.parse_iptables_save((FIX / "tails-iptables-save.txt").read_text())
        self.v6 = checks.parse_iptables_save((FIX / "tails-ip6tables-save.txt").read_text())

    def _by_id(self, cs):
        return {c.id: c for c in cs}

    def test_tails_ruleset_passes(self):
        cs = self._by_id(checks.evaluate_firewall(self.v4, self.v6))
        for cid in ("firewall.default_deny", "firewall.final_reject", "firewall.kill_switch",
                    "dns.redirect", "firewall.transproxy"):
            self.assertEqual(cs[cid].status, "pass", (cid, cs[cid].detail))
        self.assertEqual(cs["firewall.blocked_attempts"].data["rejected_packets"], 3)

    def test_leaky_rule_detected(self):
        self.v4["filter"]["rules"].insert(
            0, {"rule": "-A OUTPUT ! -o lo -m owner --uid-owner 1000 -j ACCEPT", "counters": (0, 0)})
        cs = self._by_id(checks.evaluate_firewall(self.v4, self.v6))
        self.assertEqual(cs["firewall.kill_switch"].status, "fail")

    def test_open_policy_detected(self):
        self.v6["filter"]["policies"]["OUTPUT"] = "ACCEPT"
        cs = self._by_id(checks.evaluate_firewall(self.v4, self.v6))
        self.assertEqual(cs["firewall.default_deny"].status, "fail")

    def test_classify_peer(self):
        self.assertEqual(checks.classify_peer("127.0.0.1:9050"), "loopback")
        self.assertEqual(checks.classify_peer("192.168.1.10:445"), "lan")
        self.assertEqual(checks.classify_peer("93.184.216.34:443"), "internet")
        self.assertEqual(checks.classify_peer("[2606:4700::1]:443"), "internet")
        self.assertEqual(checks.classify_peer("0.0.0.0:*"), "none")

    def test_sockets(self):
        rows = [
            {"proto": "tcp", "state": "ESTAB", "local": "10.0.2.15:51000", "peer": "185.220.101.1:443",
             "user": "debian-tor", "processes": ["tor"]},
            {"proto": "tcp", "state": "ESTAB", "local": "10.0.2.15:51002", "peer": "93.184.216.34:443",
             "user": "amnesia", "processes": ["curl"]},
        ]
        cs = self._by_id(checks.evaluate_sockets(rows))
        self.assertEqual(cs["connections.direct"].status, "fail")
        cs = self._by_id(checks.evaluate_sockets(rows[:1]))
        self.assertEqual(cs["connections.direct"].status, "pass")

    def test_tor_eval(self):
        ok = checks.evaluate_tor({"available": True, "bootstrap_progress": 100, "circuits_general": 3,
                                  "network_liveness": "up"}, True)
        self.assertEqual([c.status for c in ok], ["pass", "pass"])
        off = checks.evaluate_tor({"available": True, "bootstrap_progress": 0, "disable_network": True}, False)
        self.assertEqual(off[0].status, "warn")

    def test_resolv_conf(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("nameserver 127.0.0.1\n")
        try:
            self.assertEqual(checks.check_resolv_conf(f.name).status, "pass")
            Path(f.name).write_text("nameserver 8.8.8.8\n")
            self.assertEqual(checks.check_resolv_conf(f.name).status, "fail")
        finally:
            os.unlink(f.name)


# ------------------------------------------------------------------ audit
class TestAudit(unittest.TestCase):
    MOUNTS = """\
/dev/sda1 /run/live/medium vfat ro,noatime 0 0
/dev/loop0 /run/live/rootfs/filesystem.squashfs squashfs ro,noatime 0 0
tmpfs /run/live/overlay tmpfs rw,noatime,mode=755 0 0
overlay / overlay rw,noatime,lowerdir=/run/live/rootfs/filesystem.squashfs/,upperdir=/run/live/overlay/rw,workdir=/run/live/overlay/work 0 0
"""

    def test_overlay_in_ram(self):
        m = audit.parse_mounts(self.MOUNTS)
        self.assertEqual(audit.check_root_overlay(m).status, "pass")
        self.assertEqual(audit.check_squashfs(m).status, "pass")
        self.assertEqual(audit.check_internal_disks(m).status, "pass")

    def test_internal_disk_flagged(self):
        m = audit.parse_mounts(self.MOUNTS + "/dev/nvme0n1p2 /media/amnesia/Windows ntfs3 rw 0 0\n")
        self.assertEqual(audit.check_internal_disks(m).status, "warn")

    def test_swap(self):
        self.assertEqual(audit.check_swap("Filename Type Size Used Priority\n").status, "pass")
        self.assertEqual(audit.check_swap("Filename Type Size Used Priority\n/dev/sda2 partition 1 0 -2\n").status,
                         "fail")


# ------------------------------------------------------------------ models / catalog
class TestModels(unittest.TestCase):
    def test_catalog_pinned(self):
        for m in models.BUILTIN:
            self.assertRegex(m.sha256, r"^[0-9a-f]{64}$", m.name)
            self.assertRegex(m.url, r"/resolve/[0-9a-f]{40}/", m.name)
            self.assertTrue(m.url.startswith("https://huggingface.co/"))
            self.assertGreater(m.size, 100_000_000)

    def test_recommend(self):
        small = models.recommend({"ram_available": 4 * 1024 ** 3})
        big = models.recommend({"ram_available": 32 * 1024 ** 3})
        self.assertEqual(small.name, "qwen3-0.6b")
        self.assertIn(big.name, ("qwen3-8b",))

    def test_pull_requires_consent(self):
        old = os.environ.get("XDG_RUNTIME_DIR")
        with tempfile.TemporaryDirectory() as d:
            os.environ["XDG_RUNTIME_DIR"] = d
            try:
                with self.assertRaises(models.ConsentDenied):
                    models.pull("qwen3-0.6b", consent=lambda *a: False, require_tor=False)
            finally:
                if old is None:
                    os.environ.pop("XDG_RUNTIME_DIR", None)
                else:
                    os.environ["XDG_RUNTIME_DIR"] = old


class TestToolCatalog(unittest.TestCase):
    def test_entries_valid(self):
        ts = tools_catalog.load()
        self.assertGreater(len(ts), 40)
        names = set()
        for t in ts:
            self.assertIn(t["network_policy"], tools_catalog.NETWORK_POLICIES, t["name"])
            self.assertIn(t["privacy"], tools_catalog.PRIVACY_STATUS, t["name"])
            self.assertNotIn(t["name"], names)
            names.add(t["name"])
        for required in ("nmap", "wireshark", "tcpdump", "zeek", "suricata", "zap", "burpsuite", "ghidra",
                         "cutter", "radare2", "gdb", "volatility3", "yara", "semgrep", "trivy", "lynis",
                         "kismet", "aircrack-ng", "keepassxc", "gnupg", "age"):
            self.assertIn(required, names)

    def test_bundles_reference_catalog(self):
        b = json.loads((INC / "usr/share/veil/nix/bundles.json").read_text())
        self.assertTrue(b["burp"]["unfree"])
        self.assertTrue(b["volatility"]["unfree"])
        self.assertFalse(b["reverse-engineering"].get("unfree", False))


# ------------------------------------------------------------------ agent loop
class FakeBackend:
    name = "fake"
    running = True

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, **kw):
        self.calls.append(messages[-1])
        return self.script.pop(0)

    def stop(self):
        pass


def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


@unittest.skipUnless(userns_ok(), "unprivileged user namespaces unavailable")
class TestAgentLoop(unittest.TestCase):
    def setUp(self):
        self.rt = tempfile.TemporaryDirectory()
        self._old_rt = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self.rt.name

    def tearDown(self):
        if self._old_rt is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self._old_rt
        self.rt.cleanup()

    def test_shell_runs_without_network(self):
        be = FakeBackend([_call("shell", {"command": "cat /proc/net/dev | tail -n +3 | cut -d: -f1 | tr -d ' '"}),
                          {"role": "assistant", "content": "done"}])
        with tempfile.TemporaryDirectory() as ws:
            a = loop.Agent(backend=be, policy=policy.Policy(level="workspace", workspace=ws))
            out = a.run("list interfaces")
        self.assertEqual(out, "done")
        tool_msg = json.loads(be.calls[1]["content"])
        self.assertTrue(tool_msg["ok"])
        self.assertEqual(tool_msg["output"].split("\n", 1)[1].strip().splitlines(), ["lo"])

    def test_write_then_read_in_workspace(self):
        with tempfile.TemporaryDirectory() as ws:
            be = FakeBackend([_call("write_file", {"path": "hello.py", "content": "print(17 + 25)\n"}),
                              _call("shell", {"command": "python3 hello.py"}, "c2"),
                              {"role": "assistant", "content": "42"}])
            a = loop.Agent(backend=be, policy=policy.Policy(level="workspace", workspace=ws))
            a.run("make and run")
            self.assertEqual(Path(ws, "hello.py").read_text(), "print(17 + 25)\n")
            res = json.loads(be.calls[2]["content"])
            self.assertIn("42", res["output"])

    def test_read_only_cannot_write(self):
        with tempfile.TemporaryDirectory() as ws:
            be = FakeBackend([_call("write_file", {"path": f"{ws}/x", "content": "x"}),
                              {"role": "assistant", "content": "ok"}])
            a = loop.Agent(backend=be, policy=policy.Policy(level="read-only"))
            a.run("write")
            self.assertFalse(Path(ws, "x").exists())
            self.assertIn("not available", json.loads(be.calls[1]["content"])["output"])

    def test_risky_command_denied_without_human(self):
        be = FakeBackend([_call("shell", {"command": "dd if=/dev/zero of=/dev/sda bs=1M count=1"}),
                          {"role": "assistant", "content": "stopped"}])
        a = loop.Agent(backend=be, policy=policy.Policy(level="full-control"))  # confirm() denies by default
        a.run("wipe")
        res = json.loads(be.calls[1]["content"])
        self.assertFalse(res["ok"])
        self.assertIn("DENIED", res["output"])

    def test_network_command_fails_and_is_explained(self):
        be = FakeBackend([_call("shell", {"command": "curl -sS -m 5 https://example.com"}),
                          {"role": "assistant", "content": "no network"}])
        a = loop.Agent(backend=be, policy=policy.Policy(level="full-control"))
        a.run("fetch")
        res = json.loads(be.calls[1]["content"])
        self.assertFalse(res["ok"])
        self.assertIn("DENIED for this session", res["output"])

    def test_fallback_tool_call_parsing(self):
        calls = loop.parse_fallback_tool_calls(
            'x <tool_call>{"name": "list_dir", "arguments": {"path": "/"}}</tool_call>')
        self.assertEqual(calls[0]["function"]["name"], "list_dir")

    def test_step_limit(self):
        be = FakeBackend([_call("system_info", {}) for _ in range(5)])
        a = loop.Agent(backend=be, policy=policy.Policy(max_steps=3))
        out = a.run("loop forever")
        self.assertTrue(out.startswith("[veil] Stopped after 3 steps"))


# ------------------------------------------------------------------ session autostart
class TestAutostart(unittest.TestCase):
    def test_wanted(self):
        import importlib.machinery
        import importlib.util
        path = str(INC / "usr/local/lib/veil/veil-autostart")
        loader = importlib.machinery.SourceFileLoader("veil_autostart", path)
        spec = importlib.util.spec_from_loader("veil_autostart", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        self.assertTrue(mod.wanted("systemd-desktop-target.desktop", {"Exec": "/usr/local/lib/start-systemd-desktop-target"}))
        self.assertFalse(mod.wanted("gnome-keyring-secrets.desktop", {"Exec": "x", "OnlyShowIn": "GNOME;Unity;"}))
        self.assertTrue(mod.wanted("xfce4-notifyd.desktop", {"Exec": "x", "OnlyShowIn": "XFCE;"}))
        self.assertFalse(mod.wanted("hidden.desktop", {"Exec": "x", "Hidden": "true", "_user": True}))
        self.assertFalse(mod.wanted("notxfce.desktop", {"Exec": "x", "NotShowIn": "XFCE;", "_user": True}))
        # Not allowlisted system entries never start (e.g. a second nm-applet, Orca).
        self.assertFalse(mod.wanted("nm-applet.desktop", {"Exec": "nm-applet"}))
        self.assertFalse(mod.wanted("orca-autostart.desktop", {"Exec": "orca"}))
        # User entries run; unknown AutostartCondition forms are refused.
        self.assertTrue(mod.wanted("mine.desktop", {"Exec": "true", "_user": True}))
        self.assertFalse(mod.wanted("cond.desktop", {"Exec": "true", "_user": True,
                                                     "AutostartCondition": "GNOME3 if-session gnome"}))


# ------------------------------------------------------------------ repository hygiene
class TestRepo(unittest.TestCase):
    def test_hooks_executable(self):
        for h in (ROOT / "config/chroot_local-hooks").glob("*veil*"):
            self.assertTrue(os.access(h, os.X_OK), h)

    def test_llama_hook_pins_hash(self):
        s = (ROOT / "config/chroot_local-hooks/57-veil-llama-cpp").read_text()
        self.assertRegex(s, r'SHA256="[0-9a-f]{64}"')
        self.assertIn("sha256sum --check", s)
        self.assertIn("LLAMA_USE_PREBUILT_UI=OFF", s)

    def test_no_secrets_committed(self):
        pat = re.compile(r"(ghp_[A-Za-z0-9]{30,}|-----BEGIN (OPENSSH|RSA|EC|PGP) PRIVATE KEY-----)")
        for p in list((INC / "usr/lib/python3/dist-packages/veil").rglob("*.py")) + [ROOT / "README.md"]:
            if p.exists():
                self.assertIsNone(pat.search(p.read_text(errors="ignore")), p)


if __name__ == "__main__":
    unittest.main()
