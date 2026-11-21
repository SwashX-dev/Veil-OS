# Veil Network Guard

`veil privacy status` (CLI), `veil security audit` (adds the amnesia and
hardening checks), and the **Network Guard** app (Super+G, or click the panel
indicator).

Network Guard **only inspects**. The unchanged Tails firewall does the
enforcing. A small root helper (`/usr/local/lib/veil/netguard-helper`) is
allowed through sudo with three exact argument lists (`firewall`, `sockets`,
`tor`). It changes nothing and never prints relay identities, addresses or
destinations.

| Check | What it verifies |
|---|---|
| firewall.default_deny | IPv4 and IPv6 INPUT/OUTPUT/FORWARD policies are DROP |
| firewall.final_reject | OUTPUT ends with log-and-reject |
| firewall.kill_switch | No ACCEPT rule allows non-loopback egress except for Tor (`debian-tor`), the opt-in Unsafe Browser user, ESTABLISHED traffic and the LAN subchain, so non-Tor traffic has nowhere to go if Tor stops |
| firewall.transproxy | The live user's TCP is redirected to Tor's TransPort |
| firewall.blocked_attempts | Counter of rejected outbound packets (apps that tried to bypass Tor) |
| dns.redirect / dns.resolv_conf | System DNS is answered by Tor's DNSPort; the resolver is 127.0.0.1 |
| dns.leak_probe / udp.leak_probe | **Active**: sends direct UDP/53 and UDP/123 to RFC 5737 TEST-NET (never routed). It must be rejected locally |
| ipv6.disabled | IPv6 is disabled on all non-loopback interfaces |
| tor.bootstrap / tor.circuits | Bootstrap progress, network liveness, number of built circuits |
| connections.direct / .lan / .unsafe_browser | Live sockets: Internet peers not owned by Tor fail; LAN peers warn |
| mac.spoofing | Welcome Screen setting; physical NICs report a permanent address different from the current one |
| metadata.identity / metadata.tools | Generic hostname, UTC clock, mat2 available |
| agent.network_policy | Veil Agent is offline by default; lists active network grants |
| nix.policy | Nix accepts only signed binaries from cache.nixos.org |

`--json` produces a machine-readable report. `--no-probes` skips the active
probes. The exit code is 2 if any check fails.
