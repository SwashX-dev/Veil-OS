# Modifications to upstream Tails files

The base is Tails tag `7.17.1` (commit `a95f1e8`, signed by the Tails signing
key). Regenerate the list with:

```sh
git diff --name-status 7.17.1 -- . ':!docs/veil' ':!tests/veil' ':!branding' | grep -v '^A'
```

Every file Veil **adds** has `veil` in its name or path. The table below
covers every **modified** upstream file.

| File | Change | Why | Security impact |
|---|---|---|---|
| `auto/config` | os-release: `NAME`, `ID=veil`, `PRETTY_NAME`, `VEIL_*`, URLs; the `TAILS_*` fields and `VERSION` are kept | Identity and branding; Tails components keep reading the Tails base version | None. `NAME` is used only by the Tails upgrader, which Veil masks |
| `auto/build` | Copies `docs/veil` into the image (`/usr/share/doc/veil`) | Offline documentation for users and the agent's `docs_search` | None |
| `config/binary_local-hooks/10-syslinux_customize` | Boot menu labels "Veil OS (Tails x.y)" and the highlight color | Branding (legacy BIOS menu) | None |
| `config/binary_local-includes/EFI/debian/grub.cfg` | Menu titles, colors and a Veil software-rendering option | Branding and a Pixman fallback for graphics startup | The fallback still boots the same kernel and initramfs |
| `config/binary_local-includes/EFI/debian/grub/splash.png`, `data/splash.png`, `data/splash.svg` | Veil boot splash (from `branding/src/splash.svg`) | Branding | None |
| `config/chroot_local-hooks/32-remove-useless-wayland-sessions` | Keep `veil.desktop` instead of `gnome.desktop` | Makes the Veil (XFCE/labwc) session the only user session | The GDM greeter (Welcome Screen) still runs GNOME Shell in greeter mode, unchanged |
| `config/chroot_local-includes/usr/lib/python3/dist-packages/additional_software.py` | Validate package selectors and serialize and sync registry writes | Keep the Veil and Tails frontends consistent | Registration stays in the Tails encrypted volume |
| `config/chroot_local-includes/usr/local/lib/additional-software/asp-*` | Explicit Veil operations bypass interactive registration prompts. Keep cached boot restoration and the post-Tor upgrade cycle | Distinguish temporary and persistent APT operations | Uses APT's existing repository trust and dpkg |
| `config/chroot_local-includes/usr/local/lib/tails-spoof-mac` | `is-active gnome-session.target veil-session.target` | MAC-spoofing failure notifications must also reach a running Veil session | Keeps a Tails safety notification working |
| `config/chroot_local-includes/usr/local/bin/tails-screen-locker` | In the Veil session, lock with `veil-lock` (swaylock) after Tails' unchanged password-setup flow | `org.gnome.ScreenSaver` doesn't exist outside GNOME Shell | The lock uses ext-session-lock (compositor-enforced) and PAM, as before |
| `.../tailsgreeter/__init__.py`, `.../greeter/main.ui.in`, `.../greeter/greeter.css` | Window title "Welcome to Veil OS", button "Start Veil OS", dark Veil styling | Branding | None. Those two strings are no longer translated |
| `.../tps/configuration/features.py`, `.../tps_frontend/views/features_view.py`, `.../persistent-storage/features_view.ui.in` | Two new Persistent Storage features: *Nix Packages*, *Veil Desktop Settings* | Optional encrypted persistence for Nix and desktop settings | Uses Tails' existing bind-mount mechanism (symlink-safe, inside LUKS) |
| `config/chroot_local-includes/usr/share/tails/build/passwd`, `.../group` | Add `tcpdump:x:170:170` / `tcpdump:x:170:` | The `tcpdump` package creates a system account; Tails pins every UID/GID for upgrade stability and fails the build on unknown accounts. Veil pins its accounts in the unused 170–189 range (`03-veil-pin-uids-gids`) | None (stable, non-colliding IDs) |
| `.gitignore` | Ignore the docs copied at build time | Hygiene | None |
| `README.md` | Upstream README moved to `README.tails.md`, replaced by the Veil README | Clarity and attribution | None |

## Runtime changes made by Veil hooks (not file edits)

- `03-veil-pin-uids-gids`: before Tails' `04-change-gids-and-uids`, moves
  Veil-added system accounts (`tcpdump`) to fixed IDs in 170–189.
- `57-veil-llama-cpp`: builds llama.cpp b11946 from a hash-pinned tarball
  into `/usr/local/lib/veil/llama.cpp`.
- `58-veil-nix`: creates `/nix` owned by the live user (UID 1000) and masks
  any `nix-daemon` units.
- `59-veil-session`:
  - removes X11 session files;
  - masks the user units `tails-upgrade-frontend.service` and
    `tails-post-greeter-whisperback.service`;
  - hides the WhisperBack launcher;
  - updates the icon cache and loads the core XFCE StatusNotifier plugin in-process;
  - checks that the session's dependencies are present.

## Not modified

Firewall (`ferm.conf`), Tor configuration and onion-grater, AppArmor
profiles, the Tor Browser Flatpak, Persistent Storage encryption,
live-boot/initramfs, the memory-erasure and emergency-shutdown code, kernel
sysctls, Tor Connection, htpdate, MAC spoofing logic, sudoers for Tails
components, and the Welcome Screen's behavior.
