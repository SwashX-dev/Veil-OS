"""Veil Agent: a fully local, terminal-native AI agent.

Design rules (enforced in code, not in the system prompt):

* Inference runs locally (llama.cpp or Ollama) inside a sandbox with an
  empty network namespace. There is no cloud fallback and no telemetry.
* Every tool command runs in a bubblewrap sandbox. Network access is a
  separate grant that no permission level implies, not even full control.
* Destructive disk operations, credential access and security-boundary
  changes need a separate confirmation typed by the human. The model can
  never answer its own confirmation prompts.
* Models are never downloaded automatically.
"""
