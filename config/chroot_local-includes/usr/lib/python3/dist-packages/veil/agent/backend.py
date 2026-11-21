"""Local inference backends.

The model server always runs inside a Veil sandbox with an EMPTY network
namespace. The agent talks to it through a Unix socket in a private
directory, so even a malicious model file that exploits the inference
engine cannot reach Tor, the LAN or the Internet.

Backends:
  llama.cpp  (default) `llama-server --host <dir>/llama.sock`
  ollama     `ollama serve` + an in-sandbox Unix->TCP bridge
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from veil import common, sandbox

LLAMA_SERVER_CANDIDATES = ("/usr/local/lib/veil/llama.cpp/bin/llama-server", "llama-server")


class BackendError(RuntimeError):
    pass


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 600):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def find_llama_server() -> str | None:
    env = os.environ.get("VEIL_LLAMA_SERVER")
    if env and os.path.exists(env):
        return env
    for c in LLAMA_SERVER_CANDIDATES:
        p = c if os.path.isabs(c) else shutil.which(c)
        if p and os.path.exists(p):
            return p
    return None


def _limits_prefix(mem_limit: str | None, cpu_quota: str | None) -> list[str]:
    """Wrap in a transient systemd scope for cgroup memory/CPU limits."""
    if not (mem_limit or cpu_quota) or not common.have("systemd-run"):
        return []
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not os.path.exists(f"/run/user/{os.getuid()}/bus"):
        return []
    argv = ["systemd-run", "--user", "--scope", "--quiet", "--collect"]
    if mem_limit:
        argv += ["-p", f"MemoryMax={mem_limit}", "-p", "MemorySwapMax=0"]
    if cpu_quota:
        argv += ["-p", f"CPUQuota={cpu_quota}"]
    return argv


class LlamaCppBackend:
    name = "llama.cpp"

    def __init__(self, model_path: Path, context: int = 8192, threads: int | None = None,
                 gpu_layers: int = 0, mem_limit: str | None = None, cpu_quota: str | None = None):
        self.model_path = Path(model_path)
        self.context = context
        self.threads = threads or max(1, (os.cpu_count() or 2) - 1)
        self.gpu_layers = gpu_layers
        self.mem_limit = mem_limit
        self.cpu_quota = cpu_quota
        self.proc: subprocess.Popen | None = None
        self.rundir = common.amnesic_state_dir("agent") / f"llama-{os.getpid()}"
        self.sock = self.rundir / "llama.sock"
        self.log = self.rundir / "server.log"

    def start(self, wait: float = 180) -> None:
        if self.proc and self.proc.poll() is None:
            return
        server = find_llama_server()
        if not server:
            raise BackendError("llama-server not found (expected /usr/local/lib/veil/llama.cpp/bin/llama-server)")
        if not self.model_path.exists():
            raise BackendError(f"model not found: {self.model_path}")
        self.rundir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sock.unlink(missing_ok=True)
        cmd = [server, "-m", str(self.model_path), "--host", str(self.sock),
               "-c", str(self.context), "-t", str(self.threads), "--jinja",
               "--no-webui", "-ngl", str(self.gpu_layers), "--log-disable"]
        spec = sandbox.SandboxSpec(network=False, readonly_root=True,
                                   writable=[str(self.rundir)], hide_secrets=True,
                                   hide_session=True, unshare_pid=True,
                                   hostname="veil-inference",
                                   devices=["/dev/dri", "/dev/nvidia*", "/dev/kfd"]
                                   if self.gpu_layers else [])
        argv_prefix = _limits_prefix(self.mem_limit, self.cpu_quota)
        logf = open(self.log, "wb")  # noqa: SIM115 - kept open for the child
        if argv_prefix:
            fd = sandbox._seccomp_fd(spec)
            full = argv_prefix + sandbox.build_argv(spec, cmd, fd)
            self.proc = subprocess.Popen(full, stdout=logf, stderr=subprocess.STDOUT,
                                         pass_fds=(fd,) if fd is not None else ())
            if fd is not None:
                os.close(fd)
        else:
            self.proc = sandbox.popen(spec, cmd, stdout=logf, stderr=subprocess.STDOUT)
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise BackendError(f"llama-server exited ({self.proc.returncode}); see {self.log}:\n"
                                   + self.log.read_text(errors="replace")[-1500:])
            if self.sock.exists():
                try:
                    st, body = self._request("GET", "/health", None, timeout=5)
                    if st == 200:
                        return
                except OSError:
                    pass
            time.sleep(0.5)
        raise BackendError("llama-server did not become ready in time")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.sock.unlink(missing_ok=True)

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _request(self, method: str, path: str, body: dict | None, timeout: float = 600):
        conn = UnixHTTPConnection(str(self.sock), timeout=timeout)
        try:
            data = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def chat(self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.3,
             max_tokens: int = 2048, thinking: bool = False, on_token=None) -> dict:
        """OpenAI-compatible chat completion. Returns the assistant message."""
        self.start()
        body = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": thinking}, "stream": on_token is not None}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if on_token is None:
            st, raw = self._request("POST", "/v1/chat/completions", body)
            if st != 200:
                raise BackendError(f"inference error {st}: {raw[:500]!r}")
            data = json.loads(raw)
            msg = data["choices"][0]["message"]
            msg["_usage"] = data.get("usage", {})
            return msg
        return self._chat_stream(body, on_token)

    def _chat_stream(self, body: dict, on_token) -> dict:
        conn = UnixHTTPConnection(str(self.sock), timeout=600)
        content, reasoning = [], []
        calls: dict[int, dict] = {}
        usage = {}
        try:
            conn.request("POST", "/v1/chat/completions", body=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            if r.status != 200:
                raise BackendError(f"inference error {r.status}: {r.read()[:500]!r}")
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if ev.get("usage"):
                    usage = ev["usage"]
                for ch in ev.get("choices", []):
                    d = ch.get("delta", {})
                    if d.get("reasoning_content"):
                        reasoning.append(d["reasoning_content"])
                    if d.get("content"):
                        content.append(d["content"])
                        on_token(d["content"])
                    for tc in d.get("tool_calls", []) or []:
                        slot = calls.setdefault(tc.get("index", 0),
                                                {"id": tc.get("id", ""), "type": "function",
                                                 "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function", {})
                        slot["function"]["name"] += fn.get("name", "") or ""
                        slot["function"]["arguments"] += fn.get("arguments", "") or ""
        finally:
            conn.close()
        msg: dict = {"role": "assistant", "content": "".join(content), "_usage": usage}
        if reasoning:
            msg["reasoning_content"] = "".join(reasoning)
        if calls:
            msg["tool_calls"] = [calls[i] for i in sorted(calls)]
        return msg


class OllamaBackend:
    """Ollama, run inside the no-network sandbox behind a Unix-socket bridge."""

    name = "ollama"

    def __init__(self, model: str, context: int = 8192):
        self.model = model
        self.context = context
        self.proc: subprocess.Popen | None = None
        self.rundir = common.amnesic_state_dir("agent") / f"ollama-{os.getpid()}"
        self.sock = self.rundir / "ollama.sock"

    def start(self, wait: float = 120) -> None:
        if self.proc and self.proc.poll() is None:
            return
        ollama = shutil.which("ollama")
        if not ollama:
            raise BackendError("ollama is not installed (try: veil install ollama)")
        self.rundir.mkdir(parents=True, exist_ok=True, mode=0o700)
        models_dir = os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama" / "models"))
        script = (f'OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="{models_dir}" "{ollama}" serve '
                  f'>"{self.rundir}/server.log" 2>&1 & '
                  f'exec python3 -I -m veil.agent.bridge "{self.sock}" 127.0.0.1 11434')
        spec = sandbox.SandboxSpec(network=False, writable=[str(self.rundir), models_dir],
                                   hostname="veil-inference",
                                   extra_env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])})
        self.proc = sandbox.popen(spec, ["sh", "-c", script])
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.sock.exists():
                try:
                    conn = UnixHTTPConnection(str(self.sock), timeout=5)
                    conn.request("GET", "/api/version")
                    if conn.getresponse().status == 200:
                        return
                except OSError:
                    pass
            time.sleep(0.5)
        raise BackendError("ollama did not become ready")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def chat(self, messages, tools=None, temperature=0.3, max_tokens=2048, thinking=False, on_token=None):
        self.start()
        body = {"model": self.model, "messages": messages, "stream": False, "think": thinking,
                "options": {"temperature": temperature, "num_ctx": self.context, "num_predict": max_tokens}}
        if tools:
            body["tools"] = tools
        conn = UnixHTTPConnection(str(self.sock))
        conn.request("POST", "/api/chat", body=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        raw = r.read()
        if r.status != 200:
            raise BackendError(f"ollama error {r.status}: {raw[:300]!r}")
        msg = json.loads(raw)["message"]
        for i, tc in enumerate(msg.get("tool_calls", []) or []):
            fn = tc.get("function", {})
            if isinstance(fn.get("arguments"), dict):
                fn["arguments"] = json.dumps(fn["arguments"])
            tc.setdefault("id", f"call_{i}")
            tc.setdefault("type", "function")
        if on_token and msg.get("content"):
            on_token(msg["content"])
        return msg
