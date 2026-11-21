# Notices and attribution

## Upstream: Tails

Veil OS is derived from **Tails** (https://tails.net/, source:
https://gitlab.tails.net/tails/tails). The fork point is tag `7.17.1`, commit
`a95f1e846510307529b75b3bcaa3dd51bf5d76a9`, which carries a valid signature from
the Tails signing key
`A490 D0F4 D311 A415 3E2B B7CA DBB8 02B2 58AC D84F`.

- The Tails source code is released under the GNU GPL, version 3 or later, and
  is Copyright (C) Tails developers <foundations@tails.net>. See `COPYING`,
  `README.tails.md`, and https://tails.net/doc/about/license/ for exceptions.
- The full upstream Git history is kept in this repository.
- Veil OS is **not** an official Tails release. It is **not** affiliated with or
  endorsed by the Tails project or the Tor Project. "Tails" and "Tor" are
  mentioned only to describe where Veil comes from and what it depends on.
- Tails includes non-free firmware so it works on more hardware. Veil inherits
  that firmware unchanged.

## Veil OS additions

Copyright (C) 2026 the Veil OS contributors. Licensed under the GNU GPL,
version 3 or later.

## Third-party components added by Veil OS

| Component | Version | License | How it is included |
|---|---|---|---|
| llama.cpp | b11946 (sha256 `76ad5801…6023a2b71`) | MIT | Built from the pinned upstream tarball by `config/chroot_local-hooks/57-veil-llama-cpp` |
| labwc, XFCE 4.20, swaylock, swaybg, nix, bubblewrap, security tools | Debian trixie (Tails snapshot) | various DFSG-free | Debian packages (`config/chroot_local-packageslists/veil.list`) |
| Nix bundles (Ghidra, ZAP, Zeek, …) | nixpkgs `f7e80437` | various | **Not** shipped in the image. Downloaded on request through Tor |
| Burp Suite Community, Volatility 3 | via nixpkgs | proprietary / Volatility Software License | **Not** shipped. Installed only after the user explicitly accepts the license |
| AI models (Qwen3, Qwen2.5-Coder) | pinned HF revisions | Apache-2.0 | **Not** shipped. Downloaded only on explicit request (SHA-256 verified) |

Veil Console is an independent implementation inspired by the look of
eDEX-UI (GPL-3.0, archived). It contains no eDEX-UI code.
