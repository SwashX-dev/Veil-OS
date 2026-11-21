"""Veil Agent desktop interface (GTK3)."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

try:
    gi.require_version("Vte", "2.91")
    from gi.repository import Vte  # noqa: E402
except (ValueError, ImportError):
    Vte = None

from veil import common, ui  # noqa: E402
from veil.agent import backend as be  # noqa: E402
from veil.agent import models, session  # noqa: E402
from veil.agent import policy as pol  # noqa: E402
from veil.agent.loop import UI, Agent, strip_think  # noqa: E402

CONFIRM_TEXT = {
    "full-control": "Let the agent write anywhere in your home folder? Network stays off unless you grant it separately.",
    "network": "Let the agent's tool commands use the network (through Tor) for this session?",
    "secrets": "Expose GnuPG, SSH keys, keyrings and browser data to the agent's tool commands?",
    "destructive-disk": "The agent wants to run a command that may ERASE a disk or partition.",
    "security-boundary": "The agent wants to change privileges, firewall, services or system policy.",
    "credentials": "The agent wants to read keys, passwords, wallets or browser secrets.",
    "mass-delete": "The agent wants to recursively delete a broad path.",
}


class AgentWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Veil Agent")
        ui.add_class(self, "veil")
        self.set_default_size(1180, 760)
        self.set_icon_name("veil-agent")
        d = pol.load_defaults()
        self.policy = pol.Policy(level=d["level"], workspace=str(Path.home()) if d["level"] == "workspace" else None)
        self.mode = d["mode"]
        self.backend = None
        self.agent: Agent | None = None
        self.busy = False

        hb = Gtk.HeaderBar(show_close_button=True, title="Veil Agent", subtitle="local · offline by default")
        self.set_titlebar(hb)
        self.status_lbl = ui.label("idle", "veil-muted")
        hb.pack_end(self.status_lbl)

        root = Gtk.Box()
        root.pack_start(self._sidebar(), False, False, 0)
        self.nb = Gtk.Notebook()
        self.nb.append_page(self._chat_page(), Gtk.Label(label="Chat"))
        self.nb.append_page(self._tools_page(), Gtk.Label(label="Tool log"))
        if Vte is not None:
            self.nb.append_page(self._terminal_page(), Gtk.Label(label="Terminal"))
        root.pack_start(self.nb, True, True, 0)
        self.add(root)
        self.connect("destroy", self.on_destroy)
        GLib.timeout_add_seconds(2, self.refresh_status)
        self.refresh_status()

    # ---------------------------------------------------------------- sidebar
    def _sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        box.set_size_request(260, -1)
        ui.add_class(box, "veil-sidebar")

        box.pack_start(ui.label("MODEL", "veil-h"), False, False, 2)
        self.model_combo = Gtk.ComboBoxText()
        self._fill_models()
        box.pack_start(self.model_combo, False, False, 0)
        row = Gtk.Box(spacing=4)
        load = Gtk.Button(label="Load")
        load.connect("clicked", lambda _b: self.ensure_backend(start=True))
        unload = Gtk.Button(label="Unload")
        unload.connect("clicked", self.on_unload)
        row.pack_start(load, True, True, 0)
        row.pack_start(unload, True, True, 0)
        box.pack_start(row, False, False, 0)
        self.model_hint = ui.label("", "veil-muted", wrap=True)
        box.pack_start(self.model_hint, False, False, 0)

        box.pack_start(ui.label("MODE", "veil-h"), False, False, 6)
        self.mode_combo = Gtk.ComboBoxText()
        for m in ("direct", "agent", "research", "terminal"):
            self.mode_combo.append(m, m.capitalize())
        self.mode_combo.set_active_id(self.mode)
        self.mode_combo.connect("changed", self.on_mode)
        box.pack_start(self.mode_combo, False, False, 0)

        box.pack_start(ui.label("PERMISSIONS", "veil-h"), False, False, 6)
        self.level_combo = Gtk.ComboBoxText()
        for lv in pol.LEVELS:
            self.level_combo.append(lv, lv)
        self.level_combo.set_active_id(self.policy.level)
        self.level_combo.connect("changed", self.on_level)
        box.pack_start(self.level_combo, False, False, 0)
        self.ws_btn = Gtk.FileChooserButton(title="Workspace", action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.ws_btn.set_filename(self.policy.workspace or str(Path.home()))
        self.ws_btn.connect("file-set", self.on_workspace)
        box.pack_start(ui.label("Workspace (writable in workspace mode)", "veil-muted"), False, False, 0)
        box.pack_start(self.ws_btn, False, False, 0)
        self.net_switch = Gtk.Switch(active=False)
        self.net_switch.connect("notify::active", self.on_net)
        self.sec_switch = Gtk.Switch(active=False)
        self.sec_switch.connect("notify::active", self.on_secrets)
        for lbl, sw in (("Network (via Tor)", self.net_switch), ("Credential stores", self.sec_switch)):
            r = Gtk.Box(spacing=6)
            r.pack_start(ui.label(lbl), True, True, 0)
            r.pack_end(sw, False, False, 0)
            box.pack_start(r, False, False, 0)
        box.pack_start(ui.label("Shell access never implies network access. "
                                "Grants last for this window only.", "veil-muted", wrap=True), False, False, 0)

        box.pack_start(ui.label("STATUS", "veil-h"), False, False, 6)
        self.status_box = ui.label("", "veil-mono", wrap=True)
        self.status_box.set_use_markup(True)
        box.pack_start(self.status_box, False, False, 0)
        self.ram = Gtk.LevelBar(min_value=0, max_value=1)
        box.pack_start(self.ram, False, False, 0)

        box.pack_start(ui.label("SESSION", "veil-h"), False, False, 6)
        r = Gtk.Box(spacing=4)
        for lbl, cb in (("New", self.on_new), ("Save", self.on_save)):
            b = Gtk.Button(label=lbl)
            b.connect("clicked", cb)
            r.pack_start(b, True, True, 0)
        box.pack_start(r, False, False, 0)
        return box

    def _fill_models(self):
        self.model_combo.remove_all()
        local = models.local_models()
        for m in local:
            self.model_combo.append(m["file"], f"{m['name']}  ({common.human_bytes(m['size'])})")
        if local:
            rec = models.recommend()
            pick = next((m for m in local if m["name"] == rec.name), local[0])
            self.model_combo.set_active_id(pick["file"])

    # ---------------------------------------------------------------- pages
    def _chat_page(self):
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.chat_sc = Gtk.ScrolledWindow()
        self.chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=14)
        self.chat_sc.add(self.chat)
        v.pack_start(self.chat_sc, True, True, 0)
        self.progress = Gtk.ProgressBar(show_text=True, text="")
        self.progress.set_no_show_all(True)
        v.pack_start(self.progress, False, False, 0)
        row = Gtk.Box(spacing=6, margin=10)
        self.entry = Gtk.Entry(placeholder_text="Tell Veil Agent what to do…  (Enter to send)")
        self.entry.connect("activate", self.on_send)
        send = ui.add_class(Gtk.Button(label="Send"), "veil-accent")
        send.connect("clicked", self.on_send)
        row.pack_start(self.entry, True, True, 0)
        row.pack_start(send, False, False, 0)
        v.pack_start(row, False, False, 0)
        if not models.local_models():
            self.add_bubble("agent", "No model is installed, and Veil never downloads one automatically.\n\n"
                                     "In a terminal, run:\n  veil agent models list\n  veil agent models pull qwen3-4b\n\n"
                                     "The download asks for your confirmation, goes through Tor, and is "
                                     "verified by SHA-256. Unlock Persistent Storage first to keep it.")
        return v

    def _tools_page(self):
        sc = Gtk.ScrolledWindow()
        self.tool_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        sc.add(self.tool_list)
        return sc

    def _terminal_page(self):
        t = Vte.Terminal()
        t.spawn_async(Vte.PtyFlags.DEFAULT, self.policy.workspace or str(Path.home()),
                      [os.environ.get("SHELL", "/bin/bash")], None, GLib.SpawnFlags.DEFAULT,
                      None, None, -1, None, None, None)
        sc = Gtk.ScrolledWindow()
        sc.add(t)
        return sc

    # ---------------------------------------------------------------- chat helpers
    def add_bubble(self, who: str, text: str):
        lab = ui.label(text, wrap=True, selectable=True)
        lab.set_max_width_chars(110)
        frame = Gtk.Box()
        ui.add_class(frame, "veil-chat-user" if who == "user" else "veil-chat-agent")
        frame.pack_start(lab, True, True, 0)
        outer = Gtk.Box()
        if who == "user":
            outer.pack_end(frame, False, False, 0)
        else:
            outer.pack_start(frame, True, True, 0)
        self.chat.pack_start(outer, False, False, 0)
        self.chat.show_all()
        GLib.idle_add(self._scroll_bottom)
        return lab

    def _scroll_bottom(self):
        adj = self.chat_sc.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def add_tool(self, name, args, result):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        ui.add_class(box, "veil-tool", *(() if result.ok else ("veil-tool-fail",)))
        box.pack_start(ui.label(f"{'✓' if result.ok else '✗'} {name}  {json.dumps(args, ensure_ascii=False)[:300]}",
                                "veil-mono", wrap=True, selectable=True), False, False, 0)
        ex = Gtk.Expander(label="output")
        ex.add(ui.label(result.output[:8000], "veil-mono", wrap=True, selectable=True))
        box.pack_start(ex, False, False, 0)
        if result.data.get("diff"):
            dx = Gtk.Expander(label="diff", expanded=True)
            dx.add(ui.label(result.data["diff"][:8000], "veil-mono", wrap=True, selectable=True))
            box.pack_start(dx, False, False, 0)
        self.tool_list.pack_start(box, False, False, 0)
        self.tool_list.show_all()
        # A compact trace in the chat too.
        self.chat.pack_start(ui.label(f"   ⚙ {name} → {'ok' if result.ok else 'failed'}",
                                      "veil-pass" if result.ok else "veil-fail"), False, False, 0)
        self.chat.show_all()

    # ---------------------------------------------------------------- permissions
    def ask(self, kind: str, summary: str) -> bool:
        """Modal confirmation on the GTK thread. Only the human can answer."""
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                              buttons=Gtk.ButtonsType.NONE, text=CONFIRM_TEXT.get(kind, f"Allow {kind}?"))
        d.format_secondary_text(summary)
        d.add_button("Deny", Gtk.ResponseType.REJECT)
        allow = d.add_button("Allow", Gtk.ResponseType.ACCEPT)
        ui.add_class(allow, "destructive-action")
        d.set_default_response(Gtk.ResponseType.REJECT)
        r = d.run()
        d.destroy()
        return r == Gtk.ResponseType.ACCEPT

    def ask_from_worker(self, kind: str, summary: str) -> bool:
        ev, box = threading.Event(), {}

        def run():
            box["ok"] = self.ask(kind, summary)
            ev.set()
            return False
        GLib.idle_add(run)
        ev.wait()
        return box.get("ok", False)

    def on_level(self, combo):
        lv = combo.get_active_id()
        if lv == self.policy.level:
            return
        if lv == "full-control" and not self.ask("full-control", "Applies to this window only."):
            combo.set_active_id(self.policy.level)
            return
        self.policy.level = lv
        if lv == "workspace" and not self.policy.workspace:
            self.policy.workspace = self.ws_btn.get_filename() or str(Path.home())

    def on_workspace(self, btn):
        self.policy.workspace = btn.get_filename()

    def on_mode(self, combo):
        self.mode = combo.get_active_id()
        if self.agent:
            self.agent.mode = self.mode

    def on_net(self, sw, _p):
        on = sw.get_active()
        if on and not self.ask("network", "Tool commands will reach the network through Tor until you switch this off."):
            sw.set_active(False)
            return
        self.policy.network = on
        if self.agent:
            self.agent.set_network(on)

    def on_secrets(self, sw, _p):
        on = sw.get_active()
        if on and not self.ask("secrets", "Applies to this window only."):
            sw.set_active(False)
            return
        self.policy.secrets = on

    # ---------------------------------------------------------------- backend
    def ensure_backend(self, start=False) -> bool:
        path = self.model_combo.get_active_id()
        if not path:
            self.add_bubble("agent", "Select or install a model first (veil agent models pull NAME).")
            return False
        if self.backend is None or str(getattr(self.backend, "model_path", "")) != path:
            if self.backend:
                self.backend.stop()
            self.backend = be.LlamaCppBackend(Path(path), context=pol.load_defaults()["context"])
            self.agent = None
        if self.agent is None:
            def tool_start(n, a):
                self._last_args = a
                GLib.idle_add(self.set_status, f"running {n}…")

            gui_ui = UI(on_token=None, confirm=self.ask_from_worker,
                        on_tool_start=tool_start,
                        on_tool_end=lambda n, r: GLib.idle_add(self._tool_done, n, r),
                        on_status=lambda s: GLib.idle_add(self.set_status, s))
            self.agent = Agent(backend=self.backend, policy=self.policy, mode=self.mode,
                               session=session.Session(mode=self.mode), ui=gui_ui)
        if start:
            self.set_status("loading model…")
            threading.Thread(target=self._start_backend, daemon=True).start()
        return True

    def _start_backend(self):
        try:
            self.backend.start()
            GLib.idle_add(self.set_status, "model loaded")
        except be.BackendError as e:
            GLib.idle_add(self.add_bubble, "agent", f"[backend] {e}")
            GLib.idle_add(self.set_status, "error")

    def _tool_done(self, name, result):
        self.add_tool(name, getattr(self, "_last_args", {}), result)
        return False

    def on_unload(self, _b):
        if self.backend:
            self.backend.stop()
        self.set_status("model unloaded")

    def set_status(self, s):
        self.status_lbl.set_text(s)
        if self.busy:
            self.progress.show()
            self.progress.set_text(s)
            self.progress.pulse()
        return False

    # ---------------------------------------------------------------- run
    def on_send(self, _w):
        text = self.entry.get_text().strip()
        if not text or self.busy:
            return
        if not self.ensure_backend():
            return
        self.entry.set_text("")
        self.add_bubble("user", text)
        self.busy = True
        self.progress.show()

        def work():
            try:
                out = self.agent.run(text)
            except be.BackendError as e:
                out = f"[backend] {e}"
            GLib.idle_add(self._finish, out)
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, out):
        self.busy = False
        self.progress.hide()
        self.add_bubble("agent", strip_think(out) or "(no answer)")
        self.set_status("idle")
        return False

    def on_new(self, _b):
        if self.agent:
            self.agent.session.discard()
            self.agent.session = session.Session(mode=self.mode)
        for c in self.chat.get_children():
            self.chat.remove(c)

    def on_save(self, _b):
        if not self.agent:
            return
        try:
            dest = self.agent.session.save()
            self.add_bubble("agent", f"Session saved to {dest} (Persistent Storage, encrypted).")
        except session.PersistenceLocked as e:
            self.add_bubble("agent", str(e))

    def refresh_status(self):
        hw_mem = common.mem_info()
        total, avail = hw_mem.get("MemTotal", 1), hw_mem.get("MemAvailable", 0)
        self.ram.set_value(1 - avail / total)
        tor = common.tor_bootstrapped()
        net = self.policy.network
        self.status_box.set_markup(
            f"Tor: {'<span foreground=\"#4fd1c5\">connected</span>' if tor else 'not connected'}\n"
            f"Agent network: {'<span foreground=\"#e0b45c\">GRANTED</span>' if net else '<span foreground=\"#4fd1c5\">denied</span>'}\n"
            f"Persistence: {'unlocked' if common.persistence_unlocked() else 'amnesic'}\n"
            f"Model server: {'running' if self.backend and self.backend.running else 'stopped'}\n"
            f"RAM free: {common.human_bytes(avail)}")
        self.model_hint.set_text("" if self.model_combo.get_active_id()
                                 else "No local model. Run: veil agent models pull qwen3-4b")
        return True

    def on_destroy(self, _w):
        if self.agent:
            self.agent.close()
            self.agent.session.discard()
        if self.backend:
            self.backend.stop()


class AgentApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.veilos.Agent")

    def do_activate(self):
        ui.apply_css()
        win = self.get_active_window() or AgentWindow(self)
        win.show_all()
        win.present()


def main() -> int:
    return AgentApp().run(sys.argv[:1])
