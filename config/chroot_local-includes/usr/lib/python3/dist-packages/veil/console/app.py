"""Veil Console application window."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GLib, Gtk, Pango, Vte  # noqa: E402

from veil import common, ui  # noqa: E402
from veil.console import stats  # noqa: E402

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "veil" / "console.json"
DEFAULTS = {"panels": {"left": True, "right": True, "files": True}, "effects": False,
            "font": "JetBrains Mono 11", "reveal_addresses": False, "scrollback": 10000}

PALETTE = ["#0b0b0f", "#f26d7d", "#4fd1c5", "#e0b45c", "#7c6bb0", "#b18cf5", "#5fb3c9", "#cfcbe6",
           "#3a3846", "#ff8a96", "#6fe6da", "#f0c97a", "#9d8df5", "#c9a8ff", "#86d0e2", "#ffffff"]

CSS = """
.vc-root { background-color: #08080b; }
.vc-side { background-color: #0b0b0f; border: 1px solid #1a1a22; border-radius: 4px; padding: 10px; }
.vc-head { font-family: "JetBrains Mono"; font-size: 9px; letter-spacing: 3px; color: #7c6bb0; font-weight: bold; }
.vc-val { font-family: "JetBrains Mono"; font-size: 10px; color: #cfcbe6; }
.vc-dim { font-family: "JetBrains Mono"; font-size: 9px; color: #5c5a6a; }
.vc-big { font-family: "JetBrains Mono"; font-size: 22px; color: #e2e0ec; }
.vc-term-frame { border: 1px solid #1d1d25; border-radius: 4px; }
.vc-term-frame.vc-glow { border-color: #7c6bb0; }
.vc-files { background-color: #0b0b0f; border: 1px solid #1a1a22; border-radius: 4px; padding: 6px; }
.vc-file { font-family: "JetBrains Mono"; font-size: 9px; color: #a9a6b8; padding: 2px 6px; }
.vc-dir { color: #8b7cf6; }
notebook.vc-tabs > header { background-color: #08080b; border: none; }
notebook.vc-tabs > header tab { font-family: "JetBrains Mono"; font-size: 9px; padding: 2px 10px; color: #6c6a78; }
notebook.vc-tabs > header tab:checked { color: #e2e0ec; box-shadow: inset 0 -2px #4fd1c5; }
"""


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        user = json.loads(CONFIG.read_text())
        cfg.update({k: v for k, v in user.items() if k in DEFAULTS and k != "panels"})
        cfg["panels"].update(user.get("panels", {}))
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=1))
    except OSError:
        pass


class Spark(Gtk.DrawingArea):
    """Minimal sparkline graph."""

    def __init__(self, hist: stats.History, color=(0.49, 0.42, 0.69), maxval=100.0, height=38):
        super().__init__()
        self.hist, self.color, self.maxval = hist, color, maxval
        self.set_size_request(-1, height)
        self.connect("draw", self.on_draw)

    def on_draw(self, _w, cr):
        a = self.get_allocation()
        w, h = a.width, a.height
        cr.set_source_rgb(0.043, 0.043, 0.059)
        cr.paint()
        cr.set_source_rgba(1, 1, 1, 0.04)
        for i in range(1, 4):
            cr.move_to(0, h * i / 4)
            cr.line_to(w, h * i / 4)
        cr.stroke()
        vals = list(self.hist.v)
        mx = self.maxval or max(max(vals), 1.0)
        n = len(vals)
        cr.move_to(0, h)
        for i, v in enumerate(vals):
            cr.line_to(w * i / max(n - 1, 1), h - (h - 2) * min(v / mx, 1.0))
        cr.line_to(w, h)
        cr.close_path()
        cr.set_source_rgba(*self.color, 0.18)
        cr.fill_preserve()
        cr.set_source_rgba(*self.color, 0.9)
        cr.set_line_width(1.2)
        cr.new_path()
        for i, v in enumerate(vals):
            fn = cr.move_to if i == 0 else cr.line_to
            fn(w * i / max(n - 1, 1), h - (h - 2) * min(v / mx, 1.0))
        cr.stroke()
        return False


class ConsoleWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Veil Console")
        self.cfg = load_config()
        self.set_default_size(1440, 860)
        self.set_icon_name("veil-console")
        ui.add_class(self, "vc-root")
        self.cpu, self.net = stats.CpuSampler(), stats.NetSampler()
        self.cpu_h, self.rx_h, self.tx_h = stats.History(), stats.History(), stats.History()
        self.fullscreen_on = False
        self._glow_until = 0.0

        grid = Gtk.Box(spacing=8, margin=8)
        self.left = self._left_panel()
        self.right = self._right_panel()

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.tabs = Gtk.Notebook(scrollable=True)
        ui.add_class(self.tabs, "vc-tabs")
        self.tabs.connect("switch-page", lambda *_: GLib.idle_add(self.refresh_files))
        self.frame = Gtk.Frame()
        ui.add_class(self.frame, "vc-term-frame")
        self.frame.add(self.tabs)
        center.pack_start(self.frame, True, True, 0)
        self.files = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12,
                                 min_children_per_line=4, row_spacing=2, column_spacing=2)
        fsc = Gtk.ScrolledWindow()
        fsc.set_size_request(-1, 110)
        fsc.add(self.files)
        self.files_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ui.add_class(self.files_box, "vc-files")
        self.cwd_label = ui.label("FILESYSTEM", "vc-head")
        self.files_box.pack_start(self.cwd_label, False, False, 0)
        self.files_box.pack_start(fsc, True, True, 0)
        center.pack_start(self.files_box, False, False, 0)

        grid.pack_start(self.left, False, False, 0)
        grid.pack_start(center, True, True, 0)
        grid.pack_start(self.right, False, False, 0)
        self.add(grid)
        self.connect("key-press-event", self.on_key)
        self.new_tab()
        GLib.timeout_add(1000, self.tick)
        GLib.timeout_add(3000, self.slow_tick)

    # ---------------------------------------------------------------- panels
    def _section(self, box, title):
        box.pack_start(ui.label(title, "vc-head"), False, False, 6)

    def _left_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_size_request(250, -1)
        ui.add_class(box, "vc-side")
        self._section(box, "SYSTEM")
        self.clock = ui.label("", "vc-big")
        self.date = ui.label("", "vc-dim")
        self.sysinfo = ui.label("", "vc-val")
        for w in (self.clock, self.date, self.sysinfo):
            box.pack_start(w, False, False, 0)
        self._section(box, "CPU")
        self.cpu_lbl = ui.label("", "vc-val")
        box.pack_start(self.cpu_lbl, False, False, 0)
        box.pack_start(Spark(self.cpu_h), False, False, 0)
        self._section(box, "MEMORY  (RAM IS ALSO YOUR DISK)")
        self.mem_lbl = ui.label("", "vc-val")
        self.mem_bar = Gtk.LevelBar(min_value=0, max_value=1)
        box.pack_start(self.mem_lbl, False, False, 0)
        box.pack_start(self.mem_bar, False, False, 0)
        self._section(box, "PROCESSES")
        self.procs = ui.label("", "vc-val")
        box.pack_start(self.procs, False, False, 0)
        return box

    def _right_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_size_request(250, -1)
        ui.add_class(box, "vc-side")
        self._section(box, "PRIVACY")
        self.privacy = ui.label("", "vc-val")
        self.privacy.set_use_markup(True)
        box.pack_start(self.privacy, False, False, 0)
        self._section(box, "NETWORK")
        self.rates = ui.label("", "vc-val")
        box.pack_start(self.rates, False, False, 0)
        box.pack_start(Spark(self.rx_h, color=(0.31, 0.82, 0.77), maxval=0), False, False, 0)
        box.pack_start(Spark(self.tx_h, color=(0.55, 0.49, 0.96), maxval=0), False, False, 0)
        self.ifaces = ui.label("", "vc-val")
        box.pack_start(self.ifaces, False, False, 0)
        self.reveal = Gtk.CheckButton(label="reveal hardware addresses")
        self.reveal.set_active(self.cfg["reveal_addresses"])
        box.pack_start(self.reveal, False, False, 0)
        self._section(box, "KEYS")
        box.pack_start(ui.label("Ctrl+Shift+T  new tab\nCtrl+Shift+W  close tab\nCtrl+Shift+A  Veil Agent\n"
                                "Ctrl+Shift+X  explain selection\nCtrl+Shift+P  toggle panels\n"
                                "Ctrl+Shift+E  toggle effects\nF11           fullscreen\nCtrl+±        zoom",
                                "vc-dim"), False, False, 0)
        return box

    # ---------------------------------------------------------------- terminals
    def _make_terminal(self) -> Vte.Terminal:
        t = Vte.Terminal()
        t.set_font(Pango.FontDescription(self.cfg["font"]))
        fg, bg = Gdk.RGBA(), Gdk.RGBA()
        fg.parse("#d8d6e4")
        bg.parse("#0b0b0f")
        pal = []
        for c in PALETTE:
            r = Gdk.RGBA()
            r.parse(c)
            pal.append(r)
        t.set_colors(fg, bg, pal)
        cur = Gdk.RGBA()
        cur.parse("#4fd1c5")
        t.set_color_cursor(cur)
        t.set_cursor_blink_mode(Vte.CursorBlinkMode.OFF)
        t.set_scrollback_lines(int(self.cfg["scrollback"]))
        t.set_mouse_autohide(True)
        t.connect("contents-changed", self.on_output)
        t.connect("current-directory-uri-changed", lambda *_: self.refresh_files())
        return t

    def new_tab(self, argv: list[str] | None = None, title: str = "shell") -> Vte.Terminal:
        t = self._make_terminal()
        shell = os.environ.get("SHELL", "/bin/bash")
        env = [f"{k}={v}" for k, v in os.environ.items() if k not in ("PROMPT_COMMAND",)]
        # Report the working directory (OSC 7) so the file panel can follow it.
        env.append('PROMPT_COMMAND=printf "\\033]7;file://%s%s\\033\\\\" "$HOSTNAME" "$PWD"')
        env.append("VEIL_CONSOLE=1")
        cwd = self.current_dir() or str(Path.home())
        t.spawn_async(Vte.PtyFlags.DEFAULT, cwd, argv or [shell], env, GLib.SpawnFlags.DEFAULT,
                      None, None, -1, None, None, None)
        t.connect("child-exited", lambda term, _st: self.close_tab(term))
        sc = Gtk.ScrolledWindow()
        sc.add(t)
        idx = self.tabs.append_page(sc, Gtk.Label(label=f" {title} "))
        self.tabs.set_tab_reorderable(sc, True)
        self.tabs.show_all()
        self.tabs.set_current_page(idx)
        t.grab_focus()
        return t

    def close_tab(self, term):
        for i in range(self.tabs.get_n_pages()):
            page = self.tabs.get_nth_page(i)
            if page.get_child() is term:
                self.tabs.remove_page(i)
                break
        if self.tabs.get_n_pages() == 0:
            self.get_application().quit()

    def current_term(self) -> Vte.Terminal | None:
        page = self.tabs.get_nth_page(self.tabs.get_current_page()) if self.tabs.get_n_pages() else None
        return page.get_child() if page else None

    def current_dir(self) -> str | None:
        t = self.current_term() if hasattr(self, "tabs") else None
        uri = t.get_current_directory_uri() if t else None
        if uri:
            return urllib.parse.unquote(urllib.parse.urlparse(uri).path)
        return None

    # ---------------------------------------------------------------- updates
    def on_output(self, _t):
        if self.cfg["effects"]:
            self._glow_until = time.monotonic() + 0.35
            self.frame.get_style_context().add_class("vc-glow")

    def tick(self):
        now = time.gmtime()
        self.clock.set_text(time.strftime("%H:%M:%S", now))
        self.date.set_text(time.strftime("%a %d %b %Y  UTC", now))
        c = self.cpu.sample()
        self.cpu_h.push(c)
        self.cpu_lbl.set_text(f"{c:5.1f}%   load {stats.load()}")
        used, total = stats.memory()
        self.mem_lbl.set_text(f"{common.human_bytes(used)} / {common.human_bytes(total)}")
        self.mem_bar.set_value(used / total if total else 0)
        rx, tx = self.net.sample()
        self.rx_h.push(rx)
        self.tx_h.push(tx)
        self.rates.set_text(f"↓ {stats.human_rate(rx)}\n↑ {stats.human_rate(tx)}")
        if self._glow_until and time.monotonic() > self._glow_until:
            self.frame.get_style_context().remove_class("vc-glow")
            self._glow_until = 0.0
        for w in self.get_children()[0].get_children():
            w.queue_draw()
        self.left.queue_draw()
        self.right.queue_draw()
        return True

    def slow_tick(self):
        self.sysinfo.set_text(f"up {stats.uptime()}\n{os.uname().release}")
        self.procs.set_text("\n".join(f"{n[:16]:<16} {c:5.1f}%" for n, c, _m in stats.top_processes(8)))
        tor = common.tor_bootstrapped()
        persist = common.persistence_unlocked()
        grants = common.amnesic_state_dir("agent") / "network-grants"
        g = len(list(grants.iterdir())) if grants.is_dir() else 0
        self.privacy.set_markup(
            ("<span foreground='#4fd1c5'>◆ TOR CONNECTED</span>" if tor else
             "<span foreground='#e0b45c'>◆ TOR NOT READY</span>") + "\n" +
            ("<span foreground='#8b7cf6'>◼ PERSISTENCE ON</span>" if persist else
             "<span foreground='#8a8798'>◻ AMNESIC SESSION</span>") + "\n" +
            ("<span foreground='#e0b45c'>⇅ AGENT HAS NETWORK</span>" if g else
             "<span foreground='#8a8798'>· agent offline</span>"))
        rows = []
        for i in stats.interfaces():
            mac = i["mac"] if self.reveal.get_active() else "••:••:••:••:••:••"
            rows.append(f"{i['name'][:10]:<10} {i['kind']:<7} {i['state']}\n  {mac}")
        self.ifaces.set_text("\n".join(rows) or "no interfaces")
        return True

    def refresh_files(self):
        path = self.current_dir() or str(Path.home())
        self.cwd_label.set_text(f"FILESYSTEM  {path}")
        for c in self.files.get_children():
            self.files.remove(c)
        for name, is_dir in stats.cwd_entries(path):
            lab = ui.label(("▸ " if is_dir else "  ") + name[:24], "vc-file", *(("vc-dir",) if is_dir else ()))
            self.files.add(lab)
        self.files.show_all()
        return False

    # ---------------------------------------------------------------- keys
    def apply_panels(self):
        p = self.cfg["panels"]
        self.left.set_visible(p["left"])
        self.right.set_visible(p["right"])
        self.files_box.set_visible(p["files"])

    def on_key(self, _w, ev):
        ctrl_shift = (ev.state & Gdk.ModifierType.CONTROL_MASK) and (ev.state & Gdk.ModifierType.SHIFT_MASK)
        k = Gdk.keyval_name(ev.keyval) or ""
        t = self.current_term()
        if ev.keyval == Gdk.KEY_F11:
            (self.unfullscreen if self.fullscreen_on else self.fullscreen)()
            self.fullscreen_on = not self.fullscreen_on
            return True
        if ctrl_shift:
            k = k.upper()
            if k == "T":
                self.new_tab()
            elif k == "W" and t:
                self.close_tab(t)
            elif k == "A":
                self.new_tab(["veil", "agent", "--mode", "terminal", "--level", "workspace",
                              "--workspace", self.current_dir() or str(Path.home())], title="agent")
            elif k == "X" and t and t.get_has_selection():
                t.copy_clipboard_format(Vte.Format.TEXT)
                cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                text = (cb.wait_for_text() or "")[:4000]
                if text:
                    self.new_tab(["veil", "agent", "--mode", "research",
                                  f"Explain this terminal output and suggest next steps:\n{text}"], title="explain")
            elif k == "P":
                on = not (self.cfg["panels"]["left"] or self.cfg["panels"]["right"])
                self.cfg["panels"].update(left=on, right=on, files=on)
                self.apply_panels()
                save_config(self.cfg)
            elif k == "E":
                self.cfg["effects"] = not self.cfg["effects"]
                save_config(self.cfg)
            elif k == "C" and t:
                t.copy_clipboard_format(Vte.Format.TEXT)
            elif k == "V" and t:
                t.paste_clipboard()
            else:
                return False
            return True
        if ev.state & Gdk.ModifierType.CONTROL_MASK and t:
            if k in ("plus", "equal", "KP_Add"):
                t.set_font_scale(min(t.get_font_scale() + 0.1, 3.0))
                return True
            if k in ("minus", "KP_Subtract"):
                t.set_font_scale(max(t.get_font_scale() - 0.1, 0.5))
                return True
            if k == "Page_Down":
                self.tabs.next_page()
                return True
            if k == "Page_Up":
                self.tabs.prev_page()
                return True
        return False


class ConsoleApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.veilos.Console")

    def do_activate(self):
        ui.apply_css()
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        win = ConsoleWindow(self)
        win.show_all()
        win.apply_panels()
        win.refresh_files()
        win.present()


def main() -> int:
    return ConsoleApp().run(sys.argv[:1])
