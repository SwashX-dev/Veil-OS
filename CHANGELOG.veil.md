# Veil OS changelog

Upstream Tails changes are in `Changelog` / `debian/changelog`.

## 0.1.0-alpha.1 (2026-11-21)

Based on Tails 7.17.1 (signed tag, commit a95f1e8).

- Desktop: XFCE 4.20 components on the labwc Wayland compositor, replacing
  the GNOME Shell user session. GDM and the Tails Welcome Screen are unchanged
  apart from branding. No X.Org.
- Branding: Veil logo, boot splash, GRUB/syslinux menus, Welcome Screen title,
  wallpaper, GTK/labwc/notification themes, icons, os-release identity.
- Veil Console: a native GTK3 + VTE eDEX-style terminal (eDEX-UI itself is
  not used).
- Veil Network Guard: CLI + GUI privacy auditing with a read-only root
  helper.
- Veil Agent: local llama.cpp inference in a no-network sandbox, sandboxed
  tools, a permission model with separate network and secrets grants, four
  modes, a model catalog with pinned hashes, disposable sessions.
- Security tools: a lightweight base set plus 10 opt-in Nix bundles; every
  tool has a network policy.
- Nix: single-user as the live user (traffic through Tor), signed binaries
  only, pinned nixpkgs, `veil install/remove/search/update/list`, and
  Package Center.
- Offline labs (`veil lab`) for tools that can't use Tor.
- Persistent Storage: new *Nix Packages* and *Veil Desktop Settings* features.
- Tails upgrader masked (IUKs can't apply to a derivative), replaced by a
  notify-only Veil release check. WhisperBack hidden (it reports to Tails).

Testing (QEMU/KVM, see docs/veil/TEST-REPORT.md): 39/39 automated image
checks, 7/7 persistence checks, 41 unit tests; 20 of 24 requirements PASS,
3 PARTIAL, 1 UNTESTED (reproducibility), 0 FAIL. Not tested on physical
hardware.
