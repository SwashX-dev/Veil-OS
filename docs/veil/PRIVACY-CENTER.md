# Privacy Center

Open **Privacy Center** from the menu (or press Super+P), or use
`veil privacy settings` in a terminal. Settings are stored in
`~/.config/veil/privacy.json`: in RAM, or in Persistent Storage when the
*Veil Desktop Settings* feature is on.

| Setting | Default | What it does |
|---|---|---|
| Tor Browser security level | Standard | Starts Tor Browser at **Standard**, **Safer** or **Safest**, Tor Browser's own levels |
| Lock the screen when idle | 10 minutes | Locks with swaylock, but only when a screen-lock password exists, so you can never be locked out |
| Clear the clipboard | Never | Clears the clipboard and primary selection N seconds after the last copy |
| Emergency shutdown hotkey | On | **Ctrl+Alt+Shift+Delete** runs Tails' emergency shutdown, the same as pulling the USB stick: everything is killed, memory is erased and the computer powers off |
| Block cameras / microphones | Off | Makes camera and audio-capture device nodes inaccessible (mode 0000, no logind ACL) for every application that opens them afterwards. Blocking needs no password; unblocking asks for the administration password |
| VPN before Tor | Off | See below |
| Video upscaling | Off | Real-time upscaling in Veil Player (Super+V); see below |

## Tor Browser

Tor Browser hides you by making all its users look the same. Extensions,
custom user agents, changed fonts or other "hardening" preferences make a
browser *more* recognizable. Veil therefore never changes Tor Browser's
fingerprint. The only setting is the security level, which is one of Tor
Browser's own. Protection around the browser comes from Tails, unchanged:
AppArmor, a separate network namespace, and Tor's stream isolation. Veil
adds the kill switches, the clipboard timer and the idle lock described above.

## VPN before Tor (Proton VPN, WireGuard)

    your computer → VPN server → Tor guard → Tor middle → Tor exit → website

1. In your Proton VPN account, download a **WireGuard configuration** (the
   server must be given by IP address, which Proton's files are). Any
   provider's WireGuard file works the same way.
2. Privacy Center → *VPN before Tor* → **Import…**, or
   `veil vpn import ~/Downloads/proton-nl-1.conf`.
3. Click **Connect**, or run `veil vpn connect`. Your administration password
   is required, so set one in the Welcome Screen.

What changes:

- **Your local network and ISP** see encrypted WireGuard traffic to the VPN
  server instead of Tor traffic.
- **The VPN provider** sees your real IP address and that you use Tor, but
  nothing inside Tor. Tor still protects you from the provider exactly as
  it protects you from your ISP.
- **Only Tor's own connections** use the VPN. Applications still go through
  Tor as before, and nothing else is ever routed through the VPN.
- **Fail-closed:**
  - **Routing:** Tor's route has a blackhole fallback.
  - **Firewall:** Tor may not use any other interface, and only the
    WireGuard packets to the configured server, marked by the kernel, may
    leave.
  - **Tails firewall reloads:** the rules survive them.

  If the VPN drops, Tor stops instead of connecting directly. Network
  Guard checks all three layers.
- The `DNS`, IPv6 `Address` and `PostUp`/`PreUp` lines of the file are
  ignored or rejected. Names are resolved through Tor, IPv6 is disabled, and
  Veil never runs commands from a configuration file.

Tor before VPN (Tor → VPN → website) is **not** supported. WireGuard uses
UDP, which Tor cannot carry, and a fixed exit point tied to a paid account
would link all of your activity together.

## Tor circuits (why not 4 or 5 relays)

Tor builds circuits through 3 relays, and paths to onion services are longer.
Tor already pins extra middle relays for onion-service circuits
(*vanguards-lite*). Veil does **not** make normal circuits longer:

- Tor has no supported setting for it. Doing it from a controller means
  replacing Tor's own relay selection, guard handling and stream isolation,
  which would weaken anonymity if done imperfectly.
- The attacks that defeat Tor watch both ends of a circuit (your guard and
  your exit), and extra middle relays don't change that. The Tor Project's
  analysis is that longer paths add no meaningful anonymity.
- Every relay adds latency and load on the Tor network.

The full *vanguards* add-on, which adds extra relay layers to onion-service
circuits, is not packaged in Debian 13, so Veil does not ship it.

## Video upscaling

| Mode | How | Cost |
|---|---|---|
| Off | mpv defaults | — |
| Quality | mpv's `high-quality` profile (EWA Lanczos, debanding) | Real time on most GPUs |
| Enhanced | Quality + FSRCNNX x2 (a small neural-network shader, LGPL-3.0+, vendored) | Needs a capable GPU for real time |

Videos are enlarged to the window or screen. 720p fills a 1080p screen, and
1080p fills a 4K screen, using the chosen method. To make an upscaled
copy of a file instead (not real time):

    veil media upscale video.mp4 --to 1080p              # Lanczos
    veil media upscale video.mp4 --to 2160p --enhanced   # FSRCNNX via libplacebo (Vulkan)

Veil Player (`veil-player FILE`) and the converter run in Veil's sandbox,
which has no network, a read-only system and hidden secrets. Video files are
a common attack surface. Upscaling happens only on your computer and changes
nothing that a website can observe. Tails' own Celluloid is a separate
Flatpak app with its own settings and is not changed: use Veil Player for
upscaling.

With safe graphics active (see [DESKTOP](DESKTOP.md)), rendering happens on
the CPU, and Enhanced upscaling will stutter.

## Diagnostics

`veil diagnostics --save` (or the Privacy Center button) writes a report to
`~/Persistent` (or your home folder) with:

- Veil version and boot options
- GPU and driver
- rendering mode
- Privacy Center settings
- redacted log lines about graphics, the compositor, crashes, memory and USB
  disconnects

It contains no browsing data, no network addresses and no hardware serial
numbers.
