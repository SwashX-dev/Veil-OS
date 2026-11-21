# Building Veil OS

Veil uses the **official Tails build system unchanged**: `rake build` drives
a Vagrant + libvirt builder VM that runs live-build with Tails' pinned APT
snapshots (deb.tails.net). Follow https://tails.net/contribute/build/.

## Requirements

Debian trixie (or newer) with KVM, sudo, about 20 GB in $HOME, 5 GB in /tmp
and 5–20 GB in /var/lib. Install:

```sh
sudo apt install psmisc git dpkg-dev rake libvirt-daemon-system dnsmasq-base ebtables \
  faketime jq pigz qemu-system-x86 qemu-utils vagrant vagrant-libvirt vmdb2
for g in kvm libvirt libvirt-qemu; do sudo adduser "$(whoami)" "$g"; done
```

## Build

```sh
git clone https://github.com/SwashX-dev/Veil-OS.git && cd Veil-OS
git submodule update --init
git fetch https://gitlab.tails.net/tails/tails.git stable:refs/remotes/origin/stable   # see note
TAILS_BUILD_OPTIONS="nomergebasebranch" rake build && rake vm:halt
bin/veil-rename-artifacts   # tails-amd64-* -> veil-amd64-<veil version>.*
```

Notes from the reference build (rootless NixOS host, nested Debian VM):

- The Rakefile needs an `origin/<base_branch>` ref (`origin/stable`) to
  exist, even with `nomergebasebranch`.
- Building from a branch based on `stable` requires the last *two* Tails
  versions in `debian/changelog` to exist as git tags (`7.17.1` and `7.17`).
  This repository carries those upstream tags; if you mirror it, fetch them
  too (`git fetch upstream 'refs/tags/7.17*:refs/tags/7.17*'`).
- Run long builds as a system service (e.g. `systemd-run`) so that
  session or watchdog restarts on a loaded host cannot kill them.
- libvirt's QEMU user must be able to read the source tree. If your home is
  mode 700, use `chmod o+rx $HOME` (or build elsewhere).
- Upstream hosts moved: GitLab is gitlab.tails.net, APT snapshots are
  deb.tails.net.
- The llama.cpp hook downloads its pinned tarball through the builder's
  apt-cacher-ng (`HTTPS///` remap) and checks its SHA-256 before using it.

## Release

`bin/veil-release` computes `SHA256SUMS`, signs it with the Veil release key
(`docs/veil/veil-release-key.asc`), and uploads to a GitHub Release with
`gh`. See [VERIFY.md](VERIFY.md).
