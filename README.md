# Veil OS

**Veil OS is an amnesic, Tor-enforcing live operating system for security work.
It is derived from [Tails](https://tails.net/).** It adds an XFCE desktop on
Wayland, a native eDEX-style terminal, a library of security tools, Nix package
bundles, a privacy monitor, and a fully local AI agent.

> [!WARNING]
> **Status: alpha.** This is an unofficial derivative. It is **not** made,
> reviewed or endorsed by the Tails project. Don't rely on it where your safety
> depends on it: use [official Tails](https://tails.net/) for that. Every
> requirement has a status (passed / failed / untested) in
> [docs/veil/TEST-REPORT.md](docs/veil/TEST-REPORT.md).

## What Veil keeps from Tails (unchanged)

- Live USB boot with a read-only system image and a RAM-backed writable layer
  (amnesia). Nothing is written to the computer's disks.
- The Tails Welcome Screen, Tor Connection, Tor Browser (Flatpak + AppArmor),
  the Tails firewall (ferm, default deny), DNS through Tor, IPv6 disabled, and
  MAC address anonymization.
- Encrypted Persistent Storage (LUKS2), which you can unlock or skip at every
  boot.
- Memory erasure at shutdown, and emergency shutdown when the USB stick is
  removed.
- Kernel hardening sysctls, AppArmor, and Tails security advisories
  (`tails-security-check`).

Veil does **not** guarantee anonymity, and no system removes every forensic
trace. See [docs/veil/THREAT-MODEL.md](docs/veil/THREAT-MODEL.md).

## What Veil adds

| Area | What | Docs |
|---|---|---|
| Desktop | XFCE 4.20 components on the **labwc Wayland** compositor (Tails ships no X.Org, and neither does Veil), plus a dark Veil theme | [DESKTOP](docs/veil/DESKTOP.md) |
| Terminal | **Veil Console**: an eDEX-style terminal written natively in GTK3 + VTE, with no Electron and no network access. xfce4-terminal stays installed as the fallback. | [CONSOLE](docs/veil/CONSOLE.md) |
| Privacy | **Veil Network Guard** (`veil privacy status`, GUI): checks the firewall, Tor circuits, DNS/IPv6/UDP leaks, connections, MAC anonymization, and the agent/Nix policy | [NETWORK-GUARD](docs/veil/NETWORK-GUARD.md) |
| AI | **Veil Agent** (`veil agent`, GUI): llama.cpp runs locally in an empty network namespace. It is offline by default; tools run in a bubblewrap + seccomp sandbox, and network access is a separate grant. | [AGENT](docs/veil/AGENT.md) |
| Tools | Light Debian tools in the base image; heavy tools as opt-in **Nix bundles**. Each tool has a documented network policy. | [TOOLS](docs/veil/TOOLS.md) |
| Packages | `veil install/remove/search/update` runs single-user Nix as the live user, so its downloads always go through Tor. | [NIX](docs/veil/NIX.md) |
| Labs | `veil lab`: offline network namespaces for tools that can't use Tor. Labs have no route to the Internet. | [TOOLS](docs/veil/TOOLS.md#offline-labs) |
| Persistence | New Persistent Storage features: *Nix Packages* and *Veil Desktop Settings* | [PERSISTENCE](docs/veil/PERSISTENCE.md) |

## Quick start

1. Download `veil-amd64-<version>.img` from [Releases](https://github.com/SwashX-dev/Veil-OS/releases).
   Verify it against `SHA256SUMS` and the OpenPGP signature
   ([docs/veil/VERIFY.md](docs/veil/VERIFY.md)).
2. Write it to a USB stick of 8 GB or more: `sudo dd if=veil-amd64-<version>.img of=/dev/sdX bs=16M oflag=direct status=progress conv=fsync`.
   **Double-check `/dev/sdX`: everything on that device is erased.**
3. Boot from the stick. In the Welcome Screen, optionally unlock Persistent
   Storage, then click **Start Veil OS** and connect to Tor.
4. Open a terminal and run `veil privacy status`, `veil tools`, `veil agent models list`.

## Building

Veil uses the **official, unmodified Tails build system** (`rake build`:
Vagrant + libvirt + live-build). See [docs/veil/BUILDING.md](docs/veil/BUILDING.md).

```sh
git clone https://github.com/SwashX-dev/Veil-OS.git && cd Veil-OS
git submodule update --init
rake build
```

## Repository layout

- Upstream Tails source, full history. The fork point is the signed tag
  `7.17.1`. The `upstream` remote is https://gitlab.tails.net/tails/tails.
- Veil additions live mostly in `config/chroot_local-includes/usr/lib/python3/dist-packages/veil/`,
  `config/chroot_local-hooks/5[7-9]-veil-*`, `config/chroot_local-packageslists/veil.list`
  and `branding/`.
- Every change to an upstream file is listed and justified in
  [docs/veil/MODIFICATIONS.md](docs/veil/MODIFICATIONS.md).

## License and attribution

Veil OS is free software under the **GNU GPL v3 or later**, like Tails. Tails is
Copyright © Tails developers. Veil OS additions are Copyright © the Veil OS
contributors. Third-party components keep their own licenses: see
[NOTICE.md](NOTICE.md) and [README.tails.md](README.tails.md). "Tails" is used
only to describe where Veil comes from.
