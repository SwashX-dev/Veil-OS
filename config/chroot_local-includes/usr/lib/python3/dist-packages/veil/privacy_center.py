"""Veil Privacy Center: one place for Veil's privacy and security settings."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from veil import diagnostics, media, privacy_settings as ps, ui, vpn  # noqa: E402

DEVICE_GUARD = "/usr/local/lib/veil/veil-device-guard"

LEVEL_TEXT = {
    "standard": "Standard: all browser features are enabled.",
    "safer": "Safer: JavaScript is off on non-HTTPS sites; some fonts, math symbols and media are click-to-play.",
    "safest": "Safest: JavaScript is off everywhere; only static content works.",
}
LOCK_LABELS = {0: "Never", 1: "After 1 minute", 2: "After 2 minutes", 5: "After 5 minutes",
               10: "After 10 minutes", 15: "After 15 minutes", 30: "After 30 minutes", 60: "After 1 hour"}
CLIP_LABELS = {0: "Never", 15: "After 15 seconds", 30: "After 30 seconds", 60: "After 1 minute",
               120: "After 2 minutes", 300: "After 5 minutes"}
UPSCALE_LABELS = {"off": "Off", "quality": "Quality (real-time, most GPUs)",
                  "enhanced": "Enhanced (FSRCNNX neural shader, capable GPU)"}
CIRCUITS_TEXT = (
    "Tor builds circuits through 3 relays. Connections to .onion sites use longer paths, "
    "and Tor already adds pinned extra relays to them (vanguards-lite). Veil does not "
    "make normal circuits longer: Tor has no supported option for it, the Tor Project "
    "found that extra relays do not protect against the attacks that matter (an observer "
    "who sees both ends), and every relay adds delay. Faking it would mean replacing "
    "Tor's own relay selection and stream isolation, which would weaken anonymity.")
VPN_TEXT = (
    "Optional. Import a WireGuard configuration file (for example from your Proton VPN "
    "account: Downloads → WireGuard configuration). Only Tor's own connections then go "
    "through the VPN: your local network sees VPN traffic instead of Tor, and the VPN "
    "provider sees your real IP address and that you use Tor, but nothing inside Tor. If "
    "the VPN drops, Tor cannot connect at all (no direct fallback). Connecting asks for "
    "the administration password.")


def _combo(options: dict, current) -> Gtk.ComboBoxText:
    combo = Gtk.ComboBoxText()
    for key, text in options.items():
        combo.append(str(key), text)
    if str(current) not in [str(k) for k in options]:
        combo.append(str(current), str(current))
    combo.set_active_id(str(current))
    return combo


def _section(title: str) -> tuple[Gtk.Frame, Gtk.Box]:
    frame = Gtk.Frame()
    ui.add_class(frame, "veil-panel")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_start(10)
    box.set_margin_end(10)
    box.set_margin_top(8)
    box.set_margin_bottom(10)
    box.pack_start(ui.label(title.upper(), "veil-h"), False, False, 0)
    frame.add(box)
    return frame, box


def _row(text: str, widget: Gtk.Widget, hint: str | None = None) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    left.pack_start(ui.label(text), False, False, 0)
    if hint:
        left.pack_start(ui.label(hint, "veil-muted", wrap=True), False, False, 0)
    row.pack_start(left, True, True, 0)
    widget.set_valign(Gtk.Align.CENTER)
    row.pack_end(widget, False, False, 0)
    return row


class PrivacyCenter(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Privacy Center")
        ui.add_class(self, "veil")
        self.set_default_size(760, 820)
        self.set_icon_name("security-high")
        self.settings = ps.load()
        self._loading = True

        hb = Gtk.HeaderBar(show_close_button=True, title="Privacy Center", subtitle="Veil OS")
        self.set_titlebar(hb)

        scroller = Gtk.ScrolledWindow()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("start", "end", "top", "bottom"):
            getattr(content, f"set_margin_{m}")(14)
        scroller.add(content)
        self.add(scroller)
        self.status = ui.label("", "veil-muted", wrap=True)
        content.pack_start(self.status, False, False, 0)

        # Tor Browser
        frame, box = _section("Tor Browser")
        self.level = _combo({k: k.capitalize() for k in ps.SECURITY_LEVELS}, self.settings["tor_browser_security_level"])
        self.level_hint = ui.label(LEVEL_TEXT[self.settings["tor_browser_security_level"]], "veil-muted", wrap=True)
        self.level.connect("changed", self.on_level)
        box.pack_start(_row("Security level at startup", self.level,
                            "Tor Browser's own levels, shared with every Tor Browser user who chooses them. "
                            "Applies the next time Tor Browser starts."), False, False, 0)
        box.pack_start(self.level_hint, False, False, 0)
        content.pack_start(frame, False, False, 0)

        # Session
        frame, box = _section("Screen and clipboard")
        self.autolock = _combo(LOCK_LABELS, self.settings["auto_lock_minutes"])
        self.autolock.connect("changed", lambda c: self.save("auto_lock_minutes", int(c.get_active_id())))
        box.pack_start(_row("Lock the screen when idle", self.autolock,
                            "Only when a screen-lock password is set (Lock Screen asks for one)."), False, False, 0)
        self.clip = _combo(CLIP_LABELS, self.settings["clipboard_clear_seconds"])
        self.clip.connect("changed", lambda c: self.save("clipboard_clear_seconds", int(c.get_active_id())))
        box.pack_start(_row("Clear the clipboard", self.clip,
                            "Clears copied text (and the selection) after the last copy."), False, False, 0)
        self.hotkey = Gtk.Switch(active=self.settings["emergency_shutdown_hotkey"])
        self.hotkey.connect("notify::active", lambda s, _p: self.save("emergency_shutdown_hotkey", s.get_active()))
        box.pack_start(_row("Emergency shutdown with Ctrl+Alt+Shift+Delete", self.hotkey,
                            "Shuts down at once and erases memory, like removing the USB stick."), False, False, 0)
        content.pack_start(frame, False, False, 0)

        # Devices
        frame, box = _section("Camera and microphone")
        self.camera = Gtk.Switch()
        self.micro = Gtk.Switch()
        self.camera.connect("notify::active", lambda s, _p: self.on_device("camera", s))
        self.micro.connect("notify::active", lambda s, _p: self.on_device("microphone", s))
        box.pack_start(_row("Block cameras", self.camera, "Applications can no longer open any camera."), False, False, 0)
        box.pack_start(_row("Block microphones", self.micro, "Applications can no longer record audio."), False, False, 0)
        box.pack_start(ui.label("Blocking needs no password. Unblocking asks for the administration password.",
                                "veil-muted", wrap=True), False, False, 0)
        content.pack_start(frame, False, False, 0)

        # VPN
        frame, box = _section("VPN before Tor (Proton VPN, WireGuard)")
        box.pack_start(ui.label(VPN_TEXT, "veil-muted", wrap=True), False, False, 0)
        row = Gtk.Box(spacing=8)
        self.vpn_combo = Gtk.ComboBoxText()
        self.vpn_import = Gtk.Button(label="Import…")
        self.vpn_import.connect("clicked", self.on_vpn_import)
        self.vpn_button = ui.add_class(Gtk.Button(label="Connect"), "veil-accent")
        self.vpn_button.connect("clicked", self.on_vpn_toggle)
        row.pack_start(self.vpn_combo, True, True, 0)
        row.pack_start(self.vpn_import, False, False, 0)
        row.pack_start(self.vpn_button, False, False, 0)
        box.pack_start(row, False, False, 0)
        self.vpn_status = ui.label("", "veil-mono", wrap=True, selectable=True)
        box.pack_start(self.vpn_status, False, False, 0)
        content.pack_start(frame, False, False, 0)

        # Circuits
        frame, box = _section("Tor circuits")
        box.pack_start(ui.label(CIRCUITS_TEXT, "veil-muted", wrap=True), False, False, 0)
        content.pack_start(frame, False, False, 0)

        # Video
        frame, box = _section("Video")
        self.upscale = _combo(UPSCALE_LABELS, self.settings["video_upscaling"])
        self.upscale.connect("changed", lambda c: self.save("video_upscaling", c.get_active_id()))
        box.pack_start(_row("Upscaling in Veil Player", self.upscale,
                            "Enlarges videos to your screen in real time (720p → 1080p, 1080p → 4K) on this "
                            "computer only. Enhanced needs a capable GPU. Applies to Veil Player (Super+V); "
                            "Tails' Celluloid is a separate sandboxed app and is not changed."), False, False, 0)
        if media.safe_graphics():
            box.pack_start(ui.label("Safe graphics is active (software rendering): upscaling runs on the CPU "
                                    "and may stutter.", "veil-warn", wrap=True), False, False, 0)
        content.pack_start(frame, False, False, 0)

        # Graphics and diagnostics
        frame, box = _section("Graphics and diagnostics")
        box.pack_start(ui.label(f"Desktop rendering: {diagnostics.graphics_mode()}", wrap=True), False, False, 0)
        diag = Gtk.Button(label="Save diagnostics report")
        diag.connect("clicked", self.on_diagnostics)
        box.pack_start(_row("Report for display problems and crashes", diag,
                            "Saved to your Persistent folder if it is unlocked, otherwise to your home folder. "
                            "Contains no browsing data and no network addresses."), False, False, 0)
        content.pack_start(frame, False, False, 0)

        self._loading = False
        self.refresh_vpn_configs()
        self.refresh_async()

    # ---------------------------------------------------------------- helpers
    def say(self, text: str) -> None:
        self.status.set_text(text)

    def save(self, key: str, value) -> None:
        if self._loading:
            return
        try:
            self.settings = ps.update(key, value)
            ps.reload_daemon()
            self.say("Saved.")
        except (OSError, ValueError) as exc:
            self.say(f"Could not save: {exc}")

    def in_thread(self, work, done) -> None:
        def runner():
            try:
                result = work()
            except Exception as exc:  # shown to the user
                result = exc
            GLib.idle_add(done, result)
        threading.Thread(target=runner, daemon=True).start()

    # ---------------------------------------------------------------- Tor Browser
    def on_level(self, combo) -> None:
        level = combo.get_active_id()
        self.level_hint.set_text(LEVEL_TEXT.get(level, ""))
        self.save("tor_browser_security_level", level)

    # ---------------------------------------------------------------- devices
    def refresh_async(self) -> None:
        def work():
            r = subprocess.run(["sudo", "-n", DEVICE_GUARD, "status"], capture_output=True, text=True,
                               timeout=30, check=False)
            devices = json.loads(r.stdout) if r.returncode == 0 else {}
            return devices, vpn.status()

        def done(result):
            if isinstance(result, Exception):
                self.say(f"Status unavailable: {result}")
                return False
            devices, vpn_state = result
            self._loading = True
            self.camera.set_active(bool(devices.get("camera", {}).get("blocked")))
            self.micro.set_active(bool(devices.get("microphone", {}).get("blocked")))
            self._loading = False
            self.show_vpn(vpn_state)
            return False
        self.in_thread(work, done)

    def on_device(self, name: str, switch: Gtk.Switch) -> None:
        if self._loading:
            return
        block = switch.get_active()
        argv = (["sudo", "-n", DEVICE_GUARD, "block", name] if block else ["pkexec", DEVICE_GUARD, "allow", name])

        def work():
            return subprocess.run(argv, capture_output=True, text=True, timeout=300, check=False)

        def done(result):
            ok = not isinstance(result, Exception) and result.returncode == 0
            if ok:
                self.save(f"{name}_blocked", block)
                self.say(f"{name.capitalize()} {'blocked' if block else 'allowed'}.")
            else:
                self.say(f"Could not change the {name} setting.")
            self.refresh_async()
            return False
        self.in_thread(work, done)

    # ---------------------------------------------------------------- VPN
    def refresh_vpn_configs(self) -> None:
        self.vpn_combo.remove_all()
        for name in vpn.list_configs():
            self.vpn_combo.append(name, name)
        self.vpn_combo.set_active(0)

    def show_vpn(self, state: dict) -> None:
        if state.get("active"):
            age = state.get("latest_handshake_seconds_ago")
            hs = "no handshake yet" if age is None else f"last handshake {age} s ago"
            self.vpn_status.set_text(f"Connected to {state.get('endpoint', '?')} ({hs}). Tor uses only the VPN: "
                                     f"{'yes' if state.get('tor_routed_through_vpn') and state.get('firewall') else 'NO'}.")
            self.vpn_button.set_label("Disconnect")
        else:
            self.vpn_status.set_text("Not connected: Tor connects directly (or through bridges).")
            self.vpn_button.set_label("Connect")

    def on_vpn_import(self, _button) -> None:
        dialog = Gtk.FileChooserDialog(title="Import a WireGuard configuration", parent=self,
                                       action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Import", Gtk.ResponseType.ACCEPT)
        flt = Gtk.FileFilter()
        flt.set_name("WireGuard configuration (*.conf)")
        flt.add_pattern("*.conf")
        dialog.add_filter(flt)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            path = Path(dialog.get_filename())
            try:
                name, cfg = vpn.import_config(path)
                notes = f" Ignored: {', '.join(cfg.ignored)}." if cfg.ignored else ""
                self.say(f"Imported {name} ({cfg.summary()['endpoint']}).{notes}")
                self.refresh_vpn_configs()
                self.vpn_combo.set_active_id(name)
            except (OSError, vpn.ConfigError) as exc:
                self.say(f"Not imported: {exc}")
        dialog.destroy()

    def on_vpn_toggle(self, _button) -> None:
        connecting = self.vpn_button.get_label() == "Connect"
        name = self.vpn_combo.get_active_id()
        if connecting and not name:
            self.say("Import a WireGuard configuration first.")
            return
        self.vpn_button.set_sensitive(False)
        self.say("Connecting… (asks for the administration password)" if connecting else "Disconnecting…")

        def work():
            return vpn.connect(name) if connecting else vpn.disconnect()

        def done(result):
            self.vpn_button.set_sensitive(True)
            if isinstance(result, Exception):
                self.say(f"VPN error: {result}")
            elif result.returncode != 0:
                try:
                    message = json.loads(result.stdout).get("error", "")
                except ValueError:
                    message = (result.stderr or "").strip() or "authentication failed or was cancelled"
                self.say(f"VPN error: {message}")
            else:
                self.say("VPN connected: Tor now connects through it." if connecting
                         else "VPN disconnected: Tor connects directly again.")
            self.refresh_async()
            return False
        self.in_thread(work, done)

    # ---------------------------------------------------------------- diagnostics
    def on_diagnostics(self, button) -> None:
        button.set_sensitive(False)
        self.say("Collecting the report…")

        def work():
            return diagnostics.save(diagnostics.build_report())

        def done(result):
            button.set_sensitive(True)
            self.say(f"Report not saved: {result}" if isinstance(result, Exception) else f"Report saved: {result}")
            return False
        self.in_thread(work, done)


def main() -> int:
    ui.apply_css()
    app = Gtk.Application(application_id="org.veil.PrivacyCenter")
    app.connect("activate", lambda a: PrivacyCenter(a).show_all())
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
