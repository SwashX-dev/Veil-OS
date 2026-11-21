# Veil OS 1.1.0 validation

## Desktop failure diagnosis

On the alpha image in QEMU/KVM, the live user's labwc and XFCE panel ran
on the active seat with a separate, still-running `Debian-gdm` greeter.
Terminating labwc returned the active seat to that greeter. GDM logged
`GdmDisplay: Session never registered, failing`. The shell on that seat
was `gnome-shell --mode=gdm-tails` under `Debian-gdm`.

With the explicit compositor readiness registration loaded into a
fresh alpha VM, GDM closed the greeter after login. The active seat
belonged to the live user and had one labwc and one XFCE panel, with no
GNOME Shell process. This was a controlled fault reproduction. The
trigger of the original spontaneous hardware failure remains unknown.

## Release gates

The clean image build, signed artifacts, fresh image regressions,
physical graphics compatibility, and USB deployment are tracked
independently. Source-overlay checks use an unmodified alpha image
with selected source files loaded into its RAM overlay. They validate
individual changes before the clean image is available.

The PNY USB was inspected read-only. Its second partition is locked
LUKS2 Persistent Storage. A write requires preserving or backing up the
data and explicit approval for the identified device.

## Source-overlay verification

- All 41 Veil unit tests passed. Both changed Nix bundles resolved against
  the pinned nixpkgs input, and a dry run found their package closures in
  the configured binary cache. On the build host, AIDE initialized and
  checked a local baseline, ClamAV matched a fixture using a local
  signature, Bulk Extractor found a synthetic artifact, ExifTool read
  wallpaper metadata, and OpenSCAP reported its supported schemas.
- In a QEMU boot with Pixman, GDM handed off to XFCE. Super+T opened the
  terminal, Super+E opened Files, XFCE owned the StatusNotifier watcher,
  the session accepted a desktop notification, and Package Center
  opened. Forced loss of labwc ended the VM with QEMU exit status 0.
  A separate normal-rendering run clicked Package Center's Refresh list
  control, changed the virtual output scale and restored it, and
  launched Sound Settings.
- The offline APT fixture used a signed package index and a short-lived
  loopback repository on an isolated VM. Installing and removing a
  temporary package left no registration. Removing a registered package
  temporarily left its registration intact. The next boot restored the
  package and its dependency from the encrypted archive cache with no
  repository running. Persistent removal then left both the live package
  and registration absent, while preserving the installed dependency.
  The next unlocked boot did not restore the package. A malformed
  registration was rejected without replacing the file.
- Stopping APT during a fixture package's post-install and pre-removal
  scripts preserved the prior registry in both cases. `repair` followed
  by a retry completed each transaction. APT rejected a tampered
  archive before installation, and the registered fixture upgraded
  from version 1.0 to 2.0.
- A boot with locked storage accepted the Welcome Screen's explicit
  **Start Without Persistent Storage** choice. Persistent installation
  then failed with instructions to unlock the volume.
- The alpha report recorded 45.1 seconds to the Welcome Screen and 15.9
  seconds from Start to the panel. One Pixman source-overlay run
  recorded 49.1 and 12.3 seconds, respectively. The runs used different
  rendering and host load, so these measurements do not establish a
  performance improvement.

## Security audit notes

The overlay audit passed with no failing checks. It confirmed a read-only
SquashFS base, a RAM-backed writable overlay, RAM-only swap and the
existing agent sandbox checks. The candidate does not change the Tails
firewall rules, Tor transport, encryption or memory-erasure implementation.
Persistent package transactions have the same root authority as Debian
APT. Optional Nix tools run under the live user and have no automatic
service installation in this change.

A networked source-overlay boot started Tor Connection and Tor Browser,
bootstrapped Tor, and confirmed a Tor exit for the live user's traffic.
Direct UDP DNS and IPv6 probes failed as intended, and stopping Tor
blocked the live user's Internet access. Network Guard reported zero
failing checks and a warning about the Tails LAN policy, which allows
local-network traffic outside Tor.

These tests need to be repeated against the clean image. Physical GPU
and display behavior, idle locking, desktop icons, complete settings
coverage, Agent inference and the full toolkit matrix remain separate
validation items.
