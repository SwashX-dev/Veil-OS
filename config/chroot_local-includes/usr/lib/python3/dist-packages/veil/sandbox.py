"""Kernel-enforced sandboxes for Veil Agent tools and offline labs.

The sandbox is built on bubblewrap (unprivileged user namespaces) plus a
seccomp filter assembled here, so it needs no extra dependencies.

Security properties (enforced by the kernel, not by prompts):

* network=False  -> the process gets a fresh, empty network namespace with
  only a loopback interface. It cannot reach Tor, the LAN or the Internet,
  and it cannot reach the Tor control port or SOCKS ports either.
* hide_session=True -> the user's D-Bus session bus, the system bus, the
  Wayland/X11 sockets, PipeWire, the GnuPG and SSH agents are not visible,
  so the sandbox cannot ask an outside process (e.g. `systemd-run --user`)
  to act on its behalf.
* hide_secrets=True -> well-known credential stores are replaced by empty
  tmpfs mounts.
* no_new_privs is always set by bubblewrap, so sudo/setuid binaries cannot
  raise privileges from inside the sandbox.
* The seccomp filter denies ptrace, kernel keyrings, BPF, module loading,
  kexec, mount, namespace re-entry, userfaultfd, perf and the x32/i386
  syscall ABIs.

What it does NOT do: it does not stop a process from reading files that the
user can read, unless they are listed in SECRET_PATHS or the root is masked.
"""

from __future__ import annotations

import glob
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

BWRAP = os.environ.get("VEIL_BWRAP", "bwrap")

# Credential stores masked unless the user explicitly grants "secrets".
SECRET_PATHS = [
    "~/.gnupg",
    "~/.ssh",
    "~/.password-store",
    "~/.local/share/keyrings",
    "~/.mozilla",
    "~/.tor-browser",
    "~/.thunderbird",
    "~/.electrum",
    "~/.purple",
    "~/.config/veil/agent-secrets",
    "~/Persistent/.veil/agent/keys",
]

# x86_64 syscall numbers denied inside every Veil sandbox.
DENIED_SYSCALLS = {
    "ptrace": 101,
    "process_vm_readv": 310,
    "process_vm_writev": 311,
    "add_key": 248,
    "request_key": 249,
    "keyctl": 250,
    "bpf": 321,
    "userfaultfd": 323,
    "perf_event_open": 298,
    "kexec_load": 246,
    "kexec_file_load": 320,
    "init_module": 175,
    "finit_module": 313,
    "delete_module": 176,
    "mount": 165,
    "umount2": 166,
    "pivot_root": 155,
    "swapon": 167,
    "swapoff": 168,
    "reboot": 169,
    "acct": 163,
    "iopl": 172,
    "ioperm": 173,
    "name_to_handle_at": 303,
    "open_by_handle_at": 304,
    "setns": 308,
    "fsopen": 430,
    "fsmount": 432,
    "move_mount": 429,
    "open_tree": 428,
}

# Extra syscalls denied when the sandbox does not provide fake root
# (prevents creating nested user namespaces from inside the sandbox).
DENIED_WITHOUT_FAKE_ROOT = {
    "unshare": 272,
}

AUDIT_ARCH_X86_64 = 0xC000003E
X32_SYSCALL_BIT = 0x40000000
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_ERRNO = 0x00050000
EPERM = 1

BPF_LD_W_ABS = 0x20
BPF_JEQ_K = 0x15
BPF_JGE_K = 0x35
BPF_RET_K = 0x06


def _insn(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("=HBBI", code, jt, jf, k)


def build_seccomp_filter(syscalls: list[int]) -> bytes:
    """Assemble a classic-BPF seccomp program denying `syscalls` with EPERM.

    Layout:
        0: A = arch
        1: if A == X86_64 goto 3
        2: return ERRNO(EPERM)               ; foreign ABI (i386 int 0x80)
        3: A = nr
        4: if A >= X32 bit goto DENY          ; x32 ABI
        5..: if A == nr_i goto DENY
        N: return ALLOW
        N+1 (DENY): return ERRNO(EPERM)
    """
    nrs = sorted(set(syscalls))
    n = len(nrs)
    deny = SECCOMP_RET_ERRNO | EPERM
    prog = [
        _insn(BPF_LD_W_ABS, 0, 0, 4),
        _insn(BPF_JEQ_K, 1, 0, AUDIT_ARCH_X86_64),
        _insn(BPF_RET_K, 0, 0, deny),
        _insn(BPF_LD_W_ABS, 0, 0, 0),
        _insn(BPF_JGE_K, n + 1, 0, X32_SYSCALL_BIT),
    ]
    for i, nr in enumerate(nrs):
        prog.append(_insn(BPF_JEQ_K, n - i, 0, nr))
    prog.append(_insn(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW))
    prog.append(_insn(BPF_RET_K, 0, 0, deny))
    return b"".join(prog)


@dataclass
class SandboxSpec:
    network: bool = False
    readonly_root: bool = True
    writable: list[str] = field(default_factory=list)
    hide_secrets: bool = True
    hide_session: bool = True
    unshare_pid: bool = True
    fake_root: bool = False
    cwd: str | None = None
    hostname: str = "veil-sandbox"
    extra_env: dict[str, str] = field(default_factory=dict)
    seccomp: bool = True
    # Device nodes to expose (e.g. /dev/dri for GPU inference). Default: none.
    devices: list[str] = field(default_factory=list)
    # Paths re-exposed read-only after masking (e.g. a model file that lives
    # under $XDG_RUNTIME_DIR, which is hidden when /run is masked).
    readonly: list[str] = field(default_factory=list)


class SandboxUnavailable(RuntimeError):
    pass


def bwrap_available() -> bool:
    return shutil.which(BWRAP) is not None


def _expand(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def build_argv(spec: SandboxSpec, command: list[str], seccomp_fd: int | None = None) -> list[str]:
    """Return the full bwrap argv that runs `command` under `spec`."""
    if not bwrap_available():
        raise SandboxUnavailable("bubblewrap (bwrap) is not installed")
    uid = os.getuid()
    a: list[str] = [BWRAP, "--die-with-parent", "--new-session",
                    "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
                    "--hostname", spec.hostname]
    if not spec.network:
        a.append("--unshare-net")
    if spec.unshare_pid:
        a.append("--unshare-pid")
    if spec.fake_root:
        a += ["--unshare-user", "--uid", "0", "--gid", "0", "--cap-add", "ALL"]
    else:
        a += ["--cap-drop", "ALL"]

    a += ["--ro-bind" if spec.readonly_root else "--bind", "/", "/"]
    a += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/var/tmp"]
    for dev in spec.devices:
        for p in sorted(glob.glob(dev)):
            a += ["--dev-bind", p, p]

    if not spec.network:
        # An empty network namespace is not enough on its own: Unix sockets
        # are not namespaced. nscd / systemd-resolved / nsncd would resolve
        # DNS names on our behalf (a DNS exfiltration channel), and a Nix
        # daemon would fetch arbitrary URLs. Mask every runtime socket.
        for p in ("/run", "/var/run"):
            if os.path.isdir(p) and not os.path.islink(p):
                a += ["--tmpfs", p]
        for p in ("/nix/var/nix/daemon-socket",):
            if os.path.isdir(p):
                a += ["--tmpfs", p]
        # NixOS hosts (used for development/CI) keep their software under
        # /run/current-system; re-expose it read-only. Absent on Veil OS.
        for p in ("/run/current-system",):
            if os.path.exists(p):
                a += ["--ro-bind", p, p]
    if spec.hide_session:
        for p in (f"/run/user/{uid}", "/run/dbus", "/var/run/dbus", "/run/nscd",
                  "/run/systemd/resolve"):
            if spec.network and os.path.isdir(p) and not os.path.islink(p):
                a += ["--tmpfs", p]
        for var in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS",
                    "WAYLAND_DISPLAY", "DISPLAY", "SSH_AUTH_SOCK",
                    "GPG_AGENT_INFO", "XAUTHORITY", "PULSE_SERVER"):
            a += ["--unsetenv", var]
        a += ["--setenv", "XDG_RUNTIME_DIR", "/tmp/runtime"]

    if spec.hide_secrets:
        for raw in SECRET_PATHS:
            p = _expand(raw)
            if os.path.isdir(p):
                a += ["--tmpfs", p]
            elif os.path.isfile(p):
                a += ["--ro-bind", "/dev/null", p]

    for raw in spec.readonly:
        p = _expand(raw)
        if os.path.exists(p):
            a += ["--ro-bind", p, p]

    for raw in spec.writable:
        p = _expand(raw)
        if os.path.exists(p):
            a += ["--bind", p, p]

    for k, v in spec.extra_env.items():
        a += ["--setenv", k, v]
    a += ["--setenv", "VEIL_SANDBOX", "1"]
    if not spec.network:
        a += ["--setenv", "VEIL_SANDBOX_NETWORK", "none"]
    if spec.cwd:
        a += ["--chdir", _expand(spec.cwd)]
    if seccomp_fd is not None:
        a += ["--seccomp", str(seccomp_fd)]
    a.append("--")
    a += command
    return a


def _seccomp_fd(spec: SandboxSpec) -> int | None:
    if not spec.seccomp:
        return None
    syscalls = list(DENIED_SYSCALLS.values())
    if not spec.fake_root:
        syscalls += list(DENIED_WITHOUT_FAKE_ROOT.values())
    prog = build_seccomp_filter(syscalls)
    fd = os.memfd_create("veil-seccomp", 0)
    os.write(fd, prog)
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


def run(spec: SandboxSpec, command: list[str], timeout: float | None = None,
        input: str | None = None) -> subprocess.CompletedProcess:
    """Run `command` inside the sandbox and capture its output."""
    fd = _seccomp_fd(spec)
    try:
        argv = build_argv(spec, command, fd)
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              input=input, pass_fds=(fd,) if fd is not None else (),
                              check=False)
    finally:
        if fd is not None:
            os.close(fd)


def popen(spec: SandboxSpec, command: list[str], **kwargs) -> subprocess.Popen:
    """Start `command` inside the sandbox; caller owns the process."""
    fd = _seccomp_fd(spec)
    try:
        argv = build_argv(spec, command, fd)
        pass_fds = tuple(kwargs.pop("pass_fds", ())) + ((fd,) if fd is not None else ())
        return subprocess.Popen(argv, pass_fds=pass_fds, **kwargs)
    finally:
        if fd is not None:
            os.close(fd)


def self_test() -> dict[str, bool]:
    """Verify the sandbox really isolates. Used by `veil security audit`."""
    results: dict[str, bool] = {}
    if not bwrap_available():
        return {"bwrap_available": False}
    results["bwrap_available"] = True
    spec = SandboxSpec()
    r = run(spec, ["sh", "-c", "cat /proc/net/dev"], timeout=20)
    ifaces = [ln.split(":")[0].strip() for ln in r.stdout.splitlines()[2:] if ":" in ln]
    results["no_network_interfaces_except_lo"] = r.returncode == 0 and ifaces == ["lo"]
    probe = ("import ctypes,os;libc=ctypes.CDLL(None,use_errno=True);"
             "r=libc.syscall(101,0,0,0,0);print(r,ctypes.get_errno())")
    r = run(spec, ["python3", "-I", "-c", probe], timeout=20)
    results["seccomp_denies_ptrace"] = r.returncode == 0 and r.stdout.split() == ["-1", "1"]
    uid = os.getuid()
    r = run(spec, ["sh", "-c", f"ls -A /run/user/{uid} 2>/dev/null | wc -l"], timeout=20)
    results["session_bus_hidden"] = r.returncode == 0 and r.stdout.strip() == "0"
    r = run(spec, ["sh", "-c", "touch /etc/veil-sandbox-write-test 2>/dev/null && echo w || echo ro"], timeout=20)
    results["root_filesystem_read_only"] = r.stdout.strip() == "ro"
    # Name resolution must fail: no resolver socket may be reachable.
    r = run(spec, ["sh", "-c", "getent hosts tails.net >/dev/null 2>&1 && echo resolved || echo blocked"],
            timeout=20)
    results["dns_resolution_blocked"] = r.stdout.strip() == "blocked"
    return results
