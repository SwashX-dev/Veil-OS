"""`veil security audit`: amnesia, hardening and sandbox checks.

These checks complement Network Guard. They verify that Veil still has the
properties it inherits from Tails (RAM-backed writable layer, no swap, no
internal-disk mounts, volatile logs, kernel hardening) and that Veil's own
sandbox isolates as designed.
"""

from __future__ import annotations

import os
from pathlib import Path

from veil import common, sandbox
from veil.netguard.checks import Check

BOOT_MEDIUM = "/run/live/medium"
PERSISTENCE = str(common.PERSISTENCE_MOUNT)


def parse_mounts(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            rows.append({"source": parts[0], "target": parts[1].replace("\\040", " "),
                         "fstype": parts[2], "options": parts[3]})
    return rows


def check_root_overlay(mounts: list[dict]) -> Check:
    root = [m for m in mounts if m["target"] == "/"]
    m = root[-1] if root else {}
    upper = ""
    for opt in m.get("options", "").split(","):
        if opt.startswith("upperdir="):
            upper = opt.split("=", 1)[1]
    # The upperdir path is recorded as seen from the initramfs (e.g.
    # "/live/overlay//rw"); after switch_root live-boot moves that tmpfs to
    # e.g. /usr/lib/live/mount/overlay. Match on the overlay directory name.
    parts = [x for x in upper.split("/") if x]
    base = parts[-2] if len(parts) >= 2 else ""
    upper_fs = ""
    for mm in mounts:
        t = mm["target"].rstrip("/")
        if base and t.split("/")[-1] == base and "/live" in t:
            upper_fs = mm["fstype"]
    ok = m.get("fstype") == "overlay" and upper_fs == "tmpfs"
    return Check("amnesia.ram_overlay", "Writable layer lives in RAM",
                 "pass" if ok else "fail",
                 "Root is an overlay over the read-only SquashFS; all writes go to tmpfs (RAM)" if ok
                 else f"Root fs={m.get('fstype')} upper={upper or '?'} on {upper_fs or '?'}",
                 {"root_fstype": m.get("fstype"), "upper": upper, "upper_fstype": upper_fs}, "amnesia")


def check_squashfs(mounts: list[dict]) -> Check:
    ok = any(m["fstype"] == "squashfs" and "ro" in m["options"].split(",") for m in mounts)
    return Check("amnesia.readonly_base", "Base system is read-only", "pass" if ok else "fail",
                 "The OS image is mounted read-only (SquashFS)" if ok else "No read-only SquashFS mount found",
                 {}, "amnesia")


def check_swap(text: str) -> Check:
    lines = [ln for ln in text.splitlines()[1:] if ln.strip()]
    disk = [ln for ln in lines if not ln.split()[0].startswith("/dev/zram")]
    zram = [ln.split()[0] for ln in lines if ln.split()[0].startswith("/dev/zram")]
    return Check("amnesia.no_swap", "No swap on disk", "pass" if not disk else "fail",
                 ("No disk swap; " + (f"compressed RAM swap only ({', '.join(zram)})" if zram else "no swap at all"))
                 if not disk else f"Disk swap active: {disk}",
                 {"swaps": lines}, "amnesia")


def check_internal_disks(mounts: list[dict]) -> Check:
    flagged = []
    for m in mounts:
        src, tgt = m["source"], m["target"]
        if not src.startswith("/dev/") or src.startswith(("/dev/loop", "/dev/mapper/TailsData")):
            continue
        if tgt.startswith((BOOT_MEDIUM, "/run/live", "/lib/live", "/usr/lib/live", PERSISTENCE,
                           "/live/persistence")):
            continue
        flagged.append(f"{src} on {tgt}")
    return Check("amnesia.no_internal_mounts", "No internal disk is mounted",
                 "pass" if not flagged else "warn",
                 "Only the Veil USB stick (and unlocked Persistent Storage) are mounted" if not flagged
                 else f"Mounted by you: {', '.join(flagged)}. Writes there survive shutdown.",
                 {"mounts": flagged}, "amnesia")


def check_journal(ram_root: bool = True) -> Check:
    persistent = Path("/var/log/journal")
    on_disk_dir = persistent.is_dir()
    if not on_disk_dir:
        return Check("amnesia.volatile_logs", "System logs are volatile", "pass",
                     "The journal is kept in /run/log/journal (RAM)", {}, "amnesia")
    return Check("amnesia.volatile_logs", "System logs are volatile",
                 "pass" if ram_root else "warn",
                 "/var/log/journal lives on the RAM-backed root overlay and is erased at shutdown" if ram_root
                 else "/var/log/journal exists and the root filesystem is not RAM-backed", {}, "amnesia")


def check_persistence() -> Check:
    if common.persistence_unlocked():
        return Check("amnesia.persistence", "Persistent Storage", "info",
                     "Unlocked: files under ~/Persistent and enabled features survive shutdown (LUKS2-encrypted)",
                     {}, "amnesia")
    return Check("amnesia.persistence", "Persistent Storage", "pass",
                 "Locked or absent: nothing from this session is kept after shutdown", {}, "amnesia")


SYSCTL_EXPECT = {
    "kernel/kptr_restrict": ("2",),
    "kernel/yama/ptrace_scope": ("2", "3"),
    "kernel/unprivileged_bpf_disabled": ("1", "2"),
    "vm/unprivileged_userfaultfd": ("0",),
    "kernel/sysrq": ("0",),
    "net/core/bpf_jit_harden": ("2",),
    "fs/protected_symlinks": ("1",),
    "fs/protected_hardlinks": ("1",),
}


def check_sysctls() -> Check:
    bad, vals = [], {}
    for key, allowed in SYSCTL_EXPECT.items():
        try:
            v = Path("/proc/sys/" + key).read_text().strip()
        except OSError:
            v = "n/a"
        vals[key.replace("/", ".")] = v
        if v not in allowed and v != "n/a":
            bad.append(f"{key.replace('/', '.')}={v}")
    return Check("hardening.sysctl", "Kernel hardening settings (from Tails)",
                 "pass" if not bad else "fail",
                 "kptr_restrict, ptrace_scope, BPF, userfaultfd, SysRq and symlink protections are hardened"
                 if not bad else f"Weakened: {', '.join(bad)}", vals, "hardening")


def check_apparmor() -> Check:
    try:
        enabled = Path("/sys/module/apparmor/parameters/enabled").read_text().strip() == "Y"
    except OSError:
        enabled = False
    return Check("hardening.apparmor", "AppArmor is enabled", "pass" if enabled else "fail",
                 "AppArmor LSM is active (Tor Browser, Thunderbird, Totem... are confined)" if enabled
                 else "AppArmor is not enabled", {}, "hardening")


def check_lockdown() -> Check:
    try:
        text = Path("/sys/kernel/security/lockdown").read_text().strip()
        mode = text.split("[", 1)[1].split("]", 1)[0] if "[" in text else text
    except OSError:
        mode = "n/a"
    return Check("hardening.lockdown", "Kernel lockdown", "info",
                 f"Kernel lockdown mode: {mode} (integrity when booted with Secure Boot)",
                 {"mode": mode}, "hardening")


def check_sandbox() -> Check:
    res = sandbox.self_test()
    ok = all(res.values()) and res.get("bwrap_available", False)
    failed = [k for k, v in res.items() if not v]
    return Check("sandbox.self_test", "Veil sandbox isolation (Agent, labs)",
                 "pass" if ok else "fail",
                 "No network interfaces, no DNS, no session bus, read-only root, seccomp active" if ok
                 else f"Failed: {', '.join(failed)}", res, "sandbox")


def run_all() -> list[Check]:
    try:
        mounts = parse_mounts(Path("/proc/self/mounts").read_text())
    except OSError:
        mounts = []
    try:
        swaps = Path("/proc/swaps").read_text()
    except OSError:
        swaps = ""
    overlay = check_root_overlay(mounts)
    checks = [
        overlay,
        check_squashfs(mounts),
        check_swap(swaps),
        check_internal_disks(mounts),
        check_journal(overlay.status == "pass"),
        check_persistence(),
        check_sysctls(),
        check_apparmor(),
        check_lockdown(),
    ]
    if os.environ.get("VEIL_SANDBOX") != "1":
        checks.append(check_sandbox())
    return checks
