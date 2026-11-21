"""Veil Player: video playback with optional real-time upscaling.

Upscaling happens on your computer only, after decoding, so it changes
nothing that a website or a network observer can see.

  off       mpv's default scaling
  quality   mpv's built-in high-quality profile (EWA Lanczos, debanding),
            real time on most GPUs
  enhanced  quality + FSRCNNX, a small neural-network upscaling shader
            (2x, active when a video is enlarged by more than 1.3x, for
            example 720p on a 1080p screen or 1080p on a 4K screen); needs a
            capable GPU for real-time playback

Playback scales to the window/screen size: 720p becomes 1080p on a 1080p
screen, and 1080p becomes 4K on a 4K screen. To make an upscaled copy of a
file instead, use `veil media upscale` (not real time).

Media files are a common attack surface: the player and the converter run in
Veil's sandbox with no network, a read-only system, hidden secrets, and only
the display, audio and (unless safe graphics is active) GPU devices.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from veil import privacy_settings, sandbox

SHADER = "/usr/share/veil/mpv/shaders/FSRCNNX_x2_8-0-4-1.glsl"
CELLULOID_SCHEMA = "io.github.celluloid_player.Celluloid"
TARGET_HEIGHTS = {"1080": 1080, "1080p": 1080, "1440": 1440, "1440p": 1440, "2160": 2160, "2160p": 2160,
                  "4k": 2160}


def safe_graphics(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get("VEIL_SAFE_GRAPHICS"))


def mpv_options(mode: str, env: dict | None = None) -> list[str]:
    if mode not in privacy_settings.UPSCALING_MODES:
        raise ValueError(f"upscaling must be one of {', '.join(privacy_settings.UPSCALING_MODES)}")
    opts: list[str] = []
    if mode in ("quality", "enhanced"):
        opts.append("--profile=high-quality")
    if mode == "enhanced":
        opts.append(f"--glsl-shaders={SHADER}")
    if safe_graphics(env):
        # Software OpenGL (llvmpipe): never touch the unstable GPU driver.
        opts.append("--gpu-api=opengl")
    return opts


def celluloid_options(mode: str) -> str:
    # Celluloid embeds mpv itself: same presets, without the GPU API choice.
    return " ".join(o for o in mpv_options(mode, env={}))


def sync_celluloid(mode: str) -> bool:
    """Make Celluloid (the default video player) use the same preset."""
    if not shutil.which("gsettings"):
        return False
    r = subprocess.run(["gsettings", "set", CELLULOID_SCHEMA, "mpv-options", celluloid_options(mode)],
                       capture_output=True, text=True, check=False)
    return r.returncode == 0


def _session_sockets(env: dict) -> tuple[list[str], dict[str, str]]:
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    display = env.get("WAYLAND_DISPLAY", "wayland-0")
    sockets = [p for p in (f"{runtime}/{display}", f"{runtime}/pulse/native") if os.path.exists(p)]
    extra = {"XDG_RUNTIME_DIR": runtime, "WAYLAND_DISPLAY": display}
    if f"{runtime}/pulse/native" in sockets:
        extra["PULSE_SERVER"] = f"unix:{runtime}/pulse/native"
    for var in ("LIBGL_ALWAYS_SOFTWARE", "VK_LOADER_DRIVERS_SELECT", "GSK_RENDERER"):
        if env.get(var):
            extra[var] = env[var]
    return sockets, extra


def player_spec(files: list[Path], env: dict | None = None) -> sandbox.SandboxSpec:
    env = dict(os.environ if env is None else env)
    sockets, extra = _session_sockets(env)
    return sandbox.SandboxSpec(
        network=False, readonly_root=True, writable=sockets,
        readonly=[str(f.resolve()) for f in files], hide_secrets=True, hide_session=True,
        unshare_pid=True, hostname="veil-player", extra_env=extra,
        devices=[] if safe_graphics(env) else ["/dev/dri"])


def play_argv(files: list[Path], mode: str, extra: list[str] | None = None, env: dict | None = None) -> list[str]:
    cmd = ["mpv", "--ytdl=no", "--save-position-on-quit=no", "--input-ipc-server=",
           *mpv_options(mode, env), *(extra or []), "--", *[str(f.resolve()) for f in files]]
    return cmd


def play(files: list[Path], mode: str | None = None, extra: list[str] | None = None) -> int:
    for f in files:
        if not f.is_file():
            raise FileNotFoundError(f"not a file: {f}")
    mode = mode or privacy_settings.load()["video_upscaling"]
    spec = player_spec(files)
    proc = sandbox.popen(spec, play_argv(files, mode, extra))
    return proc.wait()


def probe(path: Path) -> dict:
    """Width/height of the first video stream (ffprobe, sandboxed)."""
    spec = sandbox.SandboxSpec(network=False, readonly=[str(path.resolve())], hostname="veil-probe")
    r = sandbox.run(spec, ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height", "-of", "json", str(path.resolve())], timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr.strip()[-300:]}")
    streams = json.loads(r.stdout).get("streams") or []
    if not streams:
        raise RuntimeError("no video stream found")
    return {"width": int(streams[0]["width"]), "height": int(streams[0]["height"])}


def upscale_argv(src: Path, dest: Path, height: int, enhanced: bool = False) -> list[str]:
    if enhanced:
        vf = (f"libplacebo=w=-2:h={height}:upscaler=ewa_lanczossharp:custom_shader_path={SHADER}"
              ":format=yuv420p")
        hw = ["-init_hw_device", "vulkan"]
    else:
        vf = f"scale=-2:{height}:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv420p"
        hw = []
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *hw,
            "-i", str(src.resolve()), "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "copy", str(dest)]


def upscale(src: Path, target: str, dest: Path | None = None, enhanced: bool = False,
            timeout: float | None = None) -> Path:
    key = target.lower()
    if key not in TARGET_HEIGHTS:
        raise ValueError("target must be 1080p, 1440p or 2160p (4K)")
    height = TARGET_HEIGHTS[key]
    info = probe(src)
    if info["height"] >= height:
        raise ValueError(f"the video is already {info['height']} pixels high")
    dest = dest or src.with_name(f"{src.stem}-{height}p.mkv")
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    out_dir = dest.resolve().parent
    devices = [] if (safe_graphics() or not enhanced) else ["/dev/dri"]
    extra = {}
    if enhanced and safe_graphics():
        # Software Vulkan (lavapipe): slow, but never uses the GPU driver.
        extra["VK_LOADER_DRIVERS_SELECT"] = "*lvp*"
    spec = sandbox.SandboxSpec(network=False, readonly=[str(src.resolve())], writable=[str(out_dir)],
                               hostname="veil-upscale", devices=devices, extra_env=extra)
    r = sandbox.run(spec, upscale_argv(src, dest.resolve(), height, enhanced), timeout=timeout)
    if r.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-500:]}")
    return dest
