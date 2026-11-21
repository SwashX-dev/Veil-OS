# Veil desktop (XFCE on labwc, Wayland)

Tails 7 runs GNOME on Wayland and **does not ship X.Org**. Under X11, any
graphical app could read other apps' keystrokes and screen contents, including
Tor Browser's. Veil keeps Wayland: XFCE 4.20's panel and apps run as Wayland
clients of the **labwc** compositor. XFCE's Wayland support is officially
*experimental* in 4.20. The known limitations are listed below.

## Components

- Compositor: `labwc` (`/etc/xdg/labwc/{rc.xml,menu.xml,autostart,environment}`)
  with the `Veil` theme (`/usr/share/themes/Veil/openbox-3/themerc`)
- Panel: `xfce4-panel` with app launchers, task buttons, Tor status and a
  StatusNotifier tray. The bundled tray runs in-process. XEmbed tray icons
  and X11-only external plugins are unavailable in this Wayland session.
- Wallpaper: `swaybg` (no xfdesktop, so no desktop icons). Lock: `swaylock`. Polkit: `mate-polkit`. Notifications: `xfce4-notifyd`
- GTK theme `Veil` (Adwaita-dark plus the Veil palette), icons
  `elementary-xfce-dark`, monospace font JetBrains Mono
- Apps: xfce4-terminal (fallback terminal), Mousepad, Ristretto, Task
  Manager, plus all Tails apps

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Super, Super+Space | Application menu |
| Super+Enter | Veil Console |
| Super+T | xfce4-terminal (fallback) |
| Super+A | Veil Agent |
| Super+G | Network Guard |
| Super+E | Files |
| Super+L | Lock screen |
| Super+←/→/↑/↓ | Snap left/right, maximize, minimize |
| Alt+Tab | Window switcher |
| Print / Shift+Print | Screenshot (full / region) to ~/Pictures |

## Startup and graphics recovery

GDM starts the sole user session from `veil.desktop`. Once labwc has a
Wayland socket and has published the environment, startup registers the
session with GDM. The Tails Welcome Screen still uses its own GNOME Shell
process as the dedicated greeter account. No GNOME desktop session is
selectable for the live user.

Veil retries an early compositor exit once with Pixman software rendering.
The UEFI boot entry **Veil OS (Software Rendering)** sets
`veil.software-rendering` to force Pixman. The fallback still requires
a working Wayland-compatible display device.

If the compositor is lost after the session has registered (a crash, or a
GPU or display fault), `veil-session` restarts it with Pixman instead of
ending the session. Windows that were open are gone, because Wayland clients
exit with their compositor, and a notification explains what happened. The
screen lock survives: while the compositor runs, `veil-session` records
whether swaylock is active, and if the compositor is lost while it was, the
new session starts `veil-lock` before it starts anything else. Only six
compositor losses within five minutes make Veil request a normal shutdown,
because no usable desktop can be provided then. A logout or shutdown
(SIGTERM) is never treated as a crash. The cause of each loss (exit status
or signal) is written to the journal as `Veil: compositor ...`.

Version 1.1.0 instead shut the machine down on every such loss, which looked
like a random black screen followed by power-off. The underlying reason that
the compositor stops on some hardware is not yet known.

## Known limitations

- GDM exports `XDG_DATA_DIRS` with trailing slashes, which libxfce4util
  skips; `veil-session` normalizes it (otherwise the panel finds no plugins).
- XFCE settings dialogs that are X11-only (display settings, xfwm4
  appearance) don't apply on Wayland. Use `wdisplays` for monitors.
- Volume: use the keyboard volume keys or Settings → Sound.
- Desktop icons are disabled (`swaybg` instead of `xfdesktop`), which is
  discreet by design.
- No automatic screen lock on idle. Lock with Super+L or the power menu.
- Input methods (IBus for CJK input) and the GNOME screen magnifier are
  **untested** under labwc.
- The upstream Tails automated test suite (Cucumber + image matching) targets
  GNOME. Its desktop scenarios don't apply to Veil.
