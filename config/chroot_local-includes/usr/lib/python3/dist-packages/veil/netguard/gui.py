"""Veil Network Guard — graphical privacy monitor."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from veil import common, ui  # noqa: E402
from veil.netguard import checks, report  # noqa: E402

ICON = {"pass": "●", "warn": "▲", "fail": "✖", "info": "◆", "skip": "○"}


class GuardWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Network Guard")
        ui.add_class(self, "veil")
        self.set_default_size(820, 640)
        self.set_icon_name("veil-network-guard")
        self.last: list[checks.Check] = []

        hb = Gtk.HeaderBar(show_close_button=True, title="Network Guard", subtitle="Veil OS privacy monitor")
        self.set_titlebar(hb)
        self.run_btn = ui.add_class(Gtk.Button(label="Run audit"), "veil-accent")
        self.run_btn.connect("clicked", lambda _b: self.refresh(active=True))
        hb.pack_end(self.run_btn)
        exp = Gtk.Button(label="Export report…")
        exp.connect("clicked", self.on_export)
        hb.pack_end(exp)
        self.auto = Gtk.CheckButton(label="Auto-refresh")
        self.auto.set_tooltip_text("Re-run passive checks every 30 s (no active probes)")
        hb.pack_start(self.auto)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.summary = ui.label("Press “Run audit”.", "veil-title", xalign=0.0)
        self.summary.set_margin_start(16)
        self.summary.set_margin_top(12)
        self.summary.set_margin_bottom(4)
        self.sub = ui.label("Network Guard only inspects; enforcement stays with the unchanged Tails firewall.",
                            "veil-muted", wrap=True)
        self.sub.set_margin_start(16)
        self.sub.set_margin_bottom(10)
        outer.pack_start(self.summary, False, False, 0)
        outer.pack_start(self.sub, False, False, 0)

        sc = Gtk.ScrolledWindow()
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        ui.add_class(self.list, "veil-bg")
        sc.add(self.list)
        outer.pack_start(sc, True, True, 0)
        self.add(outer)
        GLib.timeout_add_seconds(30, self._auto_tick)
        GLib.idle_add(lambda: self.refresh(active=False) and False)

    def _auto_tick(self):
        if self.auto.get_active():
            self.refresh(active=False)
        return True

    def refresh(self, active: bool) -> None:
        self.run_btn.set_sensitive(False)
        self.summary.set_text("Auditing…")

        def work():
            res = checks.run_all(active_probes=active)
            GLib.idle_add(self.show_results, res)
        threading.Thread(target=work, daemon=True).start()

    def show_results(self, res):
        self.last = res
        for c in self.list.get_children():
            self.list.remove(c)
        s = report.summarize(res)
        n = s["counts"]
        verdict = {"pass": "No privacy problems detected", "warn": "Review the warnings below",
                   "fail": "Privacy problem detected"}[s["verdict"]]
        self.summary.set_markup(f"<span foreground='{ {'pass': '#4fd1c5', 'warn': '#e0b45c', 'fail': '#f26d7d'}[s['verdict']] }'>"
                                f"{ICON[s['verdict']]}</span>  {GLib.markup_escape_text(verdict)}")
        self.sub.set_text(f"{n['pass']} passed · {n['warn']} warnings · {n['fail']} failed · {n['info']} info · "
                          f"{n['skip']} skipped — {time.strftime('%H:%M:%S UTC', time.gmtime())}")
        cat = None
        for c in sorted(res, key=lambda c: (c.category, report.ORDER.get(c.status, 9))):
            if c.category != cat:
                cat = c.category
                h = ui.label(cat.upper(), "veil-h")
                h.set_margin_start(16)
                h.set_margin_top(10)
                row = Gtk.ListBoxRow(activatable=False, selectable=False)
                row.add(h)
                self.list.add(row)
            self.list.add(self._row(c))
        self.list.show_all()
        self.run_btn.set_sensitive(True)
        return False

    def _row(self, c: checks.Check) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(activatable=False, selectable=False)
        box = Gtk.Box(spacing=10, margin_start=16, margin_end=16, margin_top=4, margin_bottom=4)
        icon = ui.label(ICON.get(c.status, "?"), f"veil-{c.status}")
        icon.set_size_request(18, -1)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        v.pack_start(ui.label(c.title, xalign=0.0), False, False, 0)
        v.pack_start(ui.label(c.detail, "veil-muted", wrap=True, selectable=True), False, False, 0)
        if c.data and c.status in ("warn", "fail"):
            ex = Gtk.Expander(label="Details")
            ex.add(ui.label("\n".join(f"{k}: {val}" for k, val in c.data.items()), "veil-mono",
                            wrap=True, selectable=True))
            v.pack_start(ex, False, False, 0)
        box.pack_start(icon, False, False, 0)
        box.pack_start(v, True, True, 0)
        box.pack_end(ui.label(c.status.upper(), f"veil-{c.status}"), False, False, 0)
        row.add(box)
        return row

    def on_export(self, _b):
        if not self.last:
            return
        base = common.PERSISTENT_DIR if common.persistence_unlocked() else Path.home()
        dest = base / f"veil-privacy-report-{time.strftime('%Y%m%d-%H%M%S')}.json"
        dest.write_text(report.to_json("Veil Network Guard", self.last))
        d = Gtk.MessageDialog(transient_for=self, message_type=Gtk.MessageType.INFO,
                              buttons=Gtk.ButtonsType.OK, text="Report saved")
        d.format_secondary_text(f"{dest}\n" + ("(Persistent Storage)" if base != Path.home()
                                               else "(RAM only: it will be erased at shutdown)"))
        d.run()
        d.destroy()


class GuardApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.veilos.NetworkGuard")

    def do_activate(self):
        ui.apply_css()
        win = self.get_active_window() or GuardWindow(self)
        win.show_all()
        win.present()


def main() -> int:
    return GuardApp().run(sys.argv)
