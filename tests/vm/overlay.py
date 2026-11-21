"""Load source changes into a running test VM's RAM overlay (before login).

Used to test changes on an already-built image. Everything written here is
lost at shutdown, like any change to a Veil OS live system.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

CHUNK = 12000


def push(rs, data: bytes, remote: str, mode: int = 0o644) -> None:
    tmp = "/tmp/.veil-overlay-push"
    rc, _out, err = rs.sh(f"rm -f {tmp} && touch {tmp}")
    if rc:
        raise RuntimeError(err)
    for start in range(0, len(data), CHUNK):
        chunk = base64.b64encode(data[start:start + CHUNK]).decode()
        rc, _out, err = rs.sh(f"printf %s {chunk} | base64 -d >> {tmp}")
        if rc:
            raise RuntimeError(err)
    rc, _out, err = rs.sh(f"install -D -m {mode:o} {tmp} '{remote}' && rm -f {tmp}")
    if rc:
        raise RuntimeError(err)


def changed_files(repo: Path, base: str) -> tuple[list[str], list[str]]:
    root = "config/chroot_local-includes"
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).splitlines()
    changed = git("diff", "--name-only", "--diff-filter=ACMRT", base, "--", root)
    changed += git("ls-files", "--others", "--exclude-standard", "--", root)
    deleted = git("diff", "--name-only", "--diff-filter=D", base, "--", root)
    return sorted(set(changed)), deleted


def apply(rs, repo: Path, base: str, debs: list[Path] = (), sudoers: list[Path] = ()) -> list[str]:
    """Push every file changed since `base`, install packages, reload what reads them."""
    root = repo / "config/chroot_local-includes"
    changed, deleted = changed_files(repo, base)
    log = []
    for rel in changed:
        path = repo / rel
        if not path.is_file():
            continue
        remote = "/" + str(path.relative_to(root))
        push(rs, path.read_bytes(), remote, path.stat().st_mode & 0o777)
        log.append(f"+ {remote}")
    for rel in deleted:
        remote = "/" + str(Path(rel).relative_to("config/chroot_local-includes"))
        rs.sh(f"rm -f '{remote}'")
        log.append(f"- {remote}")
    for deb in debs:
        push(rs, deb.read_bytes(), f"/tmp/{deb.name}")
    if debs:
        names = " ".join(f"/tmp/{d.name}" for d in debs)
        rc, out, err = rs.sh(f"dpkg -i {names}", timeout=300)
        log.append(f"dpkg -i: rc={rc} {err.strip()[-300:]}")
        if rc:
            raise RuntimeError(f"dpkg failed: {out}{err}")
    for f in sudoers:
        push(rs, f.read_bytes(), f"/etc/sudoers.d/{f.name}", 0o440)
    rc, out, err = rs.sh("visudo -c >/dev/null && systemctl daemon-reload && "
                         "sysctl -q --system >/dev/null; "
                         "systemctl restart polkit && nmcli general reload conf || true", timeout=120)
    log.append(f"reload: rc={rc} {err.strip()[-300:]}")
    return log
