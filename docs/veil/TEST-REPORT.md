# Veil OS test report

Status legend: **PASS** (verified, with evidence) · **FAIL** · **PARTIAL** ·
**UNTESTED** (not verified yet; never assume it works).

Test environment: a NixOS 26.05 host (AMD Ryzen 5 7235HS, 31 GiB RAM, KVM)
running a Debian 13 builder VM; images booted in QEMU/KVM. **No physical
hardware testing has been done by the Veil project.**

## 0.1.0-alpha.1

### Host-level tests (no image needed)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| H1 | Unit tests (37): sandbox, seccomp, policy, Network Guard parsers, audit, agent loop, catalogs | **PASS** | `python3 -m unittest discover -s tests/veil` → `Ran 37 tests … OK` |
| H2 | Agent sandbox has no network (no interfaces except lo, no DNS via nscd/resolver sockets) | **PASS** | `sandbox.self_test()` all true. A DNS leak through the nscd socket was found and fixed during development |
| H3 | Seccomp denies ptrace/keyctl/unshare in the sandbox | **PASS** | probe returns `-1 EPERM` |
| H4 | Network grant restores (Tor-routed on Veil) connectivity only when granted | **PASS** | curl fails without the grant and succeeds with it (host test) |
| H5 | Offline lab: namespaced root, raw sockets, lab TCP, no Internet route | **PASS** | `veil lab` test: `uid=0`, `lab0/lab1`, `NO-INTERNET`, raw socket ok |
| H6 | llama-server runs inside the no-network sandbox, served over a Unix socket | **PASS** | Qwen3-1.7B (SHA-256 verified) answered a chat completion over the socket |
| H7 | End-to-end agent task with a real model (write + run a file) | **UNTESTED** | First attempt was too slow under host CPU contention (~7 tokens/s prompt processing); will re-run |
| H8 | Nix bundles evaluate against pinned nixpkgs and come from the binary cache | **PASS** | `nix build --dry-run`: 9 free bundles fetch only; unfree bundles build locally (expected) |
| H9 | Shellcheck / JSON / XML validity of Veil files | **PASS** | CI static checks run locally |

### Image tests (QEMU/KVM)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Image builds with the official Tails build system | **UNTESTED** | baseline Tails 7.17.1 build in progress |
| 2 | USB live boot (UEFI) | **UNTESTED** | |
| 3 | Amnesic behavior / RAM overlay | **UNTESTED** | |
| 4 | No unintended internal-disk writes | **UNTESTED** | |
| 5 | Tor connectivity | **UNTESTED** | |
| 6 | DNS leak prevention | **UNTESTED** | |
| 7 | Firewall enforcement | **UNTESTED** | |
| 8 | Kill-switch behavior | **UNTESTED** | |
| 9 | Persistent storage | **UNTESTED** | |
| 10 | XFCE desktop integration | **UNTESTED** | |
| 11 | Tails application compatibility | **UNTESTED** | |
| 12 | Veil Console | **UNTESTED** | |
| 13 | Cybersecurity tool execution | **UNTESTED** | |
| 14 | Nix integration | **UNTESTED** | |
| 15 | Veil Agent local inference | **UNTESTED** | |
| 16 | Veil Agent offline behavior | **UNTESTED** | |
| 17 | Agent tool permissions | **UNTESTED** (in image) | |
| 18 | Agent sandboxing | **UNTESTED** (in image) | |
| 19 | Boot compatibility (BIOS, UEFI) | **UNTESTED** | |
| 20 | Boot performance | **UNTESTED** | |
| 21 | Update integrity | **UNTESTED** | |
| 22 | Image reproducibility | **UNTESTED** | |
| 23 | Shutdown behavior | **UNTESTED** | |
| 24 | Recovery procedures | **UNTESTED** | |
