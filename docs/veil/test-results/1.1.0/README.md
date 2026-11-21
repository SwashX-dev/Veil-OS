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
