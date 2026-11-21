#!/usr/bin/env python3
"""Persistent Storage + amnesia test (two boots of a writable USB image copy).

Boot 1: create Persistent Storage (Tails D-Bus API, as the Welcome Screen /
Persistent Storage app would), activate the Veil "Nix Packages" feature,
write a persistent marker, an amnesic marker and a /nix marker.
Boot 2: unlock in the Welcome Screen (dogtail, as the Tails test suite does),
check persistent files survived and amnesic files did not.
Boot 3: start WITHOUT unlocking: persistent files must be absent.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import veilvm  # noqa: E402

PASS = "veil-persistence-test-passphrase-81723"
R = []


def rec(rid, name, ok, ev):
    R.append({"id": rid, "name": name, "status": "PASS" if ok else "FAIL", "evidence": ev.strip()[:2000]})
    print(f"[{'PASS' if ok else 'FAIL':8}] {rid} {name}: {ev.strip().splitlines()[0][:140] if ev.strip() else ''}",
          flush=True)


def boot(img, out, name, kernel, initrd, cmd, memory):
    vm = veilvm.VM(img, str(out / name), mode="usb", firmware="uefi", memory=memory, cpus=2,
                   kernel=kernel, initrd=initrd, cmdline=cmd + " autotest_never_use_this_option",
                   snapshot=False, network=False)
    vm.start()
    vm.rs.wait_up(300)
    vm.rs.signal_ready()
    deadline = time.time() + 300
    while time.time() < deadline:
        rc, _, _ = vm.rs.sh("pgrep -f tails-greeter >/dev/null")
        if rc == 0:
            break
        time.sleep(3)
    time.sleep(8)
    return vm


def unlock_in_greeter(vm, title):
    code = f"""
import time
from dogtail.tree import root
app = root.application({title!r})
e = app.findChild(lambda n: n.roleName == 'password text', retry=True)
e.text = {PASS!r}
app.findChild(lambda n: n.roleName == 'button' and 'Unlock' in n.name, retry=True).doActionNamed('click')
print('unlock-clicked')
"""
    exc, out, err = vm.rs.py(code, user="Debian-gdm", timeout=180)
    return exc is None and "unlock-clicked" in out, f"{exc} {out} {err}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--out", default="vm-persistence")
    ap.add_argument("--memory", default="4096")
    ap.add_argument("--title", default="Welcome to Veil OS")
    ap.add_argument("--button", default="Start Veil OS")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    work = out / "usb-work.img"
    shutil.copyfile(a.img, work)
    subprocess.run(["truncate", "-s", "8G", str(work)], check=True)  # an 8 GB "USB stick"
    kernel, initrd, cmd = veilvm.extract_boot_files(a.iso, str(out / "boot"))

    # ---- Boot 1: create
    vm = boot(str(work), out, "boot1", kernel, initrd, cmd, a.memory)
    try:
        veilvm.login_via_greeter(vm.rs, title=a.title, button_re=a.button)
        time.sleep(20)
        gd = "gdbus call --system --dest org.boum.tails.PersistentStorage"
        rc, o, e = vm.rs.sh(f"{gd} --object-path /org/boum/tails/PersistentStorage "
                            f"--method org.boum.tails.PersistentStorage.Create '{PASS}'", timeout=900)
        rc2, props, _ = vm.rs.sh(f"{gd} --object-path /org/boum/tails/PersistentStorage --method "
                                 "org.freedesktop.DBus.Properties.GetAll org.boum.tails.PersistentStorage")
        rec("9.create", "Persistent Storage can be created (LUKS2)", rc == 0 and "'IsUnlocked': <true>" in props,
            f"rc={rc} {o} {e}\n{props}")
        rc, o, e = vm.rs.sh(f"{gd} --object-path /org/boum/tails/PersistentStorage/Features/VeilNix "
                            "--method org.boum.tails.PersistentStorage.Feature.Activate", timeout=300)
        rc3, mnt, _ = vm.rs.sh("findmnt -no SOURCE,TARGET /nix; cryptsetup status TailsData_unlocked | head -3; "
                               "cat /live/persistence/TailsData_unlocked/persistence.conf")
        rec("9.veil_nix_feature", "Veil 'Nix Packages' feature activates (bind mount of /nix)",
            rc == 0 and "/nix" in mnt, f"rc={rc} {o} {e}\n{mnt}")
        rc, o, e = vm.rs.sh("echo PERSISTENT-MARKER > /home/amnesia/Persistent/marker.txt && "
                            "echo AMNESIC-MARKER > /home/amnesia/amnesic.txt && echo NIX-MARKER > /nix/marker.txt && "
                            "ls -la /home/amnesia/Persistent/ /nix/ && sync", user="amnesia")
        rec("9.write", "Write test files", rc == 0, o + e)
    finally:
        vm.stop(180)

    # ---- Boot 2: unlock and verify
    vm = boot(str(work), out, "boot2", kernel, initrd, cmd, a.memory)
    try:
        ok, ev = unlock_in_greeter(vm, a.title)
        deadline = time.time() + 180
        unlocked = False
        while time.time() < deadline:
            rc, o, e = vm.rs.sh("findmnt /live/persistence/TailsData_unlocked >/dev/null")
            if rc == 0:
                unlocked = True
                break
            time.sleep(3)
        vm.screenshot("boot2-unlocked")
        rec("9.unlock", "Persistent Storage unlocks from the Welcome Screen", ok and unlocked, ev)
        veilvm.login_via_greeter(vm.rs, title=a.title, button_re=a.button)
        time.sleep(20)
        rc, o, e = vm.rs.sh("cat /home/amnesia/Persistent/marker.txt; cat /nix/marker.txt; "
                            "test -e /home/amnesia/amnesic.txt && echo AMNESIC-SURVIVED || echo AMNESIC-GONE",
                            user="amnesia")
        rec("9.persisted", "Persistent files survive reboot (Persistent folder + /nix)",
            "PERSISTENT-MARKER" in o and "NIX-MARKER" in o, o + e)
        rec("2.amnesia_reboot", "Files outside Persistent Storage are gone after reboot", "AMNESIC-GONE" in o, o + e)
    finally:
        vm.stop(180)

    # ---- Boot 3: no unlock
    vm = boot(str(work), out, "boot3", kernel, initrd, cmd, a.memory)
    try:
        veilvm.login_via_greeter(vm.rs, title=a.title, button_re=a.button)
        time.sleep(15)
        rc, o, e = vm.rs.sh("ls /home/amnesia/Persistent 2>&1 | head -3; findmnt /live/persistence/TailsData_unlocked "
                            "|| echo NOT-UNLOCKED; test -e /nix/marker.txt && echo NIX-VISIBLE || echo NIX-HIDDEN")
        rec("9.boot_without_unlock", "Booting without unlocking works and keeps persistent data hidden",
            "NOT-UNLOCKED" in o and "NIX-HIDDEN" in o and "PERSISTENT-MARKER" not in o, o + e)
    finally:
        vm.stop(180)
    (out / "results.json").write_text(json.dumps({"results": R}, indent=1))
    work.unlink(missing_ok=True)
    return 0 if all(r["status"] == "PASS" for r in R) else 1


if __name__ == "__main__":
    sys.exit(main())
