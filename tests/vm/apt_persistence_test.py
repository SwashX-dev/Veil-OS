#!/usr/bin/env python3
"""Test Debian Additional Software across four boots of a disposable image.

Create the signed fixture with make_apt_fixture.sh on Debian first. This VM
has no network adapter. The fixture repository listens on its loopback
address during boot 1 only. Run this script against a freshly built image.
"""

import argparse
import base64
import json
from pathlib import Path
import shutil
import shlex
import time

import veilvm
from persistence_test import PASS, unlock_in_greeter

results = []
PERSIST = '/live/persistence/TailsData_unlocked'
REG = f'{PERSIST}/live-additional-software.conf'
ROOT = 'veil-vm-test-root'
DEP = 'veil-vm-test-dep'
EXTRA = 'veil-vm-test-extra'
APT = '/usr/local/bin/apt'
CHECK = "dpkg-query -W -f='${Status}'"
CLI = 'veil packages persistent'
APT_UPDATE = ("/usr/bin/apt-get -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/veil-vm-test.list "
              "-o Dir::Etc::sourceparts=- -o APT::Update::Error-Mode=any update")

def check(name, res, code=0, contains=None):
    rc, out, err = res
    ok = (rc == code) and (contains is None or contains in out+err)
    print(name, 'PASS' if ok else 'FAIL', rc, out[-900:].strip(), err[-500:].strip(), flush=True)
    results.append({'test': name, 'pass': ok, 'code': rc, 'stdout': out[-3000:], 'stderr': err[-3000:]})
    (work/'results.json').write_text(json.dumps(results, indent=2))
    if not ok:
        raise RuntimeError(name)

def wait(rs, command, limit=90):
    deadline = time.monotonic()+limit
    while time.monotonic() < deadline:
        result = rs.sh(command, timeout=5)
        if result[0] == 0:
            return result
        time.sleep(1)
    raise RuntimeError((command, result))

def boot(num, unlock=False, login=True):
    vm = veilvm.VM(str(copy), str(work/f'boot{num}'), firmware='uefi', memory='4096', network=False,
                  kernel=kernel, initrd=initrd, cmdline=cmdline + ' autotest_never_use_this_option',
                  snapshot=False)
    vm.start()
    try:
        vm.rs.wait_up(120)
        vm.rs.signal_ready()
        rs = vm.rs
        check(f'{num}.fixture-source', rs.sh("mkdir -p /etc/apt/sources.list.d; "
             "echo 'deb [signed-by=/usr/share/keyrings/veil-vm-fixture.gpg] http://127.0.0.1:18765/ ./' "
             "> /etc/apt/sources.list.d/veil-vm-test.list"))
        if unlock:
            wait(rs, "test -s /tmp/gdm-env")
            ok, evidence = unlock_in_greeter(vm, 'Welcome to Veil OS')
            check(f'{num}.unlock', (0 if ok else 1, evidence, ''))
            wait(rs, f'mountpoint -q {PERSIST}')
        if login:
            start = time.monotonic()
            veilvm.login_via_greeter(rs)
            session = 'pgrep -u amnesia -x xfce4-panel && ! pgrep -x gnome-shell && pgrep -u amnesia -x labwc'
            check(f'{num}.xfce-ready', wait(rs, session))
            print(f'{num}.greeter-to-desktop-seconds={time.monotonic()-start:.1f}', flush=True)
        return vm
    except BaseException:
        vm.stop(30)
        raise

def test_interrupted_transactions(rs):
    pkg = "veil-vm-test-interrupted"
    for action, hook, should_exist in (("install", "postinst", False), ("remove", "prerm", True)):
        unit = f"veil-apt-interrupted-{action}"
        hold = f"/run/veil-apt-hold-{hook}"
        entered = f"/run/veil-apt-entered-{hook}"
        check(f"1.start-interrupted-{action}",
              rs.sh(f"touch {hold} && systemd-run --unit={unit} {APT} {action} {pkg} -Pr -y"))
        wait(rs, f"test -e {entered}")
        check(f"1.interrupt-{action}", rs.sh(f"systemctl stop {unit} && rm {hold}"))
        rc, out, err = rs.sh(f"{CLI} list")
        check(f"1.registration-after-interrupted-{action}",
              (0 if rc == 0 and (pkg in out.splitlines()) == should_exist else 1, out, err))
        check(f"1.repair-after-interrupted-{action}", rs.sh(f"{CLI} repair", timeout=90))
        check(f"1.retry-{action}", rs.sh(f"{APT} {action} {pkg} -Pr -y", timeout=90),
              0, "saved and verified")


def run_tests():
    vm = boot(1)
    rs = vm.rs
    try:
        check('1.no-persistence', rs.sh(f'{APT} install {ROOT} -Pr -y'), 1, 'Create encrypted Persistent Storage')
        check('1.create-luks', rs.sh('gdbus call --timeout 150 --system --dest org.boum.tails.PersistentStorage '
              '--object-path /org/boum/tails/PersistentStorage --method org.boum.tails.PersistentStorage.Create '
              + shlex.quote(PASS), timeout=180))
        check('1.active-feature', wait(rs, '/usr/local/lib/tpscli is-active AdditionalSoftware'))
        payload = base64.b64encode(fixture).decode()
        check('1.transfer-fixture', rs.sh(f"printf %s {payload} | base64 -d > /tmp/veil-test-fixture.tar.gz"))
        check('1.fixture', rs.sh('mkdir -p /tmp/fixture && tar -xf /tmp/veil-test-fixture.tar.gz -C /tmp/fixture && '
                                'install -m644 /tmp/fixture/fixture.gpg /usr/share/keyrings/veil-vm-fixture.gpg'))
        check('1.fixture-server', rs.sh('systemd-run --unit=veil-vm-apt-fixture --working-directory=/tmp/fixture '
                                        '/usr/bin/python3 -m http.server 18765 --bind 127.0.0.1'))
        check('1.fixture-loopback', rs.sh('iptables -I OUTPUT 1 -o lo -d 127.0.0.1 '
                                         '-p tcp --dport 18765 -j ACCEPT'))
        wait(rs, 'curl --noproxy "*" --silent --fail http://127.0.0.1:18765/InRelease >/dev/null')
        check('1.signed-apt-index', rs.sh(APT_UPDATE, timeout=60), 0, 'InRelease')
        fixture_pkg = f"/tmp/fixture/{EXTRA}.deb"
        check("1.tamper-fixture", rs.sh(f"cp {fixture_pkg} /tmp/original.deb && "
              f"printf X | dd of={fixture_pkg} bs=1 conv=notrunc status=none"))
        check("1.package-hash-enforced", rs.sh(f"{APT} install {EXTRA} -Pr -y"), 100, "Hash Sum mismatch")
        check("1.invalid-package-not-registered", rs.sh(f'list=$({CLI} list) && test -z "$list"'))
        check("1.restore-fixture", rs.sh(f"mv /tmp/original.deb {fixture_pkg}"))
        test_interrupted_transactions(rs)
        check('1.temporary-install', rs.sh(f'{APT} install -y {EXTRA}'), 0, 'Setting up')
        check('1.temporary-install-not-registered', rs.sh(f'list=$({CLI} list) && test -z "$list"'), 0)
        check('1.temporary-remove', rs.sh(f'{APT} remove -y {EXTRA}'), 0, 'Removing')
        check('1.persistent-install', rs.sh(f'{APT} install {ROOT} -Pr -y', timeout=90), 0, 'saved and verified')
        check('1.registration-visible-to-user', rs.sh(f'{CLI} list', user='amnesia'), 0, ROOT)
        check('1.dependency-protected', rs.sh(f'{APT} remove {DEP} -Pr -y'), 1, 'would also remove')
        check("1.publish-updated-fixture", rs.sh(
            "cp /tmp/fixture/upgrade/* /tmp/fixture/ && " + APT_UPDATE, timeout=75))
        check("1.upgrade-persistent", rs.sh(f"{APT} install {ROOT} -Pr -y", timeout=90), 0, "saved and verified")
        check("1.upgraded-version", rs.sh(f"/usr/bin/dpkg-query -W {ROOT}"), 0, "2.0")
        check('1.temporary-removal-of-registered', rs.sh(f'{APT} remove -y {ROOT}'), 0, 'Removing')
        check('1.registration-preserved', rs.sh(f'{CLI} list'), 0, ROOT)
        check('1.archive-cached', rs.sh(f'ls /var/cache/apt/archives/{ROOT}_*.deb /var/cache/apt/archives/{DEP}_*.deb'))
    finally:
        vm.stop(180)
        print('1.shutdown:', vm.proc.returncode, flush=True)
    vm = boot(2, unlock=True)
    rs = vm.rs
    try:
        check('2.cached-offline-restore', wait(rs, f'{CHECK} {ROOT} | grep -q "install ok installed"'))
        check("2.upgraded-version-restored", rs.sh(f"/usr/bin/dpkg-query -W {ROOT}"), 0, "2.0")
        check('2.dependency-restored', rs.sh(f'{CHECK} {DEP}'), 0, 'install ok installed')
        check('2.temporary-package-gone', rs.sh(f'{CHECK} {EXTRA}'), 1)
        check('2.verify-registration', rs.sh(f'{CLI} verify'), 0, 'immutable image')
        check('2.persistent-remove', rs.sh(f'{APT} remove {ROOT} -Pr -y'), 0, 'saved and verified')
        check('2.dependency-preserved', rs.sh(f'{CHECK} {DEP}'), 0, 'install ok installed')
        check('2.registration-removed', rs.sh(f'list=$({CLI} list) && test -z "$list"'))
    finally:
        vm.stop(180)
        print('2.shutdown:', vm.proc.returncode, flush=True)
    vm = boot(3, unlock=True)
    rs = vm.rs
    try:
        check('3.removed-package-not-restored', rs.sh(f'{CHECK} {ROOT}'), 1)
        check('3.empty-registration', rs.sh(f'list=$({CLI} list) && test -z "$list"'))
        check('3.corrupt-registration-rejected', rs.sh(f'echo --allow-unauthenticated > {REG}; {CLI} verify'), 1, 'Invalid')
        check('3.corrupted-file-preserved', rs.sh(f'grep -qx -- --allow-unauthenticated {REG}; rc=$?; truncate -s0 {REG}; exit $rc'))
    finally:
        vm.stop(180)
        print('3.shutdown:', vm.proc.returncode, flush=True)
    vm = boot(4, login=False)
    rs = vm.rs
    try:
        wait(rs, '/usr/local/lib/tpscli is-created && ! /usr/local/lib/tpscli is-unlocked')
        check('4.locked-storage-rejected', rs.sh(f'{APT} install {ROOT} -Pr -y'), 1, 'is locked')
    finally:
        vm.stop(180)
        print('4.shutdown:', vm.proc.returncode, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--img", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New test output directory")
    args = parser.parse_args()

    global work, copy, fixture, kernel, initrd, cmdline
    work = args.out.resolve()
    work.mkdir()
    copy = work / "virtual-usb.img"
    shutil.copyfile(args.img, copy)
    with copy.open("r+b") as image:
        image.truncate(8 * 1024**3)
    kernel, initrd, cmdline = veilvm.boot_files(str(args.img.resolve()), None, str(work / "boot"))
    fixture = args.fixture.read_bytes()
    try:
        run_tests()
    finally:
        copy.unlink()


if __name__ == "__main__":
    main()
