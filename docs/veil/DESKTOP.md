# Veil desktop (XFCE on labwc, Wayland)

Tails 7 runs GNOME on Wayland and **does not ship X.Org**. Under X11, any
graphical app could read other apps' keystrokes and screen contents, including
Tor Browser's. Veil keeps Wayland: XFCE 4.20's panel and apps run as Wayland
clients of the **labwc** compositor. XFCE's Wayland support is officially
*experimental* in 4.20. The known limitations are listed below.

## Components

- Compositor: `labwc` (`/etc/xdg/labwc/{rc.xml,menu.xml,autostart,environment}`)
  with the `Veil` theme (`/usr/share/themes/Veil/openbox-3/themerc`)
- Panel: `xfce4-panel` (`/etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml`),
  which holds the Whisker menu, task list, Veil status (genmon), tray, volume,
  UTC clock and power button
- Wallpaper: `swaybg` (no xfdesktop, so no desktop icons). Lock: `swaylock`. Polkit: `mate-polkit`. Notifications: `xfce4-notifyd`
- GTK theme `Veil` (Adwaita-dark plus the Veil palette), icons
  `elementary-xfce-dark`, monospace font JetBrains Mono
- Apps: xfce4-terminal (fallback terminal), Mousepad, Ristretto, Task
  Manager, plus all Tails apps

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Super | Application menu |
| Super+Enter | Veil Console |
| Super+T | xfce4-terminal (fallback) |
| Super+A | Veil Agent |
| Super+G | Network Guard |
| Super+E | Files |
| Super+L | Lock screen |
| Super+←/→/↑/↓ | Snap left/right, maximize, minimize |
| Alt+Tab | Window switcher |
| Print / Shift+Print | Screenshot (full / region) to ~/Pictures |

## Known limitations (alpha)

- XFCE settings dialogs that are X11-only (display settings, xfwm4
  appearance) don't apply on Wayland. Use `wdisplays` for monitors.
- Desktop icons are disabled (`swaybg` instead of `xfdesktop`), which is
  discreet by design.
- No automatic screen lock on idle. Lock with Super+L or the power menu.
- Input methods (IBus for CJK input) and the GNOME screen magnifier are
  **untested** under labwc.
- The upstream Tails automated test suite (Cucumber + image matching) targets
  GNOME. Its desktop scenarios don't apply to Veil.
