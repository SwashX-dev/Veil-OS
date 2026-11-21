"""`veil` — Veil OS command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys

from veil import __version__, common
from veil.common import bold, cyan, dim, red, violet, yellow


def _confirm(prompt: str, word: str | None = None) -> bool:
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(yellow(prompt) + (f"\n  Type {bold(word)} to continue: " if word else " [y/N]: "))
            tty.flush()
            a = tty.readline().strip()
            return a == word if word else a.lower() in ("y", "yes")
    except OSError:
        return False


# ---------------------------------------------------------------- packages


def cmd_install(a) -> int:
    from veil import pkg, tools_catalog
    for w in pkg.preflight(installing=True):
        print((red if w.startswith("ERROR") else yellow)(f"  {w}"))
        if w.startswith("ERROR"):
            return 1
    for b in pkg.unfree_bundles(a.names):
        print(yellow(f"\n  {b['name']}: {b['description']}\n  License: {b.get('license_url', 'see upstream')}"))
        if not _confirm("  This software is not free software. Accept its license?", "ACCEPT"):
            return 1
    for n in a.names:
        t = tools_catalog.get(n)
        if t and t["network_policy"] == "offline-lab":
            print(yellow(f"  {n}: cannot work over Tor; Veil will only let it reach offline labs (veil lab)."))
    print(dim("  Downloading signed binaries from cache.nixos.org through Tor..."))
    r = pkg.install(a.names)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    print(cyan("  ✓ installed") if r.ok else red("  ✗ install failed"))
    return 0 if r.ok else 1


def cmd_remove(a) -> int:
    from veil import pkg
    r = pkg.remove(a.names)
    sys.stdout.write(r.stdout + r.stderr)
    return 0 if r.ok else 1


def cmd_search(a) -> int:
    from veil import pkg
    if a.remote:
        for w in pkg.preflight(installing=False):
            print(yellow(f"  {w}"))
    print(pkg.describe_search(a.term, remote=a.remote))
    return 0


def cmd_update(a) -> int:
    from veil import pkg
    for w in pkg.preflight(installing=False):
        print((red if w.startswith("ERROR") else yellow)(f"  {w}"))
        if w.startswith("ERROR"):
            return 1
    r = pkg.update()
    sys.stdout.write(r.stdout + r.stderr)
    print(dim("  Veil OS itself is updated by installing a new verified image; see `veil version --check`."))
    return 0 if r.ok else 1


def cmd_list(a) -> int:
    from veil import pkg
    print(pkg.describe_installed())
    return 0


# ---------------------------------------------------------------- privacy / security


def cmd_privacy(a) -> int:
    from veil.netguard import checks, report
    cs = checks.run_all(active_probes=not a.no_probes)
    if a.json:
        print(report.to_json("Veil Network Guard", cs))
    else:
        print(report.to_text("Veil Network Guard — privacy status", cs, verbose=a.verbose))
    return {"pass": 0, "warn": 0, "fail": 2}[report.summarize(cs)["verdict"]]


def cmd_audit(a) -> int:
    from veil import audit
    from veil.netguard import checks, report
    cs = audit.run_all() + checks.run_all(active_probes=not a.no_probes)
    if a.json:
        print(report.to_json("Veil security audit", cs))
    else:
        print(report.to_text("Veil security audit", cs, verbose=a.verbose))
    return {"pass": 0, "warn": 0, "fail": 2}[report.summarize(cs)["verdict"]]


# ---------------------------------------------------------------- tools / lab


def cmd_tools(a) -> int:
    from veil import tools_catalog as tc
    if a.name:
        t = tc.get(a.name)
        if not t:
            common.die(f"unknown tool {a.name}")
        print(violet(bold(t["name"])) + f"  {t['description']}")
        print(f"  category : {t['category']}\n  source   : {t['source']}\n  mode     : {t['mode']}")
        print(f"  network  : {t['network_policy']} — {tc.NETWORK_POLICIES[t['network_policy']]}")
        print(f"  privacy  : {t['privacy']} — {tc.PRIVACY_STATUS[t['privacy']]}")
        if t.get("notes"):
            print(dim(f"  notes    : {t['notes']}"))
        return 0
    cat = None
    for t in sorted(tc.load(), key=lambda x: (x["category"], x["name"])):
        if t["category"] != cat:
            cat = t["category"]
            print(violet(f"\n{cat}"))
        src = "base" if t["source"] in ("base", "tails") else t["source"].replace("nix:bundle:", "bundle:")
        print(f"  {t['name']:<14} {src:<28} net={t['network_policy']:<13} {t['privacy']}")
    return 0


def cmd_lab(a) -> int:
    from veil import lab
    if a.action == "up":
        i = lab.up(a.name, a.workspace)
        print(cyan(f"lab '{i['name']}' running: scanner 10.66.0.1, target 10.66.0.2, workspace {i['workspace']}"))
        print(dim("No route to Tor, the LAN or the Internet exists inside the lab."))
    elif a.action == "down":
        print("stopped" if lab.down(a.name) else "not running")
    elif a.action == "list":
        print(lab.describe())
    elif a.action == "shell":
        argv = lab.enter_argv(a.name, [os.environ.get("SHELL", "/bin/bash")])
        os.execvp(argv[0], argv)
    elif a.action in ("run", "target"):
        if not a.command:
            common.die(f"usage: veil lab {a.action} NAME -- COMMAND...")
        argv = lab.enter_argv(a.name, a.command, target=a.action == "target")
        os.execvp(argv[0], argv)
    return 0


# ---------------------------------------------------------------- misc


def cmd_version(a) -> int:
    osr = common.read_os_release()
    info = {"veil": osr.get("VEIL_VERSION", __version__), "tails_base": osr.get("VERSION", "?"),
            "channel": osr.get("VEIL_CHANNEL", "alpha"), "git": osr.get("VEIL_GIT_COMMIT", "?")}
    print(json.dumps(info, indent=1) if a.json else
          f"Veil OS {info['veil']} ({info['channel']}), based on Tails {info['tails_base']}, commit {info['git']}")
    if a.check:
        from veil import update
        print(update.describe())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "agent":
        from veil.agent import cli as agent_cli
        return agent_cli.main(argv[1:])
    ap = argparse.ArgumentParser(prog="veil", description="Veil OS command-line interface")
    ap.add_argument("--version", action="version", version=f"veil {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    p = sub.add_parser("install", help="install Nix packages or bundles (e.g. bundle:reverse-engineering)")
    p.add_argument("names", nargs="+")
    p.set_defaults(fn=cmd_install)
    p = sub.add_parser("remove", help="remove Nix packages or bundles")
    p.add_argument("names", nargs="+")
    p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("search", help="search bundles, the tool catalog, or nixpkgs (--remote)")
    p.add_argument("term")
    p.add_argument("--remote", action="store_true", help="also search nixpkgs (downloads index through Tor)")
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("update", help="upgrade installed Nix packages")
    p.set_defaults(fn=cmd_update)
    p = sub.add_parser("list", help="list installed Nix packages")
    p.set_defaults(fn=cmd_list)

    from veil.apt_packages import persistent_cli

    p = sub.add_parser("packages", help="manage Debian Additional Software")
    packages = p.add_subparsers(dest="packages_cmd", required=True)
    p = packages.add_parser("persistent", help="manage packages restored from Persistent Storage")
    actions = p.add_subparsers(dest="action", required=True)
    for action in ("list", "add", "remove", "repair", "status", "verify"):
        command = actions.add_parser(action)
        if action in ("add", "remove"):
            command.add_argument("names", nargs="+", metavar="PACKAGE")
        command.set_defaults(fn=persistent_cli)

    p = sub.add_parser("privacy", help="privacy status (Veil Network Guard)")
    p.add_argument("what", nargs="?", default="status", choices=["status"])
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--no-probes", action="store_true", help="skip active leak probes")
    p.set_defaults(fn=cmd_privacy)
    p = sub.add_parser("security", help="security audit")
    p.add_argument("what", nargs="?", default="audit", choices=["audit"])
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--no-probes", action="store_true")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("tools", help="security tool catalog with network/privacy policy")
    p.add_argument("name", nargs="?")
    p.set_defaults(fn=cmd_tools)
    p = sub.add_parser("lab", help="offline lab namespaces for Tor-incompatible tools")
    p.add_argument("action", choices=["up", "down", "list", "shell", "run", "target"])
    p.add_argument("name", nargs="?", default="default")
    p.add_argument("--workspace")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_lab)

    sub.add_parser("agent", help="Veil Agent (local AI). See `veil agent --help`")
    p = sub.add_parser("version", help="show Veil version")
    p.add_argument("--json", action="store_true")
    p.add_argument("--check", action="store_true", help="check GitHub releases through Tor")
    p.set_defaults(fn=cmd_version)

    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 0
    if a.cmd == "lab" and a.command and a.command[0] == "--":
        a.command = a.command[1:]
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
