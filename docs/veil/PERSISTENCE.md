# Persistent Storage in Veil OS

Veil uses Tails' Persistent Storage **unchanged**: a LUKS2 partition on the
USB stick, created and unlocked from the Welcome Screen. You can always start
without unlocking it. Veil adds two features, using the same bind-mount
mechanism as the Tails features:

| Feature | Keeps |
|---|---|
| Nix Packages | `/nix`, `~/.local/state/nix` |
| Veil Desktop Settings | `~/.config/xfce4`, `~/.config/labwc`, `~/.config/veil` |

These need no feature of their own, because they live under `~/Persistent`
(the default *Persistent Folder* feature):

- AI models: `~/Persistent/.veil/models`
- Saved agent sessions and logs: `~/Persistent/.veil/agent/sessions`
- Labs / research workspaces: wherever you choose, e.g. `~/Persistent/Lab`
- Network Guard reports: `~/Persistent/veil-privacy-report-*.json`

Keys and documents keep using the Tails features (GnuPG, SSH Client,
Persistent Folder, KeePassXC database in the Persistent Folder).
