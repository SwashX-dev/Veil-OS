"""Veil OS userland components.

Veil OS is an unofficial derivative of Tails (https://tails.net/). It is not
made, reviewed or endorsed by the Tails project.

Everything in this package runs as an unprivileged user unless explicitly
stated otherwise, and never weakens the Tails firewall or Tor enforcement.
"""

import os

__version__ = "0.1.0a1"

SHARE_DIR = os.environ.get("VEIL_SHARE_DIR", "/usr/share/veil")
LIB_DIR = os.environ.get("VEIL_LIB_DIR", "/usr/local/lib/veil")
