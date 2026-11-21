#!/usr/bin/env python3
"""Boot a VM, load source changes into its RAM overlay, log in, and stay up.

Control it with `vmctl.py sh|py|shot WORKDIR ...`. Stop with `vmctl.py stop WORKDIR`.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import overlay  # noqa: E402
import veilvm  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--overlay-base")
    ap.add_argument("--debs", nargs="*", default=[])
    ap.add_argument("--sudoers", nargs="*", default=[])
    ap.add_argument("--cmdline-extra", default="")
    ap.add_argument("--memory", default="6144")
    ap.add_argument("--vpn-network", action="store_true", help="198.18.0.0/24 user network (VPN tests)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[2]
    kernel, initrd, cmdline = veilvm.boot_files(a.img, None, str(out / "boot"))
    vm = veilvm.VM(a.img, str(out / "vm"), memory=a.memory, kernel=kernel, initrd=initrd,
                   cmdline=f"{cmdline} {a.cmdline_extra} autotest_never_use_this_option")
    if a.vpn_network:
        base_argv = vm.argv

        def argv():
            av = base_argv()
            i = av.index("user,model=virtio-net-pci")
            av[i] = "user,model=virtio-net-pci,net=198.18.0.0/24,host=198.18.0.2,dns=198.18.0.3"
            return av
        vm.argv = argv
    vm.start()
    vm.rs.wait_up(300)
    vm.rs.signal_ready()
    if a.overlay_base:
        log = overlay.apply(vm.rs, repo, a.overlay_base, [Path(d) for d in a.debs], [Path(s) for s in a.sudoers])
        print("\n".join(log), flush=True)
    veilvm.login_via_greeter(vm.rs)
    print("READY", flush=True)
    while vm.proc.poll() is None:
        time.sleep(2)


if __name__ == "__main__":
    main()
