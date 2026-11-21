# Veil Console

An immersive, eDEX-UI-inspired terminal, written from scratch in Python
(GTK3 + VTE).

**Why not eDEX-UI itself?** eDEX-UI was archived by its author in 2021. It is
an Electron application pinned to an old Chromium with known, unpatched
vulnerabilities, and it bundles a large Node.js dependency tree. Putting it in
a privacy-focused OS would add a big, unmaintained attack surface. Veil Console
reproduces the useful ideas instead.

- Multiple terminal tabs (VTE, the same engine as xfce4-terminal)
- Side panels: UTC clock, uptime, CPU graph, memory (RAM is also your disk
  here), top processes; Tor/persistence/agent status, network throughput
  graphs, interfaces
- Filesystem strip that follows the current directory (OSC 7)
- Integration with Veil Agent: Ctrl+Shift+A opens an agent tab in the current
  directory, and Ctrl+Shift+X explains the selected output
- Configurable panels and optional effects (`~/.config/veil/console.json`)

**Privacy:** the console makes no network requests and only reads `/proc` and
`/sys`. It hides hardware (MAC) addresses unless you tick *reveal*, and shows
no IP addresses. It starts only when you open it, so it adds nothing to boot
time.

| Keys | Action |
|---|---|
| Ctrl+Shift+T / W | New / close tab |
| Ctrl+PgUp / PgDn | Previous / next tab |
| Ctrl+Shift+A | Veil Agent tab (terminal mode, workspace = current directory) |
| Ctrl+Shift+X | Explain the selected text with Veil Agent (research mode) |
| Ctrl+Shift+P | Toggle side panels |
| Ctrl+Shift+E | Toggle visual effects |
| Ctrl+Shift+C / V | Copy / paste |
| Ctrl + / − | Zoom |
| F11 | Fullscreen |
