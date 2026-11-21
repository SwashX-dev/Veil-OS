"""APT compatibility and explicit Tails Additional Software operations."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

APT = "/usr/bin/apt"
APT_GET = "/usr/bin/apt-get"
SUDO = "/usr/bin/sudo"
WRAPPER = "/usr/local/bin/apt"
PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]+(?::[a-z0-9][a-z0-9-]*)?")
SAFE_OPTIONS = {"-y", "--yes", "--assume-yes", "--no-install-recommends", "--install-recommends"}
NATIVE_ARCH = os.uname().machine.replace("x86_64", "amd64")
BASE_STATUS = Path("/usr/lib/live/mount/rootfs/filesystem.squashfs/var/lib/dpkg/status")
ENV = {**os.environ, "VEIL_APT_EXPLICIT": "1"}


class PackageError(Exception):
    pass


def run(argv, env=ENV, **kwargs):
    return subprocess.run(argv, check=True, env=env, text=True, **kwargs)


def apt_main():
    args = sys.argv[1:]
    options, operands = split_options(args)
    if "-Pr" not in options:
        # Standard APT gets the original argv, including options and selectors.
        # Offer sudo for the simple interactive forms advertised by Veil.
        if (os.geteuid() != 0 and args and args[0] in ("install", "remove")
                and len(args) > 1 and all(PACKAGE.fullmatch(a) for a in args[1:])):
            os.execv(SUDO, [SUDO, "--", WRAPPER, *args])
        if args and args[0] in ("install", "remove") and not any(
                option in options for option in ("-s", "--simulate", "--dry-run", "--just-print",
                                                 "--recon", "--no-act", "--print-uris", "-d", "--download-only")):
            print("APT is operating on this live session. Persistent registrations are preserved.", flush=True)
        os.execve(APT, [APT, *args], ENV)

    try:
        verbs = [arg for arg in options if not arg.startswith("-")]
        if not verbs or verbs[0] not in {"install", "remove"}:
            raise PackageError("Use -Pr with apt install or apt remove.")
        flags = [arg for arg in options if arg.startswith("-") and arg != "-Pr"]
        if set(flags) - SAFE_OPTIONS or options.count("-Pr") != 1:
            raise PackageError("Use -Pr once, with package names and optional -y or APT recommends options.")
        return execute(verbs[0], [*verbs[1:], *operands], flags)
    except (PackageError, OSError, subprocess.CalledProcessError) as exc:
        return report_error(exc)


def split_options(args):
    """Split at APT's end-of-options separator."""
    if "--" in args:
        index = args.index("--")
        return args[:index], args[index + 1:]
    return args, []


def state():
    from tailslib.persistence import (PERSISTENCE_DIR, additional_software_persistence_feature_is_active,
                                      has_persistence, has_unlocked_persistence)

    if not has_unlocked_persistence():
        if has_persistence():
            return "Persistent Storage is locked. Unlock it from the Welcome Screen."
        return "Create encrypted Persistent Storage using the Persistent Storage app."
    if not os.path.ismount(PERSISTENCE_DIR):
        return "Persistent Storage has no active encrypted mount. Reboot and unlock it from the Welcome Screen."
    if not additional_software_persistence_feature_is_active() or not all(
            os.path.ismount(path) for path in ("/var/cache/apt/archives", "/var/lib/apt/lists")):
        return "Enable Additional Software in the Persistent Storage app."
    return ""


def registered():
    import additional_software as asp

    try:
        return asp.get_additional_packages()
    except (asp.ASPDataError, UnicodeError) as exc:
        raise PackageError(f"Invalid Additional Software configuration. Preserve a copy of "
                           f"{asp.get_packages_list_path()} and correct the invalid entry: {exc}") from exc


def simulate(command, specs, base=False):
    args = [APT_GET, "-s"]
    if base:
        if not BASE_STATUS.is_file():
            raise PackageError("Cannot verify restoration against the immutable package manifest.")
        args += ["-o", f"Dir::State::status={BASE_STATUS}", "-o", "Dir::State::extended_states=/dev/null"]
    if command == "install":
        args.append("--no-remove")
    try:
        result = run([*args, command, "--", *specs], capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise PackageError((exc.stderr or exc.stdout).strip() or "APT could not resolve the requested packages.") from exc
    return [line.split()[1] for line in result.stdout.splitlines() if line.startswith(("Inst ", "Remv "))]


def is_installed(name, base=False):
    args = ["/usr/bin/dpkg-query"]
    if base:
        args.append(f"--admindir={BASE_STATUS.parent}")
    result = subprocess.run([*args, "-W", "-f=${Status}", "--", name],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return result.returncode == 0 and result.stdout.endswith(" ok installed")


def matches(spec, name):
    """Use APT's package name and optional architecture, ignoring legacy pins."""
    import additional_software as asp

    def qualify(package):
        base, separator, arch = package.partition(":")
        return base, (arch if separator and arch != "all" else NATIVE_ARCH)

    return qualify(asp.package_identity(spec)) == qualify(name)


def execute(action, names=(), options=()):
    import additional_software as asp

    if action in ("install", "remove") and (not names or not all(PACKAGE.fullmatch(n) for n in names)):
        raise PackageError("Supply Debian package names, optionally with :architecture.")
    problem = state()
    if action == "status" and problem:
        print(problem)
        return 1
    if problem:
        raise PackageError(problem)
    if action in ("list", "status", "verify"):
        return inspect(action)
    if os.geteuid() != 0:
        if action == "repair":
            target = ["/usr/local/bin/veil", "packages", "persistent", "repair"]
        else:
            target = [WRAPPER, action, *names, "-Pr", *options]
        os.execv(SUDO, [SUDO, "--", *target])

    with asp.lock_config():
        if problem := state():
            raise PackageError(problem)
        before = registered()
        if action == "repair":
            run(["/usr/bin/dpkg", "--configure", "-a"])
            run([APT_GET, "--no-remove", "--fix-broken", "install"])
            if before:
                run([APT_GET, "--no-remove", "install", "--", *sorted(before)])
            return inspect("verify")

        # Exact cache lookup rejects virtual names, APT patterns and trailing
        # '+'/'-' action syntax when those strings are not real package names.
        import apt

        cache = apt.Cache()
        for name in names:
            if name not in cache and not (action == "remove" and any(matches(spec, name) for spec in before)):
                raise PackageError(f"No exact package named {name} in APT's index. Try sudo apt update.")
        del cache
        after = {spec for spec in before if not any(matches(spec, name) for name in names)}
        if action == "install":
            after.update(names)
            simulate("install", sorted(after), base=True)
            run([APT_GET, "--no-remove", *options, "install", "--", *names])
        else:
            installed_names = [name for name in names if is_installed(name)]
            planned_removals = simulate("remove", installed_names) if installed_names else []
            extras = [item for item in planned_removals if not any(matches(item, name) for name in names)]
            if extras:
                raise PackageError("APT would also remove: " + ", ".join(extras))
            restored = simulate("install", sorted(after), base=True) if after else []
            if required := [item for item in restored if any(matches(item, name) for name in names)]:
                raise PackageError("Other registered packages would restore: " + ", ".join(required))
            if installed_names:
                env = {**ENV, "VEIL_APT_REMOVE_ONLY": json.dumps(installed_names)}
                run([APT_GET, *options, "remove", "--", *installed_names], env=env)

        missing = [name for name in names if is_installed(name) != (action == "install")]
        if missing:
            raise PackageError("Unexpected live package state: " + ", ".join(missing))
        if problem := state():
            raise PackageError(problem)
        if after != before:
            asp.write_config(after)
        if registered() != after:
            raise PackageError("Could not verify the saved Additional Software configuration.")

    if action == "remove":
        for name in names:
            if is_installed(name, base=True):
                print(f"{name} is part of the immutable image and will be present after reboot.")
    print("Additional Software registrations saved and verified.")
    return 0


def inspect(action):
    import additional_software as asp

    specs = registered()
    if action == "list":
        print("\n".join(sorted(specs)))
        return 0
    missing = [spec for spec in sorted(specs) if not is_installed(asp.package_identity(spec))]
    print(f"Additional Software is active. {len(specs)} registered, {len(missing)} absent in this session.")
    if missing:
        print("Absent in this session: " + ", ".join(missing))
    if action == "verify" and specs:
        simulate("install", sorted(specs), base=True)
        print("APT resolved the registered packages against the immutable image.")
        if missing:
            return 1
    return 0


def report_error(exc):
    print(f"Veil packages: {exc}", file=sys.stderr)
    if isinstance(exc, subprocess.CalledProcessError):
        print("Check the APT output for live package changes. Retry the command after resolving the error.",
              file=sys.stderr)
        return exc.returncode if exc.returncode > 0 else 1
    return 1


def persistent_cli(args):
    try:
        actions = {"add": "install", "remove": "remove"}
        return execute(actions.get(args.action, args.action), getattr(args, "names", ()))
    except (PackageError, OSError, subprocess.CalledProcessError) as exc:
        return report_error(exc)
