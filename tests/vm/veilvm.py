#!/usr/bin/env python3
"""Boot a Veil OS (or Tails) image in QEMU/KVM and drive it for testing.

Uses the same virtio "remote shell" as the upstream Tails test suite. It is
only enabled when the kernel command line contains
`autotest_never_use_this_option`, which this harness passes through direct
kernel boot. Images users install never enable it.

Without root: needs /dev/kvm, qemu-system-x86_64, and OVMF for UEFI.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


class QMP:
    def __init__(self, path: str):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.f = self.s.makefile("rw")
        self._read()
        self.cmd("qmp_capabilities")

    def _read(self):
        while True:
            line = self.f.readline()
            if not line:
                raise ConnectionError("QMP closed")
            msg = json.loads(line)
            if "event" not in msg:
                return msg

    def cmd(self, name: str, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        r = self._read()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r.get("return")


class RemoteShell:
    def __init__(self, path: str):
        self.path = path
        self.id = 0

    def spawn(self, cmd: str, user: str = "amnesia") -> None:
        """Start a command without waiting (the server sends no reply)."""
        self.id += 1
        s = socket.socket(socket.AF_UNIX)
        s.connect(self.path)
        s.sendall((json.dumps([self.id, "sh_spawn", user, {}, cmd]) + "\n").encode())
        s.close()

    def _call(self, *args, timeout: float = 120):
        self.id += 1
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        s.connect(self.path)
        try:
            s.sendall((json.dumps([self.id, *args]) + "\n").encode())
            buf = b""
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if not line.startswith(b"["):
                        line = b"[" + line.split(b"[", 1)[-1]
                    try:
                        rid, status, *rest = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rid != self.id:
                        continue
                    if status != "success":
                        raise RuntimeError(f"remote shell: {status}: {rest}")
                    return rest
            raise TimeoutError(f"remote shell: no answer to {args[0]} within {timeout}s")
        finally:
            s.close()

    def wait_up(self, timeout: float = 240) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self._call("sh_call", "root", {}, "true", timeout=5)
                return
            except (OSError, TimeoutError, RuntimeError):
                time.sleep(2)
        raise TimeoutError("remote shell did not come up")

    def signal_ready(self):
        return self._call("signal_ready", timeout=30)

    def sh(self, cmd: str, user: str = "root", timeout: float = 120) -> tuple[int, str, str]:
        rc, out, err = self._call("sh_call", user, {}, cmd, timeout=timeout)
        return rc, out, err

    def py(self, code: str, user: str = "amnesia", timeout: float = 120):
        """Run Python code as `user`; returns (exception_or_None, stdout, stderr).

        Uses sh_call rather than the remote shell's python_execute: the latter
        feeds an InteractiveConsole, which in Debian trixie's Python 3.13.5
        rejects the wrapper the server generates (dedent after try/except).
        """
        import base64 as _b64
        b64 = _b64.b64encode(code.encode()).decode()
        cmd = f"echo {b64} | base64 -d > /tmp/.veil-test-$$.py && python3 /tmp/.veil-test-$$.py; rc=$?; rm -f /tmp/.veil-test-$$.py; exit $rc"
        rc, out, err = self.sh(cmd, user=user, timeout=timeout)
        return (None if rc == 0 else f"exit {rc}: {err.strip().splitlines()[-1] if err.strip() else ''}"), out, err

    def read_file(self, path: str) -> bytes:
        (b64,) = self._call("file_read", path, timeout=60)
        return base64.b64decode(b64)


def find_ovmf() -> tuple[str, str] | None:
    env = os.environ.get("VEIL_OVMF_DIR")
    cands = [Path(env)] if env else []
    cands += [Path("/usr/share/OVMF"), Path("/usr/share/ovmf")]
    for d in cands:
        for code, var in (("OVMF_CODE.fd", "OVMF_VARS.fd"), ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"),
                          ("FV/OVMF_CODE.fd", "FV/OVMF_VARS.fd")):
            if (d / code).exists() and (d / var).exists():
                return str(d / code), str(d / var)
    return None


class VM:
    def __init__(self, image: str, workdir: str, mode: str = "usb", firmware: str = "uefi",
                 memory: str = "4096", cpus: int = 2, autotest: bool = True, extra_disk: str | None = None,
                 kernel: str | None = None, initrd: str | None = None, cmdline: str | None = None,
                 network: bool = True, qemu: str = "qemu-system-x86_64", snapshot: bool = True):
        self.image, self.workdir, self.mode, self.firmware = image, Path(workdir), mode, firmware
        self.memory, self.cpus, self.autotest, self.extra_disk = memory, cpus, autotest, extra_disk
        self.kernel, self.initrd, self.cmdline, self.network, self.qemu = kernel, initrd, cmdline, network, qemu
        self.snapshot = snapshot
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.qmp_path = str(self.workdir / "qmp.sock")
        self.rs_path = str(self.workdir / "remote-shell.sock")
        self.serial = str(self.workdir / "serial.log")
        self.proc: subprocess.Popen | None = None
        self.qmp: QMP | None = None
        self.rs = RemoteShell(self.rs_path)
        self.t_start = 0.0

    def argv(self) -> list[str]:
        a = [self.qemu, "-name", "veil-test", "-enable-kvm", "-machine", "q35,accel=kvm",
             "-cpu", "host", "-smp", str(self.cpus), "-m", self.memory,
             "-display", "none", "-vga", "virtio",
             "-serial", f"file:{self.serial}",
             "-qmp", f"unix:{self.qmp_path},server=on,wait=off",
             "-device", "virtio-serial",
             "-chardev", f"socket,id=rs,path={self.rs_path},server=on,wait=off",
             "-device", "virtserialport,chardev=rs,name=org.tails.remote_shell.0",
             "-device", "qemu-xhci,id=xhci",
             "-device", "usb-tablet,bus=xhci.0",
             "-rtc", "base=utc"]
        if self.firmware == "uefi":
            fw = find_ovmf()
            if not fw:
                raise RuntimeError("OVMF not found (set VEIL_OVMF_DIR)")
            vars_copy = self.workdir / "OVMF_VARS.fd"
            if not vars_copy.exists():
                shutil.copy(fw[1], vars_copy)
                os.chmod(vars_copy, 0o644)
            a += ["-drive", f"if=pflash,format=raw,readonly=on,file={fw[0]}",
                  "-drive", f"if=pflash,format=raw,file={vars_copy}"]
        if self.mode == "usb":
            snap = ",snapshot=on" if self.snapshot else ""
            a += ["-drive", f"file={self.image},if=none,id=stick,format=raw{snap}",
                  "-device", "usb-storage,bus=xhci.0,drive=stick,removable=on,bootindex=1"]
        else:
            a += ["-drive", f"file={self.image},media=cdrom,if=none,id=cd,readonly=on",
                  "-device", "ide-cd,drive=cd,bootindex=1"]
        if self.extra_disk:
            a += ["-drive", f"file={self.extra_disk},if=none,id=internal,format=qcow2",
                  "-device", "ahci,id=ahci", "-device", "ide-hd,drive=internal,bus=ahci.0"]
        if self.network:
            a += ["-nic", "user,model=virtio-net-pci"]
        else:
            a += ["-nic", "none"]
        if self.kernel:
            a += ["-kernel", self.kernel, "-initrd", self.initrd, "-append", self.cmdline]
        return a

    def start(self) -> None:
        for p in (self.qmp_path, self.rs_path):
            if os.path.exists(p):
                os.unlink(p)
        self.t_start = time.time()
        self.proc = subprocess.Popen(self.argv(), stdout=subprocess.DEVNULL,
                                     stderr=open(self.workdir / "qemu.log", "wb"))
        for _ in range(100):
            if os.path.exists(self.qmp_path):
                try:
                    self.qmp = QMP(self.qmp_path)
                    return
                except OSError:
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError((self.workdir / "qemu.log").read_text())
            time.sleep(0.1)
        raise TimeoutError("QMP not available")

    def screenshot(self, name: str) -> str:
        ppm = str(self.workdir / f"{name}.ppm")
        self.qmp.cmd("screendump", filename=ppm)
        png = str(self.workdir / f"{name}.png")
        for tool in (["magick", ppm, png], ["convert", ppm, png], ["pnmtopng", ppm]):
            if shutil.which(tool[0]):
                if tool[0] == "pnmtopng":
                    with open(png, "wb") as out:
                        subprocess.run(tool, stdout=out, check=False)
                else:
                    subprocess.run(tool, check=False)
                return png
        return ppm

    def elapsed(self) -> float:
        return time.time() - self.t_start

    def stop(self, timeout: float = 120) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.qmp.cmd("system_powerdown")
                self.proc.wait(timeout)
            except Exception:  # noqa: BLE001
                self.proc.kill()
                self.proc.wait()


def extract_boot_files(iso: str, outdir: str, entry: str = "live") -> tuple[str, str, str]:
    """Extract vmlinuz, initrd.img and the default kernel command line."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("vmlinuz", "initrd.img"):
        subprocess.run(["xorriso", "-osirrox", "on", "-indev", iso, "-extract", f"/live/{name}",
                        str(out / name)], check=True, capture_output=True)
        os.chmod(out / name, 0o644)
    subprocess.run(["xorriso", "-osirrox", "on", "-indev", iso, "-extract", "/EFI/debian/grub.cfg",
                    str(out / "grub.cfg")], check=True, capture_output=True)
    cfg = (out / "grub.cfg").read_text()
    m = re.search(r"--id '" + re.escape(entry) + r"' \{.*?linux /live/vmlinuz (.*?)\n", cfg, re.S)
    cmd = m.group(1) if m else "boot=live config"
    cmd = re.sub(r"\s*FSUUID=\$\{rootuuid\}", "", cmd)
    # Keep initrd=/live/initrd.img: Tails' initramfs-restore (memory erasure
    # at shutdown) locates the initrd through it, as on a real GRUB boot.
    cmd = cmd.strip()
    return str(out / "vmlinuz"), str(out / "initrd.img"), cmd


def first_partition_offset(img: str) -> int:
    """Byte offset of the first GPT partition (the FAT boot partition)."""
    import struct as _st
    with open(img, "rb") as f:
        f.seek(512)
        hdr = f.read(92)
        if hdr[:8] != b"EFI PART":
            raise RuntimeError("not a GPT disk image")
        entries_lba = _st.unpack_from("<Q", hdr, 72)[0]
        f.seek(entries_lba * 512)
        entry = f.read(128)
        first_lba = _st.unpack_from("<Q", entry, 32)[0]
    return first_lba * 512


def extract_boot_files_from_img(img: str, outdir: str, entry: str = "live") -> tuple[str, str, str]:
    """Same as extract_boot_files() but for a USB .img (FAT boot partition, mtools)."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    spec = f"{img}@@{first_partition_offset(img)}"
    for src, name in (("::/live/vmlinuz", "vmlinuz"), ("::/live/initrd.img", "initrd.img"),
                      ("::/EFI/debian/grub.cfg", "grub.cfg")):
        subprocess.run(["mcopy", "-n", "-o", "-i", spec, src, str(out / name)], check=True, capture_output=True)
    cfg = (out / "grub.cfg").read_text()
    m = re.search(r"--id '" + re.escape(entry) + r"' \{.*?linux /live/vmlinuz (.*?)\n", cfg, re.S)
    cmd = m.group(1) if m else "boot=live config"
    cmd = re.sub(r"\s*FSUUID=\$\{rootuuid\}", "", cmd)
    # Keep initrd=/live/initrd.img: Tails' initramfs-restore (memory erasure
    # at shutdown) locates the initrd through it, as on a real GRUB boot.
    cmd = cmd.strip()
    return str(out / "vmlinuz"), str(out / "initrd.img"), cmd


def boot_files(image: str, iso: str | None, outdir: str, entry: str = "live") -> tuple[str, str, str]:
    return extract_boot_files(iso, outdir, entry) if iso else extract_boot_files_from_img(image, outdir, entry)


ATSPI_LIB = r"""
import gi, re, sys, time
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi
def app_named(name, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = Atspi.get_desktop(0)
        for i in range(d.get_child_count()):
            a = d.get_child_at_index(i)
            if a is not None and a.get_name() == name:
                return a
        time.sleep(2)
    raise RuntimeError("application not found: " + name)
def find(node, pred, depth=0):
    if node is None or depth > 60:
        return None
    try:
        if pred(node):
            return node
        for i in range(node.get_child_count()):
            r = find(node.get_child_at_index(i), pred, depth + 1)
            if r is not None:
                return r
    except Exception:
        return None
    return None
def wait_find(app, pred, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        n = find(app, pred)
        if n is not None:
            return n
        time.sleep(1)
    raise RuntimeError("widget not found")
def by_role_name(role, pattern):
    rx = re.compile(pattern)
    return lambda n: n.get_role_name() == role and rx.search((n.get_name() or "").replace("_", "")) is not None
def click(node):
    Atspi.Action.do_action(node, 0)
def set_text(node, text):
    Atspi.EditableText.set_text_contents(node, text)
"""


def atspi_run(rs: RemoteShell, user: str, body: str, timeout: float = 180):
    """Run AT-SPI automation code (no dogtail/ponytail: works with any compositor)."""
    return rs.py(ATSPI_LIB + "\n" + body, user=user, timeout=timeout)


def login_via_greeter(rs: RemoteShell, title: str = "Welcome to Veil OS", button_re: str = "Start Veil OS",
                      timeout: float = 300, *, start_without_persistence: bool = False) -> None:
    """Click the Welcome Screen's Start button through AT-SPI as Debian-gdm."""
    body = f"""
app = app_named({title!r})
btn = wait_find(app, by_role_name("button", {button_re!r}))
click(btn)
if {start_without_persistence!r}:
    click(wait_find(app, by_role_name("button", "Start Without Persistent Storage")))
print("clicked", btn.get_name())
"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            exc, out, err = atspi_run(rs, "Debian-gdm", body)
            if exc is None and "clicked" in out:
                return
            last = (exc, out[-500:], err[-1500:])
        except (TimeoutError, RuntimeError, OSError) as e:
            last = e
        time.sleep(5)
    raise RuntimeError(f"could not click Start in the Welcome Screen: {last}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Boot a Veil OS image under QEMU/KVM")
    ap.add_argument("image")
    ap.add_argument("--workdir", default="vm-run")
    ap.add_argument("--mode", choices=["usb", "iso"], default="usb")
    ap.add_argument("--firmware", choices=["uefi", "bios"], default="uefi")
    ap.add_argument("--memory", default="4096")
    ap.add_argument("--iso", help="ISO to extract kernel/initrd from for autotest direct boot")
    ap.add_argument("--no-autotest", action="store_true")
    a = ap.parse_args()
    kernel = initrd = cmd = None
    if not a.no_autotest and a.iso:
        kernel, initrd, cmd = extract_boot_files(a.iso, os.path.join(a.workdir, "boot"))
        cmd += " autotest_never_use_this_option"
    vm = VM(a.image, a.workdir, mode=a.mode, firmware=a.firmware, memory=a.memory,
            kernel=kernel, initrd=initrd, cmdline=cmd)
    vm.start()
    print("started; QMP", vm.qmp_path, "remote shell", vm.rs_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
