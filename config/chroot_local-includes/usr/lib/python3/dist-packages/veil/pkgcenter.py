"""Veil Package Center: graphical front end for optional Nix tool bundles.

Actions run `veil install/remove` inside an embedded terminal so that the
same license and confirmation prompts appear as on the command line.
"""

from __future__ import annotations

import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import GLib, Gtk, Vte  # noqa: E402

from veil import common, pkg, tools_catalog, ui  # noqa: E402


class PkgWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Veil Package Center")
        ui.add_class(self, "veil")
        self.set_default_size(980, 700)
        self.set_icon_name("veil-packages")
        hb = Gtk.HeaderBar(show_close_button=True, title="Package Center",
                           subtitle="Optional security tool bundles · Nix · through Tor")
        self.set_titlebar(hb)
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        self.notice = ui.label("", "veil-muted", wrap=True)
        top.pack_start(self.notice, False, False, 0)
        sc = Gtk.ScrolledWindow()
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        sc.add(self.listbox)
        top.pack_start(sc, True, True, 0)
        paned.pack1(top, True, False)
        self.term = Vte.Terminal()
        self.term.set_size_request(-1, 200)
        paned.pack2(self.term, False, True)
        self.add(paned)
        self.installed: set[str] = set()
        self.populate()
        threading.Thread(target=self._load_installed, daemon=True).start()

    def _load_installed(self):
        names = {r["name"] for r in pkg.installed()}
        GLib.idle_add(self._set_installed, names)

    def _set_installed(self, names):
        self.installed = names
        self.populate()
        return False

    def populate(self):
        warns = pkg.preflight(installing=True)
        self.notice.set_text("\n".join(warns) or
                             "Tor is connected. Packages are signed binaries from cache.nixos.org and run as you "
                             "(never as root), so they cannot change the firewall.")
        for c in self.listbox.get_children():
            self.listbox.remove(c)
        cat = {t["name"]: t for t in tools_catalog.load()}
        for name, b in sorted(pkg.bundles().items()):
            row = Gtk.Box(spacing=12, margin=8)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title = f"{name}" + ("   [proprietary / license required]" if b.get("unfree") else "")
            v.pack_start(ui.label(title, "veil-title"), False, False, 0)
            v.pack_start(ui.label(b.get("description", ""), "veil-muted", wrap=True), False, False, 0)
            pols = sorted({cat[p]["network_policy"] for p in b["packages"] if p in cat})
            v.pack_start(ui.label("packages: " + ", ".join(b["packages"]) +
                                  (f"\nnetwork policy: {', '.join(pols)}" if pols else ""),
                                  "veil-mono", wrap=True), False, False, 0)
            row.pack_start(v, True, True, 0)
            is_inst = any(n.startswith(f"bundle-{name}") for n in self.installed)
            btn = Gtk.Button(label="Remove" if is_inst else "Install")
            if not is_inst:
                ui.add_class(btn, "veil-accent")
            btn.connect("clicked", self.on_action, name, "remove" if is_inst else "install")
            row.pack_end(btn, False, False, 0)
            self.listbox.add(row)
        self.listbox.show_all()

    def on_action(self, _b, name, action):
        self.term.reset(True, True)
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, None, ["veil", action, f"bundle:{name}"], None,
                              GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None, self._spawned, None)
        self.term.connect("child-exited", lambda *_: threading.Thread(target=self._load_installed,
                                                                      daemon=True).start())
        self.term.grab_focus()

    def _spawned(self, *_a):
        pass


class PkgApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.veilos.PackageCenter")

    def do_activate(self):
        ui.apply_css()
        w = self.get_active_window() or PkgWindow(self)
        w.show_all()
        w.present()


def main() -> int:
    if not common.have("nix"):
        print("nix is not installed", file=sys.stderr)
    return PkgApp().run(sys.argv[:1])
