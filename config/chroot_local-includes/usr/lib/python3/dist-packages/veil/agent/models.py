"""Model catalog, hardware detection and explicit, verified downloads.

No model is ever downloaded automatically. `pull()` requires a consent
callback that only a human can satisfy, requires Tor to be connected, and
verifies a SHA-256 hash pinned in this file (or in /etc/veil/models.d/*.toml)
against an immutable, commit-pinned URL.
"""

from __future__ import annotations

import glob
import hashlib
import os
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from veil import common

HF = "https://huggingface.co"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    title: str
    file: str
    url: str
    sha256: str
    size: int          # bytes
    params: str
    license: str
    notes: str
    min_ram_gb: float  # free RAM needed to run comfortably (weights + KV cache)
    tools: bool = True


def _hf(repo: str, rev: str, file: str) -> str:
    return f"{HF}/{repo}/resolve/{rev}/{file}"


BUILTIN: list[ModelSpec] = [
    ModelSpec("qwen3-0.6b", "Qwen3 0.6B (Q8_0)", "Qwen3-0.6B-Q8_0.gguf",
              _hf("Qwen/Qwen3-0.6B-GGUF", "23749fefcc72300e3a2ad315e1317431b06b590a", "Qwen3-0.6B-Q8_0.gguf"),
              "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031", 639446688,
              "0.6B", "Apache-2.0", "Tiny; fast on any CPU; weak at multi-step tool use.", 1.5),
    ModelSpec("qwen3-1.7b", "Qwen3 1.7B (Q8_0)", "Qwen3-1.7B-Q8_0.gguf",
              _hf("Qwen/Qwen3-1.7B-GGUF", "90862c4b9d2787eaed51d12237eafdfe7c5f6077", "Qwen3-1.7B-Q8_0.gguf"),
              "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a", 1834426016,
              "1.7B", "Apache-2.0", "Light default for 8 GB machines.", 3.0),
    ModelSpec("qwen3-4b", "Qwen3 4B (Q4_K_M)", "Qwen3-4B-Q4_K_M.gguf",
              _hf("Qwen/Qwen3-4B-GGUF", "bc640142c66e1fdd12af0bd68f40445458f3869b", "Qwen3-4B-Q4_K_M.gguf"),
              "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5", 2497280256,
              "4B", "Apache-2.0", "Good balance of speed and tool-use quality.", 4.5),
    ModelSpec("qwen3-8b", "Qwen3 8B (Q4_K_M)", "Qwen3-8B-Q4_K_M.gguf",
              _hf("Qwen/Qwen3-8B-GGUF", "7c41481f57cb95916b40956ab2f0b139b296d974", "Qwen3-8B-Q4_K_M.gguf"),
              "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785", 5027783488,
              "8B", "Apache-2.0", "Strongest general agent here; needs 16 GB RAM or a GPU.", 8.0),
    ModelSpec("qwen2.5-coder-7b", "Qwen2.5 Coder 7B Instruct (Q4_K_M)", "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
              _hf("Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", "13fb94bfda8c8cf22497dc57b78f391a9acb426a",
                  "qwen2.5-coder-7b-instruct-q4_k_m.gguf"),
              "509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c", 4683073536,
              "7B", "Apache-2.0", "Code generation and debugging.", 7.5),
]


def catalog() -> dict[str, ModelSpec]:
    """Built-in catalog plus admin-provided entries in /etc/veil/models.d."""
    out = {m.name: m for m in BUILTIN}
    for path in sorted(glob.glob("/etc/veil/models.d/*.toml")):
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            for name, d in data.get("model", {}).items():
                out[name] = ModelSpec(name=name, title=d.get("title", name), file=d["file"], url=d["url"],
                                      sha256=d["sha256"].lower(), size=int(d["size"]),
                                      params=d.get("params", "?"), license=d.get("license", "?"),
                                      notes=d.get("notes", ""), min_ram_gb=float(d.get("min_ram_gb", 4)),
                                      tools=bool(d.get("tools", True)))
        except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError):
            continue
    return out


# --------------------------------------------------------------------------
# Hardware
# --------------------------------------------------------------------------

GPU_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}


def hardware() -> dict:
    mem = common.mem_info()
    flags: set[str] = set()
    model = "?"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("flags") and not flags:
                flags = set(line.split(":", 1)[1].split())
            elif line.startswith("model name") and model == "?":
                model = line.split(":", 1)[1].strip()
    except OSError:
        pass
    gpus = []
    for dev in sorted(glob.glob("/sys/class/drm/card[0-9]*/device/vendor")):
        try:
            v = Path(dev).read_text().strip()
        except OSError:
            continue
        gpus.append(GPU_VENDORS.get(v, v))
    vulkan = bool(glob.glob("/usr/share/vulkan/icd.d/*.json")) and any(
        os.path.exists(p) for p in ("/usr/lib/x86_64-linux-gnu/libvulkan.so.1", "/usr/lib/libvulkan.so.1"))
    return {
        "cpu": model,
        "threads": os.cpu_count() or 1,
        "avx2": "avx2" in flags,
        "avx512": "avx512f" in flags,
        "ram_total": mem.get("MemTotal", 0),
        "ram_available": mem.get("MemAvailable", 0),
        "gpus": sorted(set(gpus)),
        "vulkan": vulkan,
    }


def recommend(hw: dict | None = None) -> ModelSpec:
    """Largest catalog model that fits in half of the available RAM.

    Veil keeps its writable filesystem in RAM too, so we leave headroom.
    """
    hw = hw or hardware()
    budget_gb = hw["ram_available"] / (1024 ** 3) * 0.5
    fitting = [m for m in BUILTIN if m.min_ram_gb <= budget_gb and m.tools]
    if not fitting:
        return BUILTIN[0]
    return max(fitting, key=lambda m: m.min_ram_gb)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def model_dir(create: bool = True) -> tuple[Path, bool]:
    """Return (directory, persistent?).

    Without Persistent Storage, models go to ~/.local/share/veil/models: on
    Veil/Tails the home directory lives on the RAM-backed root overlay, so it
    is erased at shutdown, and unlike $XDG_RUNTIME_DIR (a tmpfs that logind
    caps at 10% of RAM) it can hold multi-GB model files.
    """
    p = common.persistent_state_dir("models") if create else None
    if p:
        return p, True
    if not create and common.persistence_unlocked():
        return common.PERSISTENT_DIR / ".veil" / "models", True
    d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "veil" / "models"
    if create:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d, False


def local_models() -> list[dict]:
    out = []
    seen = set()
    dirs = [model_dir()[0]]
    extra = os.environ.get("VEIL_MODEL_PATH")
    if extra:
        dirs += [Path(p) for p in extra.split(":") if p]
    cat = {m.file: m for m in catalog().values()}
    for d in dirs:
        for f in sorted(d.glob("*.gguf")):
            if f.name in seen:
                continue
            seen.add(f.name)
            spec = cat.get(f.name)
            out.append({"name": spec.name if spec else f.stem, "file": str(f),
                        "size": f.stat().st_size, "catalog": spec is not None})
    return out


def resolve(name_or_path: str) -> Path | None:
    p = Path(os.path.expanduser(name_or_path))
    if p.suffix == ".gguf" and p.exists():
        return p
    spec = catalog().get(name_or_path)
    for m in local_models():
        if m["name"] == name_or_path or (spec and Path(m["file"]).name == spec.file):
            return Path(m["file"])
    return None


def sha256_file(path: Path, chunk: int = 4 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class ConsentDenied(RuntimeError):
    pass


def pull(name: str, consent, progress=None, require_tor: bool = True) -> Path:
    """Download a catalog model after explicit consent; verify SHA-256.

    `consent(spec, dest, persistent) -> bool` must ask the human.
    """
    spec = catalog().get(name)
    if spec is None:
        raise KeyError(f"unknown model {name!r}; see `veil agent models list`")
    if require_tor and not common.tor_bootstrapped():
        raise RuntimeError("Tor is not connected; connect with Tor Connection first")
    dest_dir, persistent = model_dir()
    dest = dest_dir / spec.file
    if dest.exists() and sha256_file(dest) == spec.sha256:
        return dest
    if not consent(spec, dest, persistent):
        raise ConsentDenied("download not approved")
    free = os.statvfs(dest_dir)
    if free.f_bavail * free.f_frsize < spec.size * 1.05:
        raise RuntimeError(f"not enough space in {dest_dir} for {common.human_bytes(spec.size)}")
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(spec.url, headers={"User-Agent": "veil-agent"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    # As the live user, this TCP connection is transparently routed through
    # Tor by the (unchanged) Tails firewall. Nothing here bypasses it.
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - pinned https URL
        mode = "ab" if have and r.status == 206 else "wb"
        if mode == "wb":
            have = 0
        with open(part, mode) as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                have += len(b)
                if progress:
                    progress(have, spec.size)
    digest = sha256_file(part)
    if digest != spec.sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 mismatch for {spec.file}: got {digest}, expected {spec.sha256}")
    os.replace(part, dest)
    os.chmod(dest, 0o600)
    return dest


def verify(name_or_path: str) -> tuple[bool, str]:
    p = resolve(name_or_path)
    if p is None:
        return False, "not installed"
    spec = {m.file: m for m in catalog().values()}.get(p.name)
    if spec is None:
        return True, "local file (not in catalog, hash not pinned)"
    got = sha256_file(p)
    return got == spec.sha256, ("OK " if got == spec.sha256 else "MISMATCH ") + got


def remove(name_or_path: str) -> bool:
    p = resolve(name_or_path)
    if p is None:
        return False
    p.unlink()
    return True
