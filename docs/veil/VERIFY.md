# Verifying a Veil OS image

Every release publishes `veil-amd64-<version>.img` (USB) and `.iso`,
`SHA256SUMS`, and `SHA256SUMS.asc` (an OpenPGP signature by the Veil release
key).

```sh
gpg --import veil-release-key.asc          # from this repository: docs/veil/veil-release-key.asc
gpg --fingerprint "Veil OS release"         # compare with the fingerprint below and in the release notes
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
```

The fingerprint is published in this repository, in the release notes, and
in `docs/veil/RELEASE-KEY-FINGERPRINT`. Check it through at least two of
these. The key is a project key generated on the build host; it is **not** a
Tails key.
