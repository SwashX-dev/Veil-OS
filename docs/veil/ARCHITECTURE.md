# Veil OS architecture

Veil OS is Tails 7.17.1 plus a layer of additions. The security-critical core
of Tails is **not reimplemented**.

```
┌──────────────────────────── Veil user session (amnesia, UID 1000) ────────────────────────────┐
│ labwc (Wayland) · xfce4-panel · swaybg · xfce4-notifyd · mate-polkit · nm-applet              │
│ Veil Console · Veil Agent GUI · Network Guard · Package Center · Tails apps (Tor Browser, …)  │
│                                                                                               │
│ veil agent ──► tools ──► bwrap sandbox (empty netns, seccomp, no session bus, secrets hidden)  │
│      │                                                                                        │
│      └──► llama-server (bwrap, empty netns) ◄── Unix socket only                              │
│ veil install ──► nix (single-user, runs as amnesia) ──► TCP ──► Tails firewall ──► Tor        │
│ veil lab ──► user+net namespace: lab0/lab1 veth, no uplink                                     │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────────── unchanged Tails system layer ────────────────────────┐
│ GDM + Tails Welcome Screen · Tor + onion-grater · ferm firewall (default deny)│
│ tps (Persistent Storage, LUKS2) · AppArmor · memory erasure · USB-removal    │
│ emergency shutdown · tails-security-check · MAC spoofing · htpdate           │
└──────────────────────────────────────────────────────────────────────────────┘
        live-boot: read-only SquashFS + tmpfs overlay (RAM)  ·  Debian trixie
```

## Design rules

1. **Enforcement stays with Tails.** Veil never adds firewall ACCEPT rules or
   new SOCKS users. Everything new runs as the live user, whose TCP traffic
   Tails already forces through Tor and whose UDP and other non-Tor traffic
   Tails already rejects.
2. **Veil's own isolation is kernel-enforced.** The agent's tools, the model
   server and the labs run in unprivileged user/network namespaces created by
   bubblewrap, with a seccomp filter. An empty network namespace plus masked
   runtime sockets means *no* network: not Tor, not DNS, not the LAN.
3. **Prompts are not permissions.** The agent's system prompt only shapes its
   style. Permissions are code (`veil/agent/policy.py`, `veil/sandbox.py`),
   and confirmations come only from the human (TTY or GTK dialog).
4. **Nothing is downloaded behind your back.** No model downloads, no package
   installs, no telemetry. Veil makes two automatic network requests, both
   through Tor and both mirroring Tails behavior: the Tails security-advisory
   check, and a notify-only Veil release check against GitHub.
5. **Small diffs to upstream.** See [MODIFICATIONS.md](MODIFICATIONS.md).

## Session startup

GDM → Tails Welcome Screen (unchanged, rebranded) → GDM starts the only
Wayland session left, `veil.desktop` → `/usr/local/bin/veil-session` (env,
keyboard layout from the Welcome Screen) → `labwc` → `/etc/xdg/labwc/autostart`:

1. exports the Wayland environment to systemd and D-Bus,
2. starts `veil-session.target`. This pulls in the same Tails units GNOME
   triggers (`tails-dump-user-env`, `tails-start-system-gnome-session-target`)
   plus `veil-update-check`,
3. starts the wallpaper, panel, polkit agent and network applet,
4. runs `veil-autostart`, an XDG autostart runner. It starts Tails'
   `desktop.target` (Tor Connection, Persistent Storage, security check,
   notifications…).

## Mapping of Tails GNOME integrations

| Tails under GNOME | Veil OS |
|---|---|
| GNOME Shell session | labwc + XFCE panel (`veil.desktop`) |
| `torstatus@tails.boum.org` extension | genmon panel indicator `veil-panel-status` + Network Guard |
| `status-menu-helper` (Lock / power) | panel power button → `veil-shutdown` (Lock / Restart / Shut Down) |
| GNOME lock screen (`org.gnome.ScreenSaver`) | `tails-screen-locker` (unchanged password flow) → `veil-lock` (swaylock, ext-session-lock) |
| GNOME Shell polkit agent | mate-polkit |
| GNOME notifications | xfce4-notifyd (notification log disabled) |
| `gnome-session.target` hooks | `veil-session.target` (+ `tails-spoof-mac` checks both) |
| xdg-desktop-portal-gnome | xdg-desktop-portal-gtk / -wlr (`veil-portals.conf`) |
| GNOME quick settings network | nm-applet (StatusNotifier in the panel tray) |
| Tails upgrader | masked; `veil-update-check` notifies about new Veil releases |
| WhisperBack (reports to Tails) | hidden; Veil bugs go to GitHub issues |
