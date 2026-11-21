"""Tails Additional Software configuration file management."""

from contextlib import contextmanager
import fcntl
import gettext
import grp
import logging
import os
import os.path
import tempfile
import uuid
from pathlib import Path
import pwd
import re
import subprocess

import systemd.journal

from tailslib import PERSISTENT_STORAGE_USERNAME
from tailslib.persistence import get_persistence_path, PERSISTENCE_DIR
from tailslib.utils import run_with_user_env

PACKAGES_STATE_DIR = "/run/live-additional-software/packages"
INSTALLED_PACKAGES_FILE = "/run/live-additional-software/packages/installed"
REMOVED_PACKAGES_FILE = "/run/live-additional-software/packages/removed"
ASP_STATE_INSTALLER_ASKED = "/run/live-additional-software/installer-asked"
ASP_LOG_FILE = "/run/live-additional-software/log"
OLD_APT_LISTS_DIR = os.path.join(PERSISTENCE_DIR, "apt", "lists.old")
APT_ARCHIVES_DIR = "/var/cache/apt/archives"
APT_LISTS_DIR = "/var/lib/apt/lists"
PACKAGES_LIST_FILE = "live-additional-software.conf"
CONFIG_LOCK_FILE = "/run/live-additional-software/config.lock"
PACKAGE_SPEC = re.compile(r"[a-z0-9][a-z0-9+.-]+(?::[a-z0-9][a-z0-9-]*)?"
                          r"(?:=[0-9][a-zA-Z0-9:.+~^-]*|/[a-zA-Z0-9][a-zA-Z0-9.+-]*)?")


gettext.textdomain("tails")
_ = gettext.gettext


class ASPError(Exception):
    """Base class for exceptions raised by"""


class ASPDataError(ASPError):
    """Raised when the data read does not have the expected format."""


def set_up_logging(log_to_journal=False):
    debug = os.getenv("DEBUG") or "debug" in Path("/proc/cmdline").read_text().split()
    log_level = logging.DEBUG if debug else logging.INFO
    log_format = "%(levelname)s:%(filename)s:%(lineno)d: %(message)s"
    stderr_handler = logging.StreamHandler()
    file_handler = logging.FileHandler(ASP_LOG_FILE)
    handlers = [stderr_handler, file_handler]
    if log_to_journal:
        handlers.append(systemd.journal.JournalHandler())
    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)


def has_additional_packages_list():
    """Return true iff a packages list file is found in a persistence.

    Log warnings in syslog.
    """
    try:
        packages_list_path = get_packages_list_path()
    except FileNotFoundError as e:
        logging.warning(e)
        return False
    if os.path.isfile(packages_list_path):
        logging.info("Found additional packages list.")
        return True
    else:
        logging.warning("Warning: no configuration file found.")
        return False


@contextmanager
def lock_config():
    """Serialize Veil commands with Tails' registration writers and restorer."""
    with open(CONFIG_LOCK_FILE, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write_config(packages):
    config_file_owner_uid = pwd.getpwnam(PERSISTENT_STORAGE_USERNAME).pw_uid
    config_file_owner_gid = grp.getgrnam(PERSISTENT_STORAGE_USERNAME).gr_gid
    path = Path(get_packages_list_path(return_nonexistent=True))

    fd, tmpfile = tempfile.mkstemp(dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as config:
            os.fchown(config.fileno(), uid=config_file_owner_uid, gid=config_file_owner_gid)
            os.fchmod(config.fileno(), 0o0644)
            config.writelines(f"{pkg}\n" for pkg in sorted(packages))
            config.flush()
            os.fsync(config.fileno())
        os.replace(tmpfile, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        Path(tmpfile).unlink(missing_ok=True)


def package_identity(pkg):
    """Keep the architecture qualifier and drop any version or release pin."""
    return re.split("[/=]", pkg, maxsplit=1)[0]


def filter_package_details(pkg):
    """Filter target release, version and architecture from pkg."""
    return re.split("[/:=]", pkg)[0]


def get_packages_list_path(return_nonexistent=False):
    """Return the package list file path in current or new persistence.

    The return_nonexistent arguments is passed to get_persistence_path.
    """
    persistence_dir = get_persistence_path(return_nonexistent)
    return os.path.join(persistence_dir, PACKAGES_LIST_FILE)


def get_additional_packages() -> set[str]:
    """Return the list of all additional packages configured."""
    packages = set()
    try:
        with open(get_packages_list_path()) as f:
            for number, line in enumerate(f, 1):
                pkg = line.strip()
                if pkg:
                    if not PACKAGE_SPEC.fullmatch(pkg):
                        raise ASPDataError(f"Invalid package selector on line {number}")
                    packages.add(pkg)
    except FileNotFoundError:
        # Just return an empty set.
        pass
    return packages


def add_additional_packages(new_packages: set[str]) -> None:
    """Add packages to additional packages configuration.

    Add the packages to additional packages configuration.

    The new_packages argument should be a set of packages names.
    """
    logging.info("Adding to additional packages list: %s", ",".join(new_packages))
    with lock_config():
        write_config(get_additional_packages() | new_packages)


def remove_additional_packages(old_packages: set[str]) -> None:
    """Remove packages from additional packages configuration.

    Removes the packages from additional packages configuration.

    The old_packages argument should be a list of packages names.
    """
    logging.info("Removing from additional packages list: %s", ",".join(old_packages))
    with lock_config():
        write_config(get_additional_packages() - old_packages)


def notify(
    title,
    body="",
    accept_label="",
    deny_label="",
    documentation_target="",
    urgent=False,
    return_id=False,
):
    """Display a notification to the user of the live system.

    The notification will show title and body.

    If accept_label or deny_label are set, they will be shown on action buttons
    and the method will wait for user input and return 1 if the button with
    accept_label was clicked or 0 if the button with deny_label was
    clicked.

    If documentation_target is set, a "Documentation" action button will open
    the corresponding Tails documentation when clicked.

    If return_id is true, returns the notification ID, which may be used to
    close the notification.

    Else, return None.
    """

    cmd = "/usr/local/lib/tails-additional-software-notify"
    if urgent:
        urgent = "urgent"
    else:
        urgent = ""

    try:
        unit_name = "tails-additional-software-notify-" + str(uuid.uuid4())
        success_exit_codes = (0, 3, 4, 5)
        completed_process = subprocess.run(  # noqa: PLW1510
            [  # noqa: S603
                "/usr/local/lib/run-with-user-env",
                "--transient-systemd-scope",
                unit_name,
                cmd,
                title,
                body,
                accept_label,
                deny_label,
                documentation_target,
                urgent,
            ],
            capture_output=True,
            text=True,
        )
        if completed_process.stderr:
            logging.warning("%s", completed_process.stderr)
        if completed_process.returncode not in success_exit_codes:
            raise OSError(
                f"command exited with {completed_process.returncode}. "
                f"stderr: {completed_process.stderr}"
            )
    except OSError as e:
        logging.warning("Warning: unable to notify the user. %s", e)
        logging.warning("The notification was: %s %s", title, body)
        return None

    if return_id:
        for line in completed_process.stdout.splitlines():
            if line.startswith("id="):
                return line[3:]
    else:
        if completed_process.returncode == 0:
            return 1
        elif completed_process.returncode == 3:
            return 0
        else:
            return None


def notify_failure(summary, details=None):
    """Display a failure notification to the user of the live system.

    The user has the option to edit the configuration or to view the system
    log.
    """
    if details:
        # Translators: Don't translate {details}, it's a placeholder and will
        # be replaced.
        details = _(
            "{details} Please check your list of additional "
            "software or read the system log to "
            "understand the problem."
        ).format(details=details)

    else:
        details = _(
            "Please check your list of additional "
            "software or read the system log to "
            "understand the problem."
        )

    action_clicked = notify(
        summary, details, _("Show Log"), _("Configure"), urgent=True
    )
    if action_clicked == 1:
        show_system_log()
    elif action_clicked == 0:
        show_configuration_window()


def show_system_log():
    """Show additional packages configuration window."""
    run_with_user_env(
        "/usr/local/bin/gtk-abspath-launch",
        "/usr/share/applications/org.gnome.TextEditor.desktop",
        ASP_LOG_FILE,
    )


def show_configuration_window():
    """Show additional packages configuration window."""
    run_with_user_env(
        "/usr/local/bin/gtk-abspath-launch",
        "/usr/share/applications/org.boum.tails.AdditionalSoftware.desktop",
    )


def setup_additional_packages():
    """Enable additional software in persistence."""
    subprocess.check_call(
        ["/usr/local/lib/tpscli", "ensure-is-active", "AdditionalSoftware"]
    )
