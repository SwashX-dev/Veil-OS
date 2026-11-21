# Contributing to Veil OS

- **Keep upstream diffs small.** Prefer adding files (`veil-*` hooks,
  includes, the `veil` Python package) over editing Tails files. Every edit to
  an upstream file must be listed in `docs/veil/MODIFICATIONS.md` with the
  reason.
- **Never weaken Tails protections** (firewall, Tor enforcement, amnesia,
  AppArmor, persistence encryption) to make a feature work. If a tool can't
  work over Tor, it goes in an offline lab.
- **Security controls go in code, not prompts.** Agent permissions belong in
  `veil/agent/policy.py` and `veil/sandbox.py`, and come with tests.
- **Test:** `python3 -m unittest discover -s tests/veil -v` must pass. Image
  changes need a build plus the VM checks in `docs/veil/TEST-REPORT.md`.
- **Pin and verify** every external download: commit-pinned URLs and a
  SHA-256 hash.
- Never commit credentials, keys, tokens or personal data.
- Use `git commit -s`. Contributions are GPL-3.0-or-later.

Bugs: GitHub issues. Vulnerabilities: see `SECURITY.md`.
