# Threat model and new attack surface

Veil inherits Tails' threat model (https://tails.net/doc/about/warnings/):
it protects against network surveillance through Tor and against traces left
on the computer through amnesia, within the limits Tails documents. **Veil
doesn't guarantee anonymity or absolute security, and no live system removes
every forensic trace** (firmware, hardware implants, a compromised BIOS or
hypervisor, cold-boot attacks before memory erasure, user mistakes).

## What Veil adds, and the risk each one brings

| Addition | New attack surface | Mitigation |
|---|---|---|
| XFCE + labwc instead of GNOME Shell | A different compositor and panel. labwc and XFCE's Wayland support are less battle-tested than GNOME Shell. | Still Wayland, with no X.Org. Apps can't read each other's input or screen. The lock screen uses ext-session-lock. |
| Security tools in the base image | Parsers for hostile data (Wireshark dissectors, binwalk, YARA rules) | They run only when you start them, as the live user, under the Tails firewall. Only light tools are in the base image; heavy ones are opt-in. |
| Nix | Supply chain (cache.nixos.org), large closures | Signed binaries only, a pinned nixpkgs, single-user mode (no root), all traffic through Tor, opt-in. |
| Veil Agent | A malicious model or a prompt injection in files the agent reads | The inference server and tools have no network (empty netns, masked sockets), seccomp, no_new_privs and hidden credential stores. You confirm destructive or credential actions. Read-only by default. |
| Model files (GGUF) | Parser bugs in llama.cpp | Only pinned, hash-verified models are in the catalog. The server is sandboxed with no network. |
| Network Guard root helper | A sudo rule | Three exact argument lists, read-only, JSON output, and no relay identities. |
| Offline labs | User namespaces (kernel attack surface) | Already enabled in Tails for Flatpak (bubblewrap); labs add no host interface. |
| Veil release check | A metadata request to GitHub | Goes through Tor, notifies only, like the Tails upgrade check it replaces. |

## Things Veil gives up compared with Tails

- **Automatic incremental upgrades.** Tails IUKs can't apply to a
  derivative. You upgrade by installing a new verified image.
- **The upstream automated GUI test suite.** It targets GNOME, so Veil has
  its own VM tests (see TEST-REPORT.md).
- **Tails' review and audit history** for the replaced desktop components.
- **Translations** of the rebranded Welcome Screen strings (title and Start
  button), which now show in English.

## VPN before Tor (optional, 1.2.0)

When enabled, the VPN provider learns your real IP address, when you use Tor
and how much data you transfer. Your local network and ISP no longer see Tor
traffic. Tor's protection against both is unchanged: neither sees what you
do or which sites you visit. A compromised application cannot enable a VPN to
an attacker's server to learn your IP address, because connecting requires
the administration password (polkit `auth_self`). The VPN is fail-closed
(routing blackhole, firewall and session rules) and verified by Network
Guard's `vpn.before_tor` check. See [PRIVACY-CENTER](PRIVACY-CENTER.md).
