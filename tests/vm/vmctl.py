#!/usr/bin/env python3
"""Interactive control of a test VM (debugging aid for tests/vm).

    vmctl.py boot IMG WORKDIR [--iso ISO] [--memory MB] [--no-network] [--bios]
    vmctl.py sh WORKDIR [--user USER] -- COMMAND
    vmctl.py py WORKDIR [--user USER] -- PYTHON_CODE
    vmctl.py shot WORKDIR NAME
    vmctl.py stop WORKDIR
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import veilvm  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("boot")
    b.add_argument("img")
    b.add_argument("workdir")
    b.add_argument("--iso")
    b.add_argument("--memory", default="6144")
    b.add_argument("--no-network", action="store_true")
    b.add_argument("--no-autotest", action="store_true")
    b.add_argument("--bios", action="store_true")
    b.add_argument("--entry", default="live", help="GRUB entry id: live, livefailsafe, livenonremovable")
    for name in ("sh", "py"):
        s = sub.add_parser(name)
        s.add_argument("workdir")
        s.add_argument("--user", default="root")
        s.add_argument("--timeout", type=float, default=120)
        s.add_argument("code", nargs=argparse.REMAINDER)
    s = sub.add_parser("shot")
    s.add_argument("workdir")
    s.add_argument("name")
    s = sub.add_parser("stop")
    s.add_argument("workdir")
    a = ap.parse_args()

    if a.cmd == "boot":
        kernel = initrd = cmd = None
        if not a.no_autotest:
            kernel, initrd, cmd = veilvm.boot_files(a.img, a.iso, os.path.join(a.workdir, "boot"), a.entry)
            print("cmdline:", cmd, file=sys.stderr)
            cmd += " autotest_never_use_this_option"
        vm = veilvm.VM(a.img, a.workdir, mode="usb", firmware="bios" if a.bios else "uefi",
                       memory=a.memory, kernel=kernel, initrd=initrd, cmdline=cmd, network=not a.no_network)
        argv = vm.argv()
        for p in (vm.qmp_path, vm.rs_path):
            if os.path.exists(p):
                os.unlink(p)
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=open(Path(a.workdir) / "qemu.log", "wb"),
                                start_new_session=True)
        (Path(a.workdir) / "qemu.pid").write_text(str(proc.pid))
        t0 = time.time()
        if a.no_autotest:
            print(json.dumps({"pid": proc.pid}))
            return 0
        vm.rs.wait_up(300)
        vm.rs.signal_ready()
        print(json.dumps({"pid": proc.pid, "remote_shell_after_s": round(time.time() - t0, 1)}))
        return 0

    rs = veilvm.RemoteShell(os.path.join(a.workdir, "remote-shell.sock"))
    if a.cmd == "sh":
        code = " ".join(a.code[1:] if a.code and a.code[0] == "--" else a.code)
        rc, out, err = rs.sh(code, user=a.user, timeout=a.timeout)
        sys.stdout.write(out)
        sys.stderr.write(err)
        return rc
    if a.cmd == "py":
        code = " ".join(a.code[1:] if a.code and a.code[0] == "--" else a.code)
        exc, out, err = rs.py(code, user=a.user, timeout=a.timeout)
        print(out, err, exc)
        return 0 if exc is None else 1
    if a.cmd == "shot":
        q = veilvm.QMP(os.path.join(a.workdir, "qmp.sock"))
        ppm = os.path.join(a.workdir, f"{a.name}.ppm")
        q.cmd("screendump", filename=ppm)
        png = os.path.join(a.workdir, f"{a.name}.png")
        subprocess.run(["magick", ppm, png], check=False)
        os.unlink(ppm)
        print(png)
        return 0
    if a.cmd == "stop":
        try:
            q = veilvm.QMP(os.path.join(a.workdir, "qmp.sock"))
            q.cmd("quit")
        except OSError:
            pid = int((Path(a.workdir) / "qemu.pid").read_text())
            os.kill(pid, signal.SIGTERM)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
