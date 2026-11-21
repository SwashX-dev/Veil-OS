# Nix on Veil OS

Nix is the preferred way to add software. Debian/APT and Tails' *Additional
Software* feature stay intact.

- **Single-user, as the live user.** `/nix` belongs to `amnesia`. There is no
  `nix-daemon` and there are no build users. Every download is a TCP
  connection from the live user, so the **unchanged Tails firewall sends it
  through Tor**. Nix can't add root services, setuid binaries or firewall
  rules.
- **Signed binaries only:** `substituters = https://cache.nixos.org`,
  `require-sigs = true` (`/etc/nix/nix.conf`).
- **Pinned:** the `nixpkgs` registry entry and the bundle flake
  (`/usr/share/veil/nix`) both point at nixpkgs `f7e804372523`.
- **Persistence:** turn on *Nix Packages* in Persistent Storage to keep `/nix`
  and `~/.local/state/nix` across reboots. Otherwise installs live in RAM.

```sh
veil search ghidra            # bundles + tool catalog (offline)
veil search --remote jq       # nixpkgs search (downloads the index through Tor)
veil install bundle:reverse-engineering
veil install jq ripgrep
veil list
veil update                   # nix profile upgrade --all
veil remove bundle:reverse-engineering
nix shell nixpkgs#hello       # plain Nix works too
```

**Package Center** (menu → Veil Package Center) is the graphical front end. It
runs the same commands in an embedded terminal, so license prompts look
exactly like they do on the command line.

Note: over Tor, large bundles (Ghidra is about 2 GB) download slowly.
