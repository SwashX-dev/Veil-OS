# Security policy

## Supported versions

Veil OS is **alpha** software. Only the latest release gets fixes. Each
release is based on a specific Tails release. When Tails publishes a security
release, Veil rebases onto it and cuts a new release
([docs/veil/UPSTREAM-SYNC.md](docs/veil/UPSTREAM-SYNC.md)). Until then, Tails
security advisories shown by `tails-security-check` also apply to Veil.

## Reporting a vulnerability

- **Veil-specific issues** (Veil Agent, sandbox, Network Guard, Nix
  integration, desktop session, packaging): report them privately through
  GitHub's *Report a vulnerability* button (Security Advisories) on
  https://github.com/SwashX-dev/Veil-OS. Don't open a public issue for an
  unfixed vulnerability.
- **Issues that also affect upstream Tails**: report them to the Tails
  project too, following https://tails.net/security/. Don't send Tails
  reports about Veil-only code.

Please include the Veil version (`veil version`), what you expected, what
happened, and steps to reproduce. Never include personal data, or logs that
contain it.

## Scope and non-goals

In scope: anything that weakens amnesia, Tor enforcement, the firewall,
Persistent Storage encryption, or the isolation Veil adds (agent sandbox,
model server, offline labs).

Veil does not promise anonymity against every adversary, and it cannot remove
every forensic trace. Read [docs/veil/THREAT-MODEL.md](docs/veil/THREAT-MODEL.md).
