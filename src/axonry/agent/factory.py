"""Agent backends. The orchestrator only knows the AgentRunner interface (agent/base.py); which backend runs the
tasks is configuration: [agent] runner = "<name>", with the backend's own settings in [agent.<name>].
A new backend (another agent cloud, or our own environment running Codex / Claude) is a class with `name`,
`from_config(section)` and the AgentRunner methods, registered here; nothing else changes.
"""
import importlib

BACKENDS = {"arbos": "axonry.agent.arbos:ArbosRunner"}


def make_runner(cfg):
    name = cfg.get("agent.runner", "arbos")
    if name not in BACKENDS:
        raise KeyError(f"unknown agent runner {name!r} (known: {sorted(BACKENDS)})")
    mod, _, cls = BACKENDS[name].partition(":")
    return getattr(importlib.import_module(mod), cls).from_config(cfg.get(f"agent.{name}", {}) or {})
