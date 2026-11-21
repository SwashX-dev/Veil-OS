#!/usr/bin/env python3
"""Exercise software startup recovery and fail-closed lock in a disposable VM."""

import argparse
from pathlib import Path
import shlex
import time

import veilvm
from run_tests import wait_for

# This password exists only in the disposable VM's RAM overlay.
TEST_PASSWORD = "veiltestonly"
RENDERER_FIXTURE = """#!/bin/sh
[ "${WLR_RENDERER:-}" = pixman ] || exit 23
exec /usr/bin/labwc "$@"
"""


def run(vm, command, user="root"):
    code, stdout, stderr = vm.rs.sh(command, user=user, timeout=30)
    if code:
        raise RuntimeError(f"Guest command failed ({code}): {stdout}{stderr}")
    return stdout


def require(vm, command):
    ok, output = wait_for(vm, command, 45, interval=1)
    if not ok:
        raise RuntimeError(f"Guest condition was not reached: {command}\n{output}")
    return output


def lock(vm, unit):
    run(vm, "systemd-run --user --collect --property=Type=forking "
            f"--unit={unit} /usr/local/bin/veil-lock", user="amnesia")
    require(vm, "pgrep -u amnesia -x swaylock")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--img", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    kernel, initrd, cmdline = veilvm.boot_files(str(args.img.resolve()), None, str(args.out / "boot"))
    vm = veilvm.VM(str(args.img.resolve()), str(args.out / "vm"), memory="4096", network=False,
                   kernel=kernel, initrd=initrd, cmdline=cmdline + " autotest_never_use_this_option")
    vm.start()
    try:
        vm.rs.wait_up(150)
        vm.rs.signal_ready()
        run(vm, f"printf %s {shlex.quote(RENDERER_FIXTURE)} > /tmp/veil-renderer-fixture && "
                "chmod 755 /tmp/veil-renderer-fixture && "
                "sed -i 's@/usr/bin/labwc --startup@/tmp/veil-renderer-fixture --startup@' "
                "/usr/local/bin/veil-session")
        veilvm.login_via_greeter(vm.rs)
        require(vm, "pgrep -u amnesia -x xfce4-panel && ! pgrep -x gnome-shell")
        require(vm, 'grep -zaq "^WLR_RENDERER=pixman$" /proc/$(pgrep -u amnesia -x labwc)/environ')
        print("Startup failed on the first try and recovered with Pixman.", flush=True)

        run(vm, f"printf '%s\\n' {shlex.quote('amnesia:' + TEST_PASSWORD)} | chpasswd")
        lock(vm, "veil-lock-qa")
        for char in TEST_PASSWORD:
            vm.qmp.cmd("send-key", keys=[{"type": "qcode", "data": char}], **{"hold-time": 60})
            time.sleep(0.09)
        vm.qmp.cmd("send-key", keys=[{"type": "qcode", "data": "ret"}])
        require(vm, "! pgrep -u amnesia -x swaylock")
        print("Swaylock accepted the test password.", flush=True)

        lock(vm, "veil-lock-crash-qa")
        run(vm, "pkill -KILL -u amnesia -x labwc")
        if vm.proc.wait(timeout=120):
            raise RuntimeError("VM failed to power off cleanly after compositor loss")
        print("Compositor loss while locked powered off the VM.", flush=True)
    finally:
        vm.stop(120)


if __name__ == "__main__":
    main()
