# Keeping up with Tails

Remotes: `upstream` = https://gitlab.tails.net/tails/tails.git,
`origin` = https://github.com/SwashX-dev/Veil-OS.git.

For each Tails release (security releases first):

```sh
git fetch upstream --tags
git tag -v <new-tails-tag>                 # must be signed by A490 D0F4 … D84F
git checkout -b sync/<new-tails-tag> main
git merge --no-ff <new-tails-tag>          # resolve conflicts in the files from MODIFICATIONS.md
python3 -m unittest discover -s tests/veil
rake build                                  # then the VM regression tests in TEST-REPORT.md
```

Conflicts mostly happen in the few upstream files Veil patches
([MODIFICATIONS.md](MODIFICATIONS.md)). After merging, re-check:
persistence features (`tps`), the GNOME-to-Veil session mapping
(ARCHITECTURE.md), and the firewall fixture in `tests/veil/fixtures`.
Re-dump that fixture from a running image if `ferm.conf` changed.
