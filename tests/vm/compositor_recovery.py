#!/usr/bin/env python3
"""Exercise startup recovery, compositor restart, lock restoration and crash-loop shutdown in a disposable VM."""

import argparse
import base64
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


SOURCE_FILES = (
    ("config/chroot_local-includes/usr/local/bin/veil-session", "/usr/local/bin/veil-session"),
    ("config/chroot_local-includes/usr/local/lib/veil/veil-desktop-start", "/usr/local/lib/veil/veil-desktop-start"),
    ("config/chroot_local-includes/usr/local/bin/veil-lock", "/usr/local/bin/veil-lock"),
)


def push_sources(vm, root):
    """Load the working-tree session scripts into the VM's RAM overlay (for images built without them)."""
    for local, remote in SOURCE_FILES:
        data = (root / local).read_bytes()
        run(vm, "truncate -s0 /tmp/veil-push")
        for start in range(0, len(data), 12000):
            run(vm, f"printf %s {base64.b64encode(data[start:start + 12000]).decode()} | base64 -d >> /tmp/veil-push")
        run(vm, f"install -m 755 /tmp/veil-push {remote} && rm /tmp/veil-push")


def labwc_pid(vm):
    return run(vm, "pgrep -u amnesia -x labwc", user="root").split()[0]


def lock(vm, unit):
    run(vm, "systemd-run --user --collect --property=Type=forking "
            f"--unit={unit} /usr/local/bin/veil-lock", user="amnesia")
    require(vm, "pgrep -u amnesia -x swaylock")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--img", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--push-sources", type=Path, help="repository root whose session scripts replace the image's")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    kernel, initrd, cmdline = veilvm.boot_files(str(args.img.resolve()), None, str(args.out / "boot"))
    vm = veilvm.VM(str(args.img.resolve()), str(args.out / "vm"), memory="4096", network=False,
                   kernel=kernel, initrd=initrd, cmdline=cmdline + " autotest_never_use_this_option")
    vm.start()
    try:
        vm.rs.wait_up(150)
        vm.rs.signal_ready()
        if args.push_sources:
            push_sources(vm, args.push_sources)
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

        # 1. Losing the compositor while unlocked restarts the desktop: no greeter, no poweroff.
        before = labwc_pid(vm)
        run(vm, "pkill -SEGV -u amnesia -x labwc")
        require(vm, f"p=$(pgrep -u amnesia -x labwc) && [ \"$p\" != {before} ] && pgrep -u amnesia -x xfce4-panel")
        require(vm, 'grep -zaq "^WLR_RENDERER=pixman$" /proc/$(pgrep -u amnesia -x labwc)/environ')
        require(vm, "! pgrep -x gnome-shell")
        require(vm, "[ $(pgrep -fc 'veil-panel-status$') = 1 ]")
        require(vm, "journalctl -b --no-pager -g 'compositor killed by signal SEGV' | grep -q SEGV")
        print(vm.screenshot("after-compositor-restart"), flush=True)
        print("Compositor crash while unlocked: desktop restarted with Pixman, no greeter.", flush=True)

        # 2. Losing it while locked restores the lock before anything else is shown.
        lock(vm, "veil-lock-crash-qa")
        require(vm, "ls /run/user/1000/veil-session.*/locked")
        before = labwc_pid(vm)
        run(vm, "pkill -SEGV -u amnesia -x labwc")
        require(vm, f"p=$(pgrep -u amnesia -x labwc) && [ \"$p\" != {before} ] && pgrep -u amnesia -x swaylock")
        print("Compositor crash while locked: lock restored on the new desktop.", flush=True)
        for char in TEST_PASSWORD:
            vm.qmp.cmd("send-key", keys=[{"type": "qcode", "data": char}], **{"hold-time": 60})
            time.sleep(0.09)
        vm.qmp.cmd("send-key", keys=[{"type": "qcode", "data": "ret"}])
        require(vm, "! pgrep -u amnesia -x swaylock && ! ls /run/user/1000/veil-session.*/locked")
        print("Unlocking clears the lock state.", flush=True)

        # 3. A crash loop ends in the normal shutdown path.
        for _ in range(8):
            if vm.proc.poll() is not None:
                break
            try:
                vm.rs.sh("pkill -KILL -u amnesia -x labwc", timeout=10)
            except Exception:
                pass
            time.sleep(4)
        if vm.proc.wait(timeout=180):
            raise RuntimeError("VM failed to power off cleanly after repeated compositor loss")
        print("Repeated compositor loss powered off the VM.", flush=True)
    finally:
        vm.stop(120)


if __name__ == "__main__":
    main()
