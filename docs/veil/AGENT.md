# Veil Agent

A fully local, terminal-native AI agent. You start it with `veil agent`, or
open the GUI (`veil-agent-gui`, Super+A).

## Guarantees (enforced in code, tested in `tests/veil/test_veil.py`)

| Property | How |
|---|---|
| Local inference only | llama.cpp (`llama-server`) built from a pinned tarball, or Ollama if you install it. No cloud backend exists in the code. |
| Inference server has no network | It runs in bubblewrap with an **empty network namespace**, all of `/run` masked (no nscd/resolver/D-Bus sockets) and seccomp. The agent reaches it only through a Unix socket. |
| Offline by default | Tool commands run in the same kind of sandbox. `network` defaults to false in code. `/etc/veil/agent.toml` **cannot** turn it on, and full-control mode doesn't imply it. |
| Network is a separate grant | `veil agent --allow-network`, `/net on [scope]`, or the GUI switch. You must confirm on the TTY or in a dialog. When granted, traffic still goes through Tor (Tails firewall). |
| No model downloads unless you ask | `veil agent models pull NAME` is a human-only command (not an agent tool). It needs Tor, asks for confirmation, uses commit-pinned URLs and verifies SHA-256. |
| No telemetry, no accounts, no API keys | There is no code path for any of them. |
| Separate authorization for dangerous actions | Destructive disk operations, credential access, security-boundary changes and mass deletes are classified before running, and you must approve each one. Without a TTY or GUI, they are denied. |
| The model can't escalate | Sandboxed commands run with `no_new_privs` (sudo and setuid don't work), and ptrace, BPF, keyrings, mount and setns are blocked. Credential stores are hidden. |

The classifier works by pattern matching, so it is a *second* layer. The
sandbox is what enforces the boundaries.

## Modes and permission levels

- **Modes:** `direct` (no tools, concise), `agent` (autonomous multi-step),
  `research` (read-only analysis), `terminal` (command-focused).
- **Levels:** `read-only` (the default: nothing writable except the
  sandbox's private /tmp), `workspace` (only the chosen directory is
  writable), `full-control` (your home is writable; requires typing
  `FULL CONTROL`).
- **Independent grants:** network (through Tor), secrets (credential stores).

## Tools

shell, read_file, write_file, edit_file (writes go through the sandbox and
come back with diffs), list_dir, search_files, git, system_info,
process_list, read_logs, network_policy (Network Guard), security_audit,
docs_search (offline Veil/Tails docs and man pages), packages (Nix),
lab (offline labs), project_memory (`.veil/memory.md` in the workspace).

## Models

```sh
veil agent models list        # catalog + hardware-aware recommendation
veil agent models pull qwen3-4b
veil agent models verify
veil agent "summarize the open ports in scan.xml" --level workspace --workspace ~/Persistent/proj
```

| Name | Size | RAM needed | License |
|---|---|---|---|
| qwen3-0.6b | 0.6 GB | ~1.5 GiB | Apache-2.0 |
| qwen3-1.7b | 1.8 GB | ~3 GiB | Apache-2.0 |
| qwen3-4b | 2.5 GB | ~4.5 GiB | Apache-2.0 |
| qwen3-8b | 5.0 GB | ~8 GiB | Apache-2.0 |
| qwen2.5-coder-7b | 4.7 GB | ~7.5 GiB | Apache-2.0 |

Models go to `~/Persistent/.veil/models` when Persistent Storage is unlocked
(LUKS2-encrypted). Otherwise they go to RAM and are lost at shutdown.
Resource limits: `--threads`, `--context`, `--mem-limit 6G`,
`--cpu-quota 400%` (systemd cgroup scope), and `--gpu-layers N` for Vulkan
GPU offload. Use `/unload` to free RAM. The model loads only when first
needed, never at boot.

**Ollama:** `veil install bundle:ai`, then `veil agent --backend ollama --model qwen3:4b`.
Ollama also runs inside the no-network sandbox, behind a Unix-socket bridge.
`/usr/share/veil/agent/Modelfile` sets up the same behavior for Ollama.

## Memory

- Sessions and execution logs are disposable. They live in
  `$XDG_RUNTIME_DIR/veil/agent` (RAM, mode 0600).
- `/save NAME` copies a session to `~/Persistent/.veil/agent/sessions`. This
  works only while Persistent Storage is unlocked.
- Project memory is a plain file in your workspace, `.veil/memory.md`.
- `veil agent sessions wipe` deletes all saved sessions and logs. On flash
  memory, deleting a file doesn't guarantee the blocks are erased; the
  persistence encryption is what protects them.
