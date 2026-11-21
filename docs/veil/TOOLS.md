# Security tools

`veil tools` lists the catalog and `veil tools NAME` shows one entry. The
source of truth is `/usr/share/veil/tools.json`. Each tool has:

- **network policy**: `none`, `tor-tcp` (TCP only, through Tor), `lan-direct`,
  `offline-lab` (raw sockets or UDP, so only inside `veil lab`), `root-capture`
  (packet capture needs the administration password), or `radio` (Wi-Fi
  hardware)
- **privacy status**: `compatible`, `tor-limited`, `lab-only` or `caution`
- **mode**: `user`, `root` or `lab`

## Base image (Debian packages)

nmap, tcpdump, tshark, Wireshark, ngrep, socat, gdb, strace, ltrace,
checksec, YARA, Lynis, chkrootkit, rkhunter, aircrack-ng, age, binwalk,
Sleuth Kit, foremost, testdisk, hashdeep, ssdeep, John the Ripper, sqlmap,
plus Tails' KeePassXC, GnuPG and mat2.

## Optional Nix bundles (`veil install bundle:NAME`)

| Bundle | Contents |
|---|---|
| reverse-engineering | Ghidra, Cutter, radare2, rizin, ImHex, jadx, apktool, UPX |
| web-appsec | OWASP ZAP, ffuf, gobuster, nuclei, mitmproxy, Semgrep, Trivy, gitleaks |
| burp | Burp Suite Community. **Proprietary**: you must accept PortSwigger's license |
| forensics | binwalk 3, YARA, Chainsaw, osquery, Bulk Extractor, ExifTool |
| host-audit | AIDE, OpenSCAP, ClamAV command-line tools |
| volatility | Volatility 3. **Volatility Software License**: you must accept it |
| network-ids | Zeek, Suricata (for offline PCAP analysis) |
| wireless | Kismet, bettercap |
| offensive-lab | masscan, THC-Hydra, NetExec, Responder, hashcat, amass, subfinder, dnsrecon |
| ai | Ollama, llama.cpp with Vulkan |
| dev | Python, GCC, make, Go, Rust, Node.js (slim), QEMU |

Bundles come pre-built and signed from cache.nixos.org (nixpkgs pinned in
`/usr/share/veil/nix/flake.lock`). The unfree bundles build locally, because
cache.nixos.org doesn't distribute unfree packages.

## Offline labs

Many tools can't work over Tor: SYN or UDP scans, ARP and other LAN attacks,
packet injection. **Veil never opens a non-Tor path for them.** Instead:

```sh
veil lab up demo                         # isolated user+net namespaces
veil lab target demo -- python3 -m http.server --bind 10.66.0.2 8080 &   # target side (lab1)
veil lab run demo -- nmap -sS -Pn -p 1-1000 10.66.0.2                     # scanner side (lab0)
veil lab shell demo                      # interactive shell on the scanner side
veil lab down demo
```

The scanner side (`lab0`, 10.66.0.1) and the target side (`lab1`,
10.66.0.2) are separate network namespaces joined by a veth pair, so traffic
really crosses a link. Inside a lab you have root over the lab's own
namespaces only, with `CAP_NET_RAW` and `CAP_NET_ADMIN`. Use `/usr/bin/curl`
there: Tails' `curl` wrapper always goes through Tor's SOCKS port, which a
lab cannot reach. There is no interface to the host, Tor, the
LAN or the Internet.

**Authorization:** only test systems you own or are explicitly authorized to
test.

Wireshark can be installed from Debian APT through Package Center or
`apt install wireshark`. Use the unprivileged GUI for saved captures.
See [Persistent Storage](PERSISTENCE.md#debian-additional-software) to
restore the GUI across boots.
