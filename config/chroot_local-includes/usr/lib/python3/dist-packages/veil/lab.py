"""Offline labs: isolated network namespaces with NO route to the Internet.

Tools that cannot work over Tor (raw-socket scans, UDP, packet injection)
run here against local targets only. A lab is an unprivileged user + network
namespace created by bubblewrap. Inside it the user has "root" with
CAP_NET_RAW/CAP_NET_ADMIN over the lab's own network namespace only, and the
lab gets a private veth pair:

    lab0  10.66.0.1/24   scanner side (veil lab run / shell)
    lab1  10.66.0.2/24   target side, in a nested network namespace
                         (veil lab target NAME -- COMMAND starts services there)

There is no interface connected to the host, so nothing in a lab can reach
Tor, the LAN or the Internet, and the Tails firewall is never touched.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from veil import common, sandbox

SETUP = r"""
set -e
ip link set lo up
# Target side lives in its own network namespace so traffic really crosses
# the veth link (scanning an address of your own namespace goes via "lo").
unshare --net sh -c 'ip link set lo up; exec sleep infinity' &
TARGET_PID=$!
sleep 0.3
ip link add lab0 type veth peer name lab1
ip link set lab1 netns "$TARGET_PID"
ip addr add 10.66.0.1/24 dev lab0
ip link set lab0 up
nsenter -t "$TARGET_PID" -n sh -c 'ip addr add 10.66.0.2/24 dev lab1 && ip link set lab1 up'
echo "$TARGET_PID" > "$VEIL_LAB_DIR/target.pid"
mount -t sysfs sysfs /sys 2>/dev/null || true
echo ready > "$VEIL_LAB_DIR/ready"
exec sleep infinity
"""


def _state() -> Path:
    return common.amnesic_state_dir("labs")


def _info(name: str) -> dict | None:
    f = _state() / name / "lab.json"
    if not f.exists():
        return None
    data = json.loads(f.read_text())
    try:
        os.kill(data["pid"], 0)
    except (ProcessLookupError, PermissionError):
        return None
    return data


def up(name: str = "default", workspace: str | None = None) -> dict:
    existing = _info(name)
    if existing:
        return existing
    d = _state() / name
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    (d / "ready").unlink(missing_ok=True)
    ws = os.path.realpath(os.path.expanduser(workspace or "~/Lab"))
    os.makedirs(ws, exist_ok=True)
    spec = sandbox.SandboxSpec(network=False, fake_root=True, writable=[ws, str(d)], unshare_pid=True,
                               hostname=f"lab-{name}", extra_env={"VEIL_LAB_DIR": str(d)}, cwd=ws,
                               die_with_parent=False)
    proc = sandbox.popen(spec, ["sh", "-c", SETUP], stdout=subprocess.DEVNULL, stderr=open(d / "lab.log", "wb"),
                         start_new_session=True)
    for _ in range(100):
        if (d / "ready").exists():
            break
        if proc.poll() is not None:
            raise RuntimeError("lab failed to start: " + (d / "lab.log").read_text()[-500:])
        time.sleep(0.1)
    # The bwrap child that owns the namespaces is the first child of `proc`.
    inner = proc.pid
    try:
        kids = Path(f"/proc/{proc.pid}/task/{proc.pid}/children").read_text().split()
        if kids:
            inner = int(kids[0])
    except OSError:
        pass
    info = {"name": name, "pid": inner, "outer_pid": proc.pid, "workspace": ws,
            "scanner": "10.66.0.1", "target": "10.66.0.2", "started": time.time()}
    (d / "lab.json").write_text(json.dumps(info))
    return info


def down(name: str = "default") -> bool:
    info = _info(name)
    if not info:
        return False
    for pid in (info["outer_pid"], info["pid"]):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    (_state() / name / "lab.json").unlink(missing_ok=True)
    return True


def labs() -> list[dict]:
    out = []
    for d in sorted(_state().iterdir()) if _state().exists() else []:
        i = _info(d.name)
        if i:
            out.append(i)
    return out


def enter_argv(name: str, command: list[str], target: bool = False) -> list[str]:
    """argv to run `command` in the lab's scanner side, or its target side."""
    info = _info(name) or up(name)
    if target:
        tpid = (_state() / name / "target.pid").read_text().strip()
        command = ["nsenter", "-t", tpid, "-n", "--", *command]
    # Raw-socket tools need CAP_NET_RAW after exec. These capabilities only
    # apply to the lab's own user/network namespaces, never to the host.
    # bubblewrap denies setgroups(2) in unprivileged user namespaces, so we
    # keep our credentials and carry the lab-namespace capabilities into the
    # command as ambient capabilities (util-linux >= 2.38).
    return ["nsenter", "-t", str(info["pid"]), "--user", "--net", "--mount", "--uts", "--pid",
            "--preserve-credentials", "--keep-caps", "--wd=" + info["workspace"], "--", *command]


def run(name: str, command: str, timeout: float = 300, target: bool = False) -> subprocess.CompletedProcess:
    argv = enter_argv(name, ["sh", "-c", command], target=target)
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(argv, 124, e.stdout or "", f"timed out after {timeout}s")


def describe() -> str:
    rows = labs()
    if not rows:
        return "no running labs (start one with `veil lab up [NAME]`)"
    return "\n".join(f"{r['name']:<12} pid={r['pid']} scanner={r['scanner']} target={r['target']} "
                     f"workspace={r['workspace']}" for r in rows)
