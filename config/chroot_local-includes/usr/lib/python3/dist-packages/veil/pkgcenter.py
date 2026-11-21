"""Veil Package Center for Debian packages and Nix tool bundles."""

from __future__ import annotations

import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import GLib, Gtk, Vte  # noqa: E402

from veil import apt_packages, common, pkg, tools_catalog, ui  # noqa: E402


class PkgWindow(Gtk.ApplicationWindow):
    APT_FILTERS = ("Search", "Installed", "Persistent")

    def __init__(self, app):
        super().__init__(application=app, title="Veil Package Center")
        ui.add_class(self, "veil")
        self.set_default_size(980, 700)
        self.set_icon_name("veil-packages")
        hb = Gtk.HeaderBar(show_close_button=True, title="Package Center",
                           subtitle="Debian packages and Nix tool bundles")
        self.set_titlebar(hb)
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        self.notice = ui.label("", "veil-muted", wrap=True)
        top.pack_start(self.notice, False, False, 0)
        sc = Gtk.ScrolledWindow()
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        sc.add(self.listbox)
        top.pack_start(sc, True, True, 0)
        tabs = Gtk.Notebook()
        self.add_debian_tab(tabs)
        tabs.append_page(top, Gtk.Label(label="Nix bundles"))
        paned.pack1(tabs, True, False)
        self.term = Vte.Terminal()
        self.term.set_size_request(-1, 200)
        self.term.connect("child-exited", self.child_exited)
        self.running = False
        self.persistence_active = False
        self.connect("notify::is-active", self.refresh_debian_on_focus)
        paned.pack2(self.term, False, True)
        self.add(paned)
        self.installed: set[str] = set()
        self.populate()
        self.refresh_debian()
        threading.Thread(target=self._load_installed, daemon=True).start()

    def add_debian_tab(self, tabs):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        toolbar = Gtk.Box(spacing=8)
        self.filter = Gtk.ComboBoxText()
        for name in self.APT_FILTERS:
            self.filter.append_text(name)
        self.filter.set_active(0)
        self.filter.connect("changed", self.refresh_debian)
        toolbar.pack_start(self.filter, False, False, 0)
        self.query = Gtk.SearchEntry(placeholder_text="Search Debian packages")
        self.query.connect("activate", self.refresh_debian)
        toolbar.pack_start(self.query, True, True, 0)
        refresh = Gtk.Button(label="Refresh list")
        refresh.connect("clicked", self.refresh_debian)
        toolbar.pack_start(refresh, False, False, 0)
        update = Gtk.Button(label="Update APT index")
        update.connect("clicked", lambda *_: self.spawn(["sudo", "--", "/usr/local/bin/apt", "update"]))
        toolbar.pack_start(update, False, False, 0)
        box.pack_start(toolbar, False, False, 0)

        notice = ui.label("APT uses the configured Debian and Tails repositories. Tools with direct network "
                          "requirements belong in an isolated environment. Package installation requires the "
                          "administration password set on the Welcome Screen.", "veil-muted", wrap=True)
        box.pack_start(notice, False, False, 0)
        self.debian_status = ui.label("", "veil-muted", wrap=True)
        box.pack_start(self.debian_status, False, False, 0)
        self.debian_rows = Gtk.ListStore(str, str, str)
        view = Gtk.TreeView(model=self.debian_rows)
        for index, title in enumerate(("Package", "Installed version", "Restored after reboot")):
            column = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=index)
            column.set_resizable(True)
            view.append_column(column)
        self.selection = view.get_selection()
        self.selection.connect("changed", self.select_debian)
        scroll = Gtk.ScrolledWindow()
        scroll.add(view)
        box.pack_start(scroll, True, True, 0)

        self.details = ui.label("", "veil-muted", wrap=True)
        box.pack_start(self.details, False, False, 0)
        self.keep = Gtk.CheckButton(label="Keep installed after reboot")
        box.pack_start(self.keep, False, False, 0)
        buttons = Gtk.Box(spacing=8)
        self.apt_buttons = []
        for label, action, persist in (("Install or upgrade", "install", False),
                                        ("Remove now", "remove", False),
                                        ("Remove now and stop restoring after reboot", "remove", True)):
            button = Gtk.Button(label=label, sensitive=False)
            button.connect("clicked", self.debian_action, action, persist)
            buttons.pack_start(button, False, False, 0)
            self.apt_buttons.append(button)
        box.pack_start(buttons, False, False, 0)
        tabs.append_page(box, Gtk.Label(label="Debian packages"))

    def refresh_debian_on_focus(self, *_):
        if self.is_active() and not self.running:
            self.refresh_debian()

    def refresh_debian(self, *_):
        mode = self.filter.get_active_text()
        query = self.query.get_text().strip().lower()
        self.request = object()
        threading.Thread(target=self.load_debian, args=(self.request, mode, query), daemon=True).start()

    def load_debian(self, request, mode, query):
        import apt
        import additional_software as asp

        try:
            issue = apt_packages.state()
            specs = set() if issue else apt_packages.registered()
            registered = {asp.package_identity(spec) for spec in specs}
            cache = apt.Cache()
            if mode == "Persistent":
                names = sorted(registered)
            elif mode == "Installed":
                names = sorted(p.name for p in cache if p.is_installed)
            else:
                names = sorted(p.name for p in cache if query and query in p.name)
            names = [name for name in names if query in name or mode == "Persistent"]
            rows = []
            for name in names[:200]:
                package = cache[name] if name in cache else None
                installed = package.installed if package else None
                persistent = any(apt_packages.matches(spec, name) for spec in registered)
                version = (package.candidate or installed) if package else None
                if version:
                    origins = ", ".join(sorted({o.origin or o.site for o in version.origins if o.origin or o.site}))
                    dependencies = ", ".join(" | ".join(d.name for d in group.or_dependencies)
                                             for group in version.dependencies)
                    details = (f"{version.summary}\nSource: {version.source_name} · {origins or 'local metadata'} · "
                               f"{version.installed_size / 1048576:.1f} MiB installed\n"
                               f"Dependencies: {dependencies or 'none'}")
                else:
                    details = "Package metadata unavailable. Refresh the APT index after connecting to Tor."
                rows.append((name, installed.version if installed else "", "Yes" if persistent else "", details))
            status = issue or "Additional Software is active."
            if not query and mode == "Search":
                status += " Enter a package name and press Enter to search."
            elif len(names) > 200:
                status += f" Showing 200 of {len(names)} matches. Refine your search."
        except (apt_packages.PackageError, OSError, SystemError) as exc:
            status, issue, rows = str(exc), str(exc), []
        GLib.idle_add(self.show_debian, request, status, issue, rows)

    def show_debian(self, request, status, issue, rows):
        if request is not self.request:
            return
        selected = self.selection.get_selected()
        current = selected[0][selected[1]][0] if selected[1] else None
        self.debian_status.set_text(status)
        self.persistence_active = not issue
        self.debian_rows.clear()
        self.package_details = {}
        for name, installed, persistent, details in rows:
            row = self.debian_rows.append((name, installed, persistent))
            self.package_details[name] = details
            if name == current:
                self.selection.select_iter(row)
        self.select_debian(self.selection)

    def select_debian(self, selection):
        model, row = selection.get_selected()
        name, installed, persistent = tuple(model[row]) if row else ("", "", "")
        active = bool(row) and not self.running
        self.details.set_text(self.package_details[name] if row else "")
        self.keep.set_sensitive(active and self.persistence_active)
        self.keep.set_active(bool(persistent))
        self.apt_buttons[0].set_sensitive(active)
        self.apt_buttons[1].set_sensitive(active and bool(installed))
        self.apt_buttons[2].set_sensitive(active and bool(persistent) and self.persistence_active)

    def debian_action(self, _button, action, persistent):
        model, row = self.selection.get_selected()
        if row is None:
            return
        name = model[row][0]
        requested = persistent or (action == "install" and self.keep.get_active())
        self.spawn(["/usr/local/bin/apt", action, name, *(["-Pr"] if requested else [])])

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

    def on_action(self, _button, name, action):
        self.spawn(["veil", action, f"bundle:{name}"])

    def spawn(self, argv):
        if self.running:
            return
        self.running = True
        self.select_debian(self.selection)
        self.term.reset(True, True)
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, None, argv, None,
                              GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None, self.spawned, None)
        self.term.grab_focus()

    def spawned(self, terminal, _pid, error, _data):
        if error:
            terminal.feed((str(error) + "\r\n").encode())
            self.child_exited(terminal, 1)

    def child_exited(self, _terminal, _status):
        self.running = False
        self.refresh_debian()
        threading.Thread(target=self._load_installed, daemon=True).start()


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
