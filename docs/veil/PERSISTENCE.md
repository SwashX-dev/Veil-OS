# Persistent Storage in Veil OS

Veil uses Tails' Persistent Storage: a LUKS2 partition on the
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

## Debian Additional Software

Enable **Additional Software** in the Persistent Storage app and unlock
Persistent Storage at the Welcome Screen on each boot where you want it.
For package changes, set an administration password in the Welcome Screen.
The simple `apt install PACKAGE` and `apt remove PACKAGE` forms prompt for
that password via sudo when run as the live user.

| Command | Effect |
|---|---|
| `apt install wireshark` | Install for this session |
| `apt install wireshark -Pr` | Install and register for restoration |
| `apt remove wireshark` | Remove for this session and keep any registration |
| `apt remove wireshark -Pr` | Remove for this session and stop restoring |
| `veil packages persistent list` | List existing registrations |
| `veil packages persistent add PACKAGE` | Install and register |
| `veil packages persistent remove PACKAGE` | Remove and unregister |
| `veil packages persistent status` | Show whether the feature is active and which registered packages are missing |
| `veil packages persistent verify` | Check registrations, current installation, and APT resolution against the immutable image |
| `veil packages persistent repair` | Configure interrupted dpkg work, repair dependencies, and reinstall registered packages |

Package Center's **Debian packages** tab uses these same install and removal
paths. Wireshark's GUI is optional in this version. Tshark remains available
in the image for local capture file analysis.

Veil provides `/usr/local/bin/apt` ahead of `/usr/bin/apt`. Ordinary
arguments pass to Debian APT. The Veil wrapper suppresses Tails' interactive
registration prompts for its own operations. The native `apt-get` and
Synaptic interfaces retain their Tails behavior. Persistent install and
remove take explicit package names with optional architecture and `-y`.
Version and suite selectors already stored by Tails are preserved for
packages outside the operation.

Only the registered package names and Tails' existing APT indexes and
archives are stored in the encrypted volume. The presence of a cached
archive does not schedule it for restoration. Successful persistent
operations check live dpkg state before atomically updating
`live-additional-software.conf`. If a command is interrupted before the
registry commit, the prior registration remains authoritative. Resolve
any dpkg error with `repair` or retry the intended command. The package
filesystem and the registration file cannot be committed in one transaction.

The boot service first attempts restoration from authenticated APT
metadata and cached archives. The existing upgrade service can then
fetch packages through the configured repositories after Tor connects.
A cache miss can prevent offline restoration. Review the Additional
Software log and use `verify` for the current session. A malformed
configuration is preserved for recovery. Correct it in the unlocked
encrypted volume before retrying. Packages that are dependencies of
other registered software may be needed after reboot. Veil refuses a
removal when APT would remove unrelated live packages or restore the
requested package as a dependency. The immutable base still supplies
its own built-in packages at every boot.
