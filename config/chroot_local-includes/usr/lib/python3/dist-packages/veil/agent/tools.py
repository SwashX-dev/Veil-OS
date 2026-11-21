"""Tools available to Veil Agent.

Every tool result reports what actually happened (exit codes, errors). The
agent loop feeds these verbatim back to the model, and the UI shows them to
the human, so the model cannot claim a success that did not happen without
the human seeing the contradiction.
"""

from __future__ import annotations

import difflib
import json
import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from veil import common, sandbox
from veil.agent import policy as pol

MAX_OUTPUT = 12000


@dataclass
class ToolResult:
    ok: bool
    output: str
    data: dict = field(default_factory=dict)

    def for_model(self) -> str:
        out = self.output
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT // 2] + f"\n...[{len(out) - MAX_OUTPUT} chars truncated]...\n" + out[-MAX_OUTPUT // 2:]
        return json.dumps({"ok": self.ok, "output": out}, ensure_ascii=False)


@dataclass
class ToolContext:
    policy: pol.Policy
    # confirm(kind, summary) -> bool. Must ask the HUMAN; never the model.
    confirm: Callable[[str, str], bool]
    log: Callable[[dict], None] = lambda e: None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, ToolContext], ToolResult]
    levels: tuple[str, ...] = pol.LEVELS

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


def _clip(s: str, n: int = MAX_OUTPUT) -> str:
    return s if len(s) <= n else s[: n - 40] + f"\n...[truncated {len(s) - n} chars]"


# --------------------------------------------------------------------------
# Shell
# --------------------------------------------------------------------------


def _gate_command(command: str, ctx: ToolContext) -> ToolResult | None:
    """Ask the human before risky commands. Returns a refusal or None."""
    risks = pol.classify(command)
    for r in risks:
        if not ctx.confirm(r.kind, f"{r.reason}:\n    {command}"):
            return ToolResult(False, f"DENIED by user: command {r.reason}. Do not retry it; "
                                     "explain what you wanted to do instead.")
    if ctx.policy.network:
        oos = ctx.policy.out_of_scope_hosts(command)
        if oos and not ctx.confirm("network-scope", f"contacts hosts outside the granted scope {oos}:\n    {command}"):
            return ToolResult(False, f"DENIED by user: hosts {oos} are outside the network scope.")
    return None


def run_shell(args: dict, ctx: ToolContext) -> ToolResult:
    command = str(args.get("command", "")).strip()
    if not command:
        return ToolResult(False, "empty command")
    timeout = min(float(args.get("timeout", 120)), 1800)
    refusal = _gate_command(command, ctx)
    if refusal:
        return refusal
    cwd = args.get("cwd") or ctx.policy.workspace or os.path.expanduser("~")
    spec = ctx.policy.sandbox_spec(cwd=cwd)
    t0 = time.time()
    try:
        p = sandbox.run(spec, ["bash", "-c", command], timeout=timeout)
    except subprocess.TimeoutExpired:
        return ToolResult(False, f"timed out after {timeout:.0f}s", {"command": command})
    except sandbox.SandboxUnavailable as e:
        return ToolResult(False, f"sandbox unavailable: {e}")
    out = p.stdout + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")
    note = ""
    if p.returncode != 0 and not ctx.policy.network and pol.mentions_network(command):
        note = ("\n[veil] This command needs network access, which is DENIED for this session. "
                "Tell the user; only they can grant it with /net on.")
    return ToolResult(p.returncode == 0, _clip(f"exit={p.returncode} ({time.time() - t0:.1f}s)\n{out}{note}"),
                      {"command": command, "exit": p.returncode})


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


def _resolve(path: str, ctx: ToolContext) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(ctx.policy.workspace or os.path.expanduser("~")) / p
    return p


def read_file(args: dict, ctx: ToolContext) -> ToolResult:
    p = _resolve(args.get("path", ""), ctx)
    ok, why = ctx.policy.can_read(str(p))
    if not ok:
        if not ctx.confirm("credentials", f"read {p}"):
            return ToolResult(False, f"DENIED: {why}")
    try:
        text = p.read_text(errors="replace")
    except OSError as e:
        return ToolResult(False, f"{type(e).__name__}: {e}")
    lines = text.splitlines()
    start = max(1, int(args.get("start_line", 1)))
    count = min(int(args.get("max_lines", 400)), 2000)
    chunk = lines[start - 1: start - 1 + count]
    body = "\n".join(f"{i:5d}  {ln}" for i, ln in enumerate(chunk, start))
    more = f"\n[... {len(lines) - (start - 1 + count)} more lines]" if len(lines) > start - 1 + count else ""
    return ToolResult(True, _clip(f"{p} ({len(lines)} lines)\n{body}{more}"))


_WRITER = ("import os,sys\n"
           "p=sys.argv[1]\n"
           "os.makedirs(os.path.dirname(p) or '.',exist_ok=True)\n"
           "fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o644)\n"
           "os.write(fd,sys.stdin.buffer.read());os.close(fd)\n")


def _sandboxed_write(p: Path, content: str, ctx: ToolContext) -> str | None:
    """Write through the sandbox so the kernel's read-only mounts have the
    final say (closes symlink-swap races against the Python path check)."""
    r = sandbox.run(ctx.policy.sandbox_spec(), ["python3", "-I", "-c", _WRITER, str(p)],
                    input=content, timeout=60)
    if r.returncode != 0:
        return (r.stderr.strip().splitlines() or ["write failed"])[-1]
    return None


def _diff(old: str, new: str, path: Path) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        fromfile=f"a/{path.name}", tofile=f"b/{path.name}"))


def write_file(args: dict, ctx: ToolContext) -> ToolResult:
    p = _resolve(args.get("path", ""), ctx)
    ok, why = ctx.policy.can_write(str(p))
    if not ok:
        return ToolResult(False, f"DENIED: {why}")
    content = str(args.get("content", ""))
    old = p.read_text(errors="replace") if p.exists() else ""
    err = _sandboxed_write(p, content, ctx)
    if err:
        return ToolResult(False, f"write failed: {err}")
    d = _diff(old, content, p)
    return ToolResult(True, f"wrote {len(content)} bytes to {p}\n{_clip(d, 4000)}", {"path": str(p), "diff": d})


def edit_file(args: dict, ctx: ToolContext) -> ToolResult:
    p = _resolve(args.get("path", ""), ctx)
    ok, why = ctx.policy.can_write(str(p))
    if not ok:
        return ToolResult(False, f"DENIED: {why}")
    try:
        old = p.read_text()
    except OSError as e:
        return ToolResult(False, f"{type(e).__name__}: {e}")
    find, repl = str(args.get("old_text", "")), str(args.get("new_text", ""))
    n = old.count(find) if find else 0
    if n == 0:
        return ToolResult(False, "old_text not found; read the file and copy the exact text")
    if n > 1 and not args.get("replace_all"):
        return ToolResult(False, f"old_text occurs {n} times; add more context or set replace_all")
    new = old.replace(find, repl) if args.get("replace_all") else old.replace(find, repl, 1)
    err = _sandboxed_write(p, new, ctx)
    if err:
        return ToolResult(False, f"write failed: {err}")
    d = _diff(old, new, p)
    return ToolResult(True, f"edited {p}\n{_clip(d, 4000)}", {"path": str(p), "diff": d})


def list_dir(args: dict, ctx: ToolContext) -> ToolResult:
    p = _resolve(args.get("path", "."), ctx)
    ok, why = ctx.policy.can_read(str(p))
    if not ok:
        return ToolResult(False, f"DENIED: {why}")
    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as e:
        return ToolResult(False, f"{type(e).__name__}: {e}")
    rows = []
    for e in entries[:500]:
        try:
            st = e.lstat()
            kind = "d" if e.is_dir() else ("l" if e.is_symlink() else "-")
            rows.append(f"{kind} {st.st_size:>10}  {e.name}{'/' if e.is_dir() else ''}")
        except OSError:
            rows.append(f"? {'':>10}  {e.name}")
    return ToolResult(True, f"{p}\n" + "\n".join(rows) + (f"\n[{len(entries) - 500} more]" if len(entries) > 500 else ""))


def search_files(args: dict, ctx: ToolContext) -> ToolResult:
    pattern = str(args.get("pattern", ""))
    if not pattern:
        return ToolResult(False, "empty pattern")
    path = str(_resolve(args.get("path", "."), ctx))
    cmd = ["grep", "-rnI", "--color=never", "-m", "20", "-e", pattern, path]
    if args.get("glob"):
        cmd.insert(1, f"--include={args['glob']}")
    p = sandbox.run(ctx.policy.sandbox_spec(), cmd, timeout=60)
    out = p.stdout or "(no matches)"
    return ToolResult(p.returncode in (0, 1), _clip(out))


# --------------------------------------------------------------------------
# Git, diagnostics, logs, processes
# --------------------------------------------------------------------------


def git(args: dict, ctx: ToolContext) -> ToolResult:
    sub = str(args.get("args", "status")).strip()
    if re.search(r"[;&|`$<>]", sub):
        return ToolResult(False, "git args must not contain shell metacharacters; use the shell tool")
    return run_shell({"command": f"git {sub}", "cwd": args.get("cwd")}, ctx)


def system_info(args: dict, ctx: ToolContext) -> ToolResult:
    from veil.agent import models
    osr = common.read_os_release()
    hw = models.hardware()
    st = os.statvfs(os.path.expanduser("~"))
    info = {
        "os": f"{osr.get('PRETTY_NAME', platform.system())} (Tails base {osr.get('VERSION', '?')})",
        "kernel": platform.release(),
        "cpu": hw["cpu"], "threads": hw["threads"],
        "ram_total": common.human_bytes(hw["ram_total"]),
        "ram_available": common.human_bytes(hw["ram_available"]),
        "gpus": hw["gpus"], "vulkan": hw["vulkan"],
        "home_free": common.human_bytes(st.f_bavail * st.f_frsize),
        "tor_connected": common.tor_bootstrapped(),
        "persistent_storage": common.persistence_unlocked(),
        "uptime_s": int(float(Path("/proc/uptime").read_text().split()[0])) if Path("/proc/uptime").exists() else None,
    }
    return ToolResult(True, json.dumps(info, indent=1))


def process_list(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        import psutil
    except ImportError:
        p = subprocess.run(["ps", "-eo", "pid,user,%cpu,%mem,comm", "--sort=-%cpu"],
                           capture_output=True, text=True, check=False)
        return ToolResult(True, "\n".join(p.stdout.splitlines()[:30]))
    rows = []
    for pr in psutil.process_iter(["pid", "username", "cpu_percent", "memory_info", "name"]):
        i = pr.info
        rows.append((i["cpu_percent"] or 0, (i["memory_info"].rss if i["memory_info"] else 0),
                     i["pid"], i["username"] or "?", i["name"] or "?"))
    key = 1 if args.get("sort") == "memory" else 0
    rows.sort(key=lambda r: r[key], reverse=True)
    out = ["  PID USER            CPU%      RSS  NAME"]
    for c, m, pid, u, n in rows[: int(args.get("limit", 25))]:
        out.append(f"{pid:>5} {u[:14]:<14} {c:>5.1f} {common.human_bytes(m):>9}  {n}")
    return ToolResult(True, "\n".join(out))


def read_logs(args: dict, ctx: ToolContext) -> ToolResult:
    n = min(int(args.get("lines", 200)), 2000)
    cmd = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(n)]
    if args.get("unit"):
        cmd += ["-u", str(args["unit"])]
    if args.get("user_session"):
        cmd.insert(1, "--user")
    if args.get("priority"):
        cmd += ["-p", str(args["priority"])]
    r = common.run(cmd, timeout=30)
    out = r.stdout
    if args.get("grep"):
        rx = re.compile(str(args["grep"]), re.IGNORECASE)
        out = "\n".join(ln for ln in out.splitlines() if rx.search(ln))
    if not r.ok and not out:
        out = r.stderr or "no access to the system journal (try user_session=true)"
    return ToolResult(r.ok, _clip(out or "(no matching log lines)"))


def network_policy(args: dict, ctx: ToolContext) -> ToolResult:
    from veil.netguard import checks, report
    cs = checks.run_all(active_probes=bool(args.get("active_probes", False)))
    return ToolResult(True, report.to_json("Veil Network Guard", cs))


def security_audit(args: dict, ctx: ToolContext) -> ToolResult:
    from veil import audit
    from veil.netguard import report
    return ToolResult(True, report.to_json("Veil security audit", audit.run_all()))


DOC_DIRS = ["/usr/share/veil/docs", "/usr/share/doc/tails/website/doc", "/usr/share/doc/veil"]


def docs_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Offline documentation search: Veil docs, Tails offline docs, man pages."""
    q = str(args.get("query", "")).strip()
    if not q:
        return ToolResult(False, "empty query")
    terms = [t.lower() for t in re.findall(r"\w+", q) if len(t) > 2]
    hits = []
    for d in DOC_DIRS:
        for root, _dirs, files in os.walk(d):
            for f in files:
                if not f.endswith((".md", ".mdwn", ".html", ".txt")):
                    continue
                fp = os.path.join(root, f)
                try:
                    text = Path(fp).read_text(errors="ignore")
                except OSError:
                    continue
                low = text.lower()
                score = sum(low.count(t) for t in terms)
                if score:
                    i = min((low.find(t) for t in terms if t in low), default=0)
                    snippet = re.sub(r"<[^>]+>|\s+", " ", text[max(0, i - 160): i + 320]).strip()
                    hits.append((score, fp, snippet))
    hits.sort(reverse=True)
    out = [f"[{s}] {fp}\n    {snip}" for s, fp, snip in hits[:8]]
    man = common.run(["apropos", "--", q], timeout=10)
    if man.ok and man.stdout.strip():
        out.append("man pages:\n" + "\n".join(man.stdout.splitlines()[:10]))
    return ToolResult(True, "\n".join(out) or "no offline documentation matched")


def packages(args: dict, ctx: ToolContext) -> ToolResult:
    from veil import pkg
    action, name = str(args.get("action", "search")), str(args.get("name", ""))
    if action == "search":
        return ToolResult(True, pkg.describe_search(name))
    if action == "list":
        return ToolResult(True, pkg.describe_installed())
    if action in ("install", "remove"):
        if action == "install" and not ctx.policy.network:
            return ToolResult(False, "installing needs network access, which is DENIED for this session")
        if not ctx.confirm("package", f"{action} package '{name}' with Nix (runs as you, through Tor)"):
            return ToolResult(False, "DENIED by user")
        r = pkg.install([name]) if action == "install" else pkg.remove([name])
        return ToolResult(r.ok, _clip(r.stdout + r.stderr))
    return ToolResult(False, f"unknown action {action}")


def lab(args: dict, ctx: ToolContext) -> ToolResult:
    from veil import lab as labmod
    action = str(args.get("action", "list"))
    if action == "list":
        return ToolResult(True, labmod.describe())
    if action == "run":
        cmd = str(args.get("command", ""))
        refusal = _gate_command(cmd, ctx)
        if refusal:
            return refusal
        r = labmod.run(str(args.get("name", "default")), cmd, timeout=float(args.get("timeout", 300)))
        return ToolResult(r.returncode == 0, _clip(f"exit={r.returncode}\n{r.stdout}{r.stderr}"))
    return ToolResult(False, "actions: list, run")


def project_memory(args: dict, ctx: ToolContext) -> ToolResult:
    ws = Path(ctx.policy.workspace or os.path.expanduser("~"))
    mem = ws / ".veil" / "memory.md"
    action = str(args.get("action", "read"))
    if action == "read":
        return ToolResult(True, mem.read_text() if mem.exists() else "(no project memory yet)")
    if action == "append":
        ok, why = ctx.policy.can_write(str(mem))
        if not ok:
            return ToolResult(False, f"DENIED: {why}")
        mem.parent.mkdir(parents=True, exist_ok=True)
        with open(mem, "a") as f:
            f.write(f"- {time.strftime('%Y-%m-%d')} {str(args.get('text', '')).strip()}\n")
        return ToolResult(True, f"appended to {mem}")
    return ToolResult(False, "actions: read, append")


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}

ALL_TOOLS: list[Tool] = [
    Tool("shell", "Run a bash command in the Veil sandbox. Network is unavailable unless the user granted it. "
         "Returns exit code, stdout and stderr.",
         _obj({"command": S, "cwd": S, "timeout": I}, ["command"]), run_shell),
    Tool("read_file", "Read a text file with line numbers.",
         _obj({"path": S, "start_line": I, "max_lines": I}, ["path"]), read_file),
    Tool("write_file", "Create or overwrite a file (workspace/full-control only). Returns a diff.",
         _obj({"path": S, "content": S}, ["path", "content"]), write_file, ("workspace", "full-control")),
    Tool("edit_file", "Replace exact text in a file (workspace/full-control only). Returns a diff.",
         _obj({"path": S, "old_text": S, "new_text": S, "replace_all": B}, ["path", "old_text", "new_text"]),
         edit_file, ("workspace", "full-control")),
    Tool("list_dir", "List a directory.", _obj({"path": S}, []), list_dir),
    Tool("search_files", "Search file contents (grep -rn).", _obj({"pattern": S, "path": S, "glob": S}, ["pattern"]),
         search_files),
    Tool("git", "Run a git subcommand, e.g. 'status', 'diff', 'log --oneline -5'.",
         _obj({"args": S, "cwd": S}, ["args"]), git),
    Tool("system_info", "OS, kernel, CPU, RAM, GPU, Tor and persistence status.", _obj({}, []), system_info),
    Tool("process_list", "Top processes by CPU or memory.", _obj({"sort": S, "limit": I}, []), process_list),
    Tool("read_logs", "Read journal logs (optionally filtered).",
         _obj({"unit": S, "lines": I, "grep": S, "priority": S, "user_session": B}, []), read_logs),
    Tool("network_policy", "Veil Network Guard report: firewall, Tor, DNS/IPv6 leaks, connections.",
         _obj({"active_probes": B}, []), network_policy),
    Tool("security_audit", "Local security audit: amnesia, hardening, sandbox self-test.", _obj({}, []), security_audit),
    Tool("docs_search", "Search offline documentation (Veil, Tails, man pages).", _obj({"query": S}, ["query"]),
         docs_search),
    Tool("packages", "Search/list/install/remove Nix packages and Veil bundles.",
         _obj({"action": {"type": "string", "enum": ["search", "list", "install", "remove"]}, "name": S}, ["action"]),
         packages),
    Tool("lab", "Offline lab namespaces for tools that cannot use Tor (no Internet route).",
         _obj({"action": {"type": "string", "enum": ["list", "run"]}, "name": S, "command": S, "timeout": I},
              ["action"]), lab),
    Tool("project_memory", "Read or append notes in the workspace's .veil/memory.md.",
         _obj({"action": {"type": "string", "enum": ["read", "append"]}, "text": S}, ["action"]), project_memory),
]


def tools_for(level: str, mode: str) -> list[Tool]:
    if mode == "direct":
        return []
    allowed = [t for t in ALL_TOOLS if level in t.levels]
    if mode == "research":
        allowed = [t for t in allowed if t.name not in ("write_file", "edit_file", "packages")]
    return allowed


def by_name(name: str) -> Tool | None:
    return next((t for t in ALL_TOOLS if t.name == name), None)
