"""The agent loop: model -> tool calls -> results -> model, until done."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable

from veil import common
from veil.agent import policy as pol
from veil.agent import prompts, tools
from veil.agent.session import Session

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


@dataclass
class UI:
    on_token: Callable[[str], None] | None = None
    on_tool_start: Callable[[str, dict], None] = lambda n, a: None
    on_tool_end: Callable[[str, tools.ToolResult], None] = lambda n, r: None
    on_status: Callable[[str], None] = lambda s: None
    confirm: Callable[[str, str], bool] = lambda kind, summary: False


def strip_think(text: str) -> str:
    return THINK_RE.sub("", text or "").strip()


FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _as_call(obj, i: int) -> dict | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    args = obj.get("arguments", obj.get("parameters", {}))
    if not isinstance(name, str) or not isinstance(args, dict):
        return None
    return {"id": f"fallback_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def parse_fallback_tool_calls(content: str) -> list[dict]:
    """Recover tool calls that a model emitted as text instead of structured calls:
    <tool_call>{...}</tool_call> blocks, or a reply that is nothing but a JSON
    call object (optionally in a ```json fence)."""
    calls = []
    for i, m in enumerate(TOOL_CALL_RE.finditer(content or "")):
        try:
            c = _as_call(json.loads(m.group(1)), i)
        except json.JSONDecodeError:
            continue
        if c:
            calls.append(c)
    if calls:
        return calls
    text = strip_think(content or "").strip()
    fm = FENCE_RE.match(text)
    if fm:
        text = fm.group(1).strip()
    if text.startswith(("{", "[")):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return []
        for i, o in enumerate(obj if isinstance(obj, list) else [obj]):
            c = _as_call(o, i)
            if c:
                calls.append(c)
    return calls


@dataclass
class Agent:
    backend: object
    policy: pol.Policy
    mode: str = "agent"
    session: Session = field(default_factory=Session)
    ui: UI = field(default_factory=UI)
    temperature: float = 0.3
    thinking: bool = False

    def __post_init__(self) -> None:
        self._grant_marker()

    # ---- permissions -------------------------------------------------------
    def _grant_marker(self) -> None:
        d = common.amnesic_state_dir("agent") / "network-grants"
        d.mkdir(parents=True, exist_ok=True)
        marker = d / self.session.id
        if self.policy.network:
            marker.write_text(json.dumps({"pid": os.getpid(), "scope": self.policy.network_scope}))
        else:
            marker.unlink(missing_ok=True)

    def set_network(self, on: bool, scope: list[str] | None = None) -> None:
        self.policy.network = on
        self.policy.network_scope = scope or []
        self._grant_marker()
        self.session.log({"event": "network", "granted": on, "scope": self.policy.network_scope})

    def close(self) -> None:
        self.set_network(False)

    # ---- conversation --------------------------------------------------------
    def _system(self) -> dict:
        cwd = self.policy.workspace or os.path.expanduser("~")
        return {"role": "system",
                "content": prompts.system_prompt(self.mode, self.policy.describe(), self.policy.network, cwd,
                                                 self.policy.writable_roots())}

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in tools.tools_for(self.policy.level, self.mode)]

    def run(self, user_text: str) -> str:
        msgs = self.session.messages
        if not msgs or msgs[0].get("role") != "system":
            msgs.insert(0, self._system())
        else:
            msgs[0] = self._system()
        msgs.append({"role": "user", "content": user_text})
        self.session.log({"event": "user", "text": user_text})
        schemas = self._tool_schemas()
        allowed = {t.name for t in tools.tools_for(self.policy.level, self.mode)}
        ctx = tools.ToolContext(policy=self.policy, confirm=self._confirm, log=self.session.log)
        final = ""
        for step in range(self.policy.max_steps):
            self.ui.on_status(f"thinking (step {step + 1})")
            reply = self.backend.chat(msgs, tools=schemas or None, temperature=self.temperature,
                                      thinking=self.thinking, on_token=self.ui.on_token)
            reply.pop("_usage", None)
            content = reply.get("content") or ""
            calls = reply.get("tool_calls") or (parse_fallback_tool_calls(content) if schemas else [])
            clean = strip_think(TOOL_CALL_RE.sub("", content))
            entry = {"role": "assistant", "content": clean}
            if calls:
                entry["tool_calls"] = calls
            msgs.append(entry)
            if not calls:
                final = clean
                break
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool = tools.by_name(name)
                if tool is None or name not in allowed:
                    result = tools.ToolResult(False, f"tool {name!r} is not available in "
                                                     f"{self.mode} mode at level {self.policy.level}")
                else:
                    self.ui.on_tool_start(name, args)
                    try:
                        result = tool.handler(args, ctx)
                    except Exception as e:  # noqa: BLE001 - report to model, never crash
                        result = tools.ToolResult(False, f"{type(e).__name__}: {e}")
                self.ui.on_tool_end(name, result)
                self.session.log({"event": "tool", "name": name, "args": args, "ok": result.ok,
                                  "output": result.output[:4000]})
                msgs.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": name,
                             "content": result.for_model()})
        else:
            final = (f"[veil] Stopped after {self.policy.max_steps} steps without finishing. "
                     "Review the tool log; say 'continue' to keep going.")
        self.session.log({"event": "assistant", "text": final})
        return final

    def _confirm(self, kind: str, summary: str) -> bool:
        ok = bool(self.ui.confirm(kind, summary))
        self.session.log({"event": "confirm", "kind": kind, "approved": ok, "summary": summary[:500]})
        return ok
