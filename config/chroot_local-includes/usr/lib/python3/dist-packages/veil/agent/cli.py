"""`veil agent` — terminal interface for Veil Agent."""

from __future__ import annotations

import argparse
import json
import os
import sys

from veil import common
from veil.agent import backend as be
from veil.agent import models, session
from veil.agent import policy as pol
from veil.agent.loop import UI, Agent
from veil.common import bold, cyan, dim, red, violet, yellow

BANNER = r"""
 ╲ ╱  veil agent — local, offline by default
  V   type /help for commands, /exit to quit
"""

CONFIRM_WORDS = {
    "full-control": "FULL CONTROL",
    "network": "ALLOW NETWORK",
    "secrets": "SHOW SECRETS",
    "destructive-disk": "ERASE",
}


def tty_confirm(kind: str, summary: str) -> bool:
    """Ask the human on the controlling terminal. Never reads stdin."""
    try:
        with open("/dev/tty", "r+") as tty:
            word = CONFIRM_WORDS.get(kind)
            tty.write(yellow(f"\n⚠ Veil Agent requests permission [{kind}]\n  {summary}\n"))
            if word:
                tty.write(f"  Type {bold(word)} to allow, anything else to deny: ")
            else:
                tty.write("  Allow? [y/N]: ")
            tty.flush()
            answer = tty.readline().strip()
            return answer == word if word else answer.lower() in ("y", "yes")
    except OSError:
        return False  # no terminal: deny


def _progress(done: int, total: int) -> None:
    pct = done * 100 // max(total, 1)
    sys.stderr.write(f"\r  {pct:3d}%  {common.human_bytes(done)} / {common.human_bytes(total)}")
    sys.stderr.flush()


def cmd_models(a) -> int:
    if a.action == "list":
        hw = models.hardware()
        rec = models.recommend(hw)
        local = {m["name"]: m for m in models.local_models()}
        print(violet(bold("Catalog")) + dim("  (downloads are never automatic)"))
        for m in models.catalog().values():
            tag = cyan(" installed") if m.name in local else ""
            star = violet(" ★ recommended") if m.name == rec.name else ""
            print(f"  {m.name:<18} {m.params:>5}  {common.human_bytes(m.size):>9}  "
                  f"needs ~{m.min_ram_gb:.1f} GiB  {m.license}{tag}{star}")
            print(dim(f"  {'':<18} {m.notes}"))
        extra = [m for n, m in local.items() if not m["catalog"]]
        for m in extra:
            print(f"  {m['name']:<18} local file {common.human_bytes(m['size'])}  {m['file']}")
        d, persistent = models.model_dir(create=False)
        print(dim(f"\n  Model directory: {d} ({'Persistent Storage, encrypted' if persistent else 'RAM-backed home, erased at shutdown'})"))
        return 0
    if a.action == "recommend":
        hw = models.hardware()
        rec = models.recommend(hw)
        print(json.dumps({**hw, "ram_total": common.human_bytes(hw["ram_total"]),
                          "ram_available": common.human_bytes(hw["ram_available"]),
                          "recommended": rec.name}, indent=1))
        return 0
    if a.action == "pull":
        if not a.name:
            common.die("usage: veil agent models pull NAME")

        def consent(spec, dest, persistent):
            where = "Persistent Storage (encrypted)" if persistent else "your RAM-backed home folder (erased at shutdown; unlock Persistent Storage to keep it)"
            return tty_confirm("download", f"Download {spec.title} ({common.human_bytes(spec.size)}, {spec.license}) "
                                           f"over Tor from huggingface.co\n  to {where}\n  SHA-256 {spec.sha256}")
        try:
            p = models.pull(a.name, consent, progress=_progress)
        except models.ConsentDenied:
            print(red("\ncancelled"))
            return 1
        except (KeyError, RuntimeError, OSError) as e:
            common.die(str(e))
        print(cyan(f"\n✓ verified and installed: {p}"))
        return 0
    if a.action == "verify":
        targets = [a.name] if a.name else [m["name"] for m in models.local_models()]
        rc = 0
        for t in targets:
            ok, msg = models.verify(t)
            print(f"  {common.fmt_status('pass' if ok else 'fail')} {t}: {msg}")
            rc |= 0 if ok else 1
        return rc
    if a.action == "remove":
        print("removed" if a.name and models.remove(a.name) else "not found")
        return 0
    return 2


def cmd_sessions(a) -> int:
    if a.action == "list":
        rows = session.list_saved()
        if not rows:
            print(dim("No saved sessions (sessions are disposable unless you /save them to Persistent Storage)."))
        for r in rows:
            print(f"  {r['name']:<32} {r['mode']:<9} {r['messages']:>3} msgs  {r['first']}")
    elif a.action == "delete":
        print("deleted" if session.delete_saved(a.name or "") else "not found")
    elif a.action == "wipe":
        if not tty_confirm("wipe", "Delete ALL agent sessions and execution logs (RAM and Persistent Storage)"):
            return 1
        print(session.wipe_all())
    return 0


def build_backend(a):
    if a.backend == "ollama":
        return be.OllamaBackend(a.model or "qwen3:4b", context=a.context)
    model = a.model
    path = models.resolve(model) if model else None
    if path is None and not model:
        installed = models.local_models()
        if installed:
            rec = models.recommend()
            pick = next((m for m in installed if m["name"] == rec.name), installed[0])
            path = models.resolve(pick["file"])
    if path is None:
        common.die("No model installed. Veil never downloads models automatically.\n"
                   "  See: veil agent models list\n  Then: veil agent models pull <name>   (asks before downloading, over Tor)")
    gpu_layers = a.gpu_layers
    if gpu_layers is None:
        gpu_layers = 0
    return be.LlamaCppBackend(path, context=a.context, threads=a.threads, gpu_layers=gpu_layers,
                              mem_limit=a.mem_limit, cpu_quota=a.cpu_quota)


def make_policy(a) -> pol.Policy:
    d = pol.load_defaults()
    level = a.level or d["level"]
    if level == "full-control" and not tty_confirm(
            "full-control", "Full-control mode lets the agent write anywhere in your home directory "
                            "(network stays OFF unless separately granted)"):
        print(yellow("full control not granted; using workspace mode"))
        level = "workspace"
    p = pol.Policy(level=level, workspace=a.workspace, max_steps=a.max_steps or d["max_steps"])
    if a.allow_network:
        if tty_confirm("network", "Allow tool commands to use the network (through Tor) for this session"
                       + (f", limited to {a.scope}" if a.scope else "")):
            p.network = True
            p.network_scope = a.scope or []
    if a.secrets and tty_confirm("secrets", "Expose ~/.gnupg, ~/.ssh, keyrings and browser data to tool commands"):
        p.secrets = True
    return p


class TermUI(UI):
    def __init__(self, stream: bool = True):
        self.streaming = stream
        self._in_think = False
        super().__init__(on_token=self._token if stream else None, confirm=tty_confirm,
                         on_tool_start=self._tool_start, on_tool_end=self._tool_end)

    def _token(self, t: str) -> None:
        # Hide <think> blocks while streaming.
        if "<think>" in t:
            self._in_think = True
        if not self._in_think:
            sys.stdout.write(t)
            sys.stdout.flush()
        if "</think>" in t:
            self._in_think = False

    def _tool_start(self, name, args):
        short = json.dumps(args, ensure_ascii=False)
        print(dim(f"\n  ⚙ {name} {short[:200]}"))

    def _tool_end(self, name, r):
        mark = cyan("✓") if r.ok else red("✗")
        first = (r.output.strip().splitlines() or [""])[0]
        print(dim(f"  {mark} {first[:160]}"))
        if r.data.get("diff"):
            for ln in r.data["diff"].splitlines()[:40]:
                print(dim("    ") + (cyan(ln) if ln.startswith("+") else red(ln) if ln.startswith("-") else dim(ln)))


HELP = """Commands:
  /mode direct|agent|research|terminal   change interaction mode
  /level read-only|workspace|full-control
  /workspace PATH      set the writable workspace
  /net on [host,...]   grant network (through Tor) — asks you to confirm
  /net off             revoke network
  /status              permissions, model, resources
  /save [NAME]         save this session to Persistent Storage
  /clear               start a fresh disposable session
  /unload              stop the model server (frees RAM)
  /exit                quit (session is discarded unless saved)"""


def _print_final(ui: TermUI, out: str) -> None:
    """Streamed tokens are already on screen; print only what was not."""
    if ui.streaming:
        print()
        if out.startswith("[veil]"):
            print(yellow(out))
    else:
        print(out)


def repl(a) -> int:
    policy = make_policy(a)
    backend = build_backend(a)
    sess = session.Session(mode=a.mode)
    ui = TermUI(stream=not a.no_stream)
    agent = Agent(backend=backend, policy=policy, mode=a.mode, session=sess, ui=ui,
                  temperature=a.temperature, thinking=a.think)
    try:
        if a.prompt:
            print(dim(f"[{agent.mode} · {policy.describe()}]"))
            out = agent.run(" ".join(a.prompt))
            _print_final(ui, out)
            return 0
        print(violet(BANNER))
        print(dim(f"  model: {getattr(backend, 'model_path', getattr(backend, 'model', '?'))}"))
        print(dim(f"  {policy.describe()}  mode={agent.mode}"))
        print(dim("  history is disposable (RAM) unless you /save it\n"))
        while True:
            try:
                line = input(violet("veil ❯ ")).strip()
            except EOFError:
                break
            if not line:
                continue
            if line.startswith("/"):
                if not handle_command(line, agent, backend):
                    break
                continue
            try:
                out = agent.run(line)
            except be.BackendError as e:
                print(red(f"\n[backend] {e}"))
                continue
            _print_final(ui, out)
            print()
    except KeyboardInterrupt:
        print()
    finally:
        agent.close()
        backend.stop()
        sess.discard()
    return 0


def handle_command(line: str, agent: Agent, backend) -> bool:
    parts = line.split()
    cmd, args = parts[0], parts[1:]
    if cmd in ("/exit", "/quit"):
        return False
    if cmd == "/help":
        print(HELP)
    elif cmd == "/mode" and args and args[0] in ("direct", "agent", "research", "terminal"):
        agent.mode = args[0]
        agent.session.mode = args[0]
        print(dim(f"mode={agent.mode}"))
    elif cmd == "/level" and args and args[0] in pol.LEVELS:
        if args[0] == "full-control" and not tty_confirm("full-control", "Let the agent write anywhere in your home"):
            return True
        agent.policy.level = args[0]
        if args[0] == "workspace" and not agent.policy.workspace:
            agent.policy.workspace = os.getcwd()
        print(dim(agent.policy.describe()))
    elif cmd == "/workspace" and args:
        agent.policy.workspace = os.path.realpath(os.path.expanduser(args[0]))
        print(dim(agent.policy.describe()))
    elif cmd == "/net" and args:
        if args[0] == "on":
            scope = args[1].split(",") if len(args) > 1 else []
            if tty_confirm("network", "Allow tool commands to use the network through Tor"
                           + (f" (scope: {scope})" if scope else " (any host)")):
                agent.set_network(True, scope)
        else:
            agent.set_network(False)
        print(dim(agent.policy.describe()))
    elif cmd == "/status":
        hw = __import__("veil.agent.models", fromlist=["hardware"]).hardware()
        print(dim(f"{agent.policy.describe()} mode={agent.mode}\n"
                  f"backend={backend.name} running={backend.running}\n"
                  f"RAM available {common.human_bytes(hw['ram_available'])} / {common.human_bytes(hw['ram_total'])}\n"
                  f"Tor connected={common.tor_bootstrapped()} persistence={common.persistence_unlocked()}"))
    elif cmd == "/save":
        try:
            print(cyan(f"saved to {agent.session.save(args[0] if args else None)}"))
        except session.PersistenceLocked as e:
            print(yellow(str(e)))
    elif cmd == "/clear":
        agent.session.discard()
        agent.session = session.Session(mode=agent.mode)
        print(dim("new disposable session"))
    elif cmd == "/unload":
        backend.stop()
        print(dim("model unloaded; it will reload on the next message"))
    else:
        print(HELP)
    return True


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "models":
        m = argparse.ArgumentParser(prog="veil agent models")
        m.add_argument("action", choices=["list", "recommend", "pull", "verify", "remove"], nargs="?",
                       default="list")
        m.add_argument("name", nargs="?")
        return cmd_models(m.parse_args(argv[1:]))
    if argv and argv[0] == "sessions":
        s = argparse.ArgumentParser(prog="veil agent sessions")
        s.add_argument("action", choices=["list", "delete", "wipe"], nargs="?", default="list")
        s.add_argument("name", nargs="?")
        return cmd_sessions(s.parse_args(argv[1:]))
    ap = argparse.ArgumentParser(prog="veil agent", description="Fully local AI agent (offline by default)",
                                 epilog="Subcommands: `veil agent models [list|recommend|pull|verify|remove]`, "
                                        "`veil agent sessions [list|delete|wipe]`")
    for p in (ap,):
        p.add_argument("prompt", nargs="*", help="one-shot prompt (non-interactive)")
        p.add_argument("--mode", default=pol.load_defaults()["mode"],
                       choices=["direct", "agent", "research", "terminal"])
        p.add_argument("--level", choices=list(pol.LEVELS))
        p.add_argument("--workspace")
        p.add_argument("--allow-network", action="store_true", help="ask to grant network for this session")
        p.add_argument("--scope", type=lambda v: v.split(","), help="comma-separated host scope for network")
        p.add_argument("--secrets", action="store_true", help="ask to expose credential stores")
        p.add_argument("--model")
        p.add_argument("--backend", choices=["llama.cpp", "ollama"], default="llama.cpp")
        p.add_argument("--context", type=int, default=pol.load_defaults()["context"])
        p.add_argument("--threads", type=int)
        p.add_argument("--gpu-layers", type=int)
        p.add_argument("--mem-limit", help="cgroup memory limit for the model server, e.g. 6G")
        p.add_argument("--cpu-quota", help="cgroup CPU quota, e.g. 400%%")
        p.add_argument("--temperature", type=float, default=pol.load_defaults()["temperature"])
        p.add_argument("--max-steps", type=int)
        p.add_argument("--think", action="store_true", help="enable model reasoning (slower)")
        p.add_argument("--no-stream", action="store_true")
    return repl(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
