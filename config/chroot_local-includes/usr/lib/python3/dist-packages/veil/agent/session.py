"""Conversation sessions and memory.

Default: disposable. Sessions and execution logs live in $XDG_RUNTIME_DIR
(RAM) and disappear at shutdown, like everything else in Amnesic Mode.

`save()` copies a session into ~/Persistent/.veil/agent/sessions, which is
inside the LUKS2-encrypted Persistent Storage, and only if it is unlocked.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from veil import common


class PersistenceLocked(RuntimeError):
    pass


def _persistent_root() -> Path | None:
    p = common.persistent_state_dir("agent")
    if p:
        (p / "sessions").mkdir(exist_ok=True, mode=0o700)
    return p


class Session:
    def __init__(self, mode: str = "agent", sid: str | None = None):
        self.id = sid or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.mode = mode
        self.messages: list[dict] = []
        self.dir = common.amnesic_state_dir("agent") / "sessions" / self.id
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.log_path = self.dir / "execution.jsonl"
        self.created = time.time()

    # ---- execution log (RAM, 0600) --------------------------------------
    def log(self, event: dict) -> None:
        event = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def snapshot(self) -> dict:
        return {"id": self.id, "mode": self.mode, "created": self.created,
                "messages": [m for m in self.messages if m.get("role") != "system"]}

    # ---- explicit persistence -------------------------------------------
    def save(self, name: str | None = None) -> Path:
        root = _persistent_root()
        if root is None:
            raise PersistenceLocked("Persistent Storage is locked or absent: nothing can be saved. "
                                    "Unlock it in the Welcome Screen to keep sessions.")
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name or self.id)[:80]
        dest = root / "sessions" / safe
        dest.mkdir(parents=True, exist_ok=True, mode=0o700)
        (dest / "session.json").write_text(json.dumps(self.snapshot(), indent=1, ensure_ascii=False))
        if self.log_path.exists():
            shutil.copy2(self.log_path, dest / "execution.jsonl")
        for f in dest.iterdir():
            os.chmod(f, 0o600)
        return dest

    @classmethod
    def load(cls, name: str) -> Session:
        root = _persistent_root()
        if root is None:
            raise PersistenceLocked("Persistent Storage is locked")
        data = json.loads((root / "sessions" / name / "session.json").read_text())
        s = cls(mode=data.get("mode", "agent"))
        s.messages = data.get("messages", [])
        return s

    def discard(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def list_saved() -> list[dict]:
    root = _persistent_root()
    if root is None:
        return []
    out = []
    for d in sorted((root / "sessions").iterdir()):
        f = d / "session.json"
        if f.exists():
            try:
                data = json.loads(f.read_text())
                first = next((m["content"] for m in data.get("messages", []) if m.get("role") == "user"), "")
                out.append({"name": d.name, "mode": data.get("mode"), "messages": len(data.get("messages", [])),
                            "first": (first or "")[:70]})
            except (OSError, json.JSONDecodeError):
                continue
    return out


def delete_saved(name: str) -> bool:
    root = _persistent_root()
    if root is None:
        return False
    d = root / "sessions" / name
    if d.is_dir():
        shutil.rmtree(d)
        return True
    return False


def wipe_all() -> dict:
    """Delete every agent session and execution log, in RAM and persistent.

    Note: on flash storage, deletion cannot guarantee that old blocks are
    physically erased; Persistent Storage encryption is what protects them.
    """
    removed = {"amnesic": 0, "persistent": 0}
    ram = common.amnesic_state_dir("agent") / "sessions"
    if ram.exists():
        removed["amnesic"] = len(list(ram.iterdir()))
        shutil.rmtree(ram, ignore_errors=True)
    if common.persistence_unlocked():
        root = common.PERSISTENT_DIR / ".veil" / "agent" / "sessions"
        if root.exists():
            removed["persistent"] = len(list(root.iterdir()))
            shutil.rmtree(root, ignore_errors=True)
    return removed
