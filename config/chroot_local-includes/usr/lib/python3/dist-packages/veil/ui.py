"""Shared GTK3 styling for Veil applications."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

PALETTE = {
    "black": "#0b0b0f", "graphite": "#121217", "charcoal": "#1d1d25", "line": "#26262e",
    "text": "#e2e0ec", "muted": "#8a8798", "violet": "#7c6bb0", "violet_hi": "#8b7cf6",
    "cyan": "#4fd1c5", "amber": "#e0b45c", "red": "#f26d7d",
}

CSS = """
window.veil, .veil-bg { background-color: #0f0f13; color: #e2e0ec; }
.veil-sidebar { background-color: #0b0b0f; border-right: 1px solid #1d1d25; }
.veil-panel { background-color: #121217; border: 1px solid #1f1f27; border-radius: 6px; padding: 8px; }
.veil-title { font-weight: bold; font-size: 13px; color: #cfc9f2; letter-spacing: 1px; }
.veil-h { font-size: 10px; letter-spacing: 2px; color: #7c6bb0; font-weight: bold; }
.veil-muted { color: #8a8798; }
.veil-mono { font-family: "JetBrains Mono", monospace; font-size: 10px; }
.veil-pass { color: #4fd1c5; font-weight: bold; }
.veil-warn { color: #e0b45c; font-weight: bold; }
.veil-fail { color: #f26d7d; font-weight: bold; }
.veil-info { color: #8b7cf6; font-weight: bold; }
.veil-skip { color: #55535f; font-weight: bold; }
.veil-chat-user { background-color: #1b1830; border-radius: 8px; padding: 8px 10px; }
.veil-chat-agent { background-color: #121217; border: 1px solid #1f1f27; border-radius: 8px; padding: 8px 10px; }
.veil-tool { background-color: #0b0b0f; border-left: 2px solid #7c6bb0; padding: 4px 8px; }
.veil-tool-fail { border-left-color: #f26d7d; }
.veil-badge { border-radius: 10px; padding: 1px 8px; font-size: 9px; font-weight: bold; }
.veil-badge-off { background-color: #1d1d25; color: #8a8798; }
.veil-badge-on { background-color: #3a2f12; color: #e0b45c; }
.veil-badge-ok { background-color: #0f2a28; color: #4fd1c5; }
button.veil-accent { background-image: none; background-color: #3b3266; color: #fff; border: 1px solid #5d4f9c; }
levelbar block.filled { background-color: #7c6bb0; }
levelbar block.high { background-color: #e0b45c; }
"""


def apply_css() -> None:
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS.encode())
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-application-prefer-dark-theme", True)


def label(text: str, *classes: str, xalign: float = 0.0, wrap: bool = False, selectable: bool = False) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=xalign)
    lab.set_line_wrap(wrap)
    lab.set_selectable(selectable)
    for c in classes:
        lab.get_style_context().add_class(c)
    return lab


def add_class(widget: Gtk.Widget, *classes: str) -> Gtk.Widget:
    for c in classes:
        widget.get_style_context().add_class(c)
    return widget
