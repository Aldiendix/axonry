"""Agent runner interface. The supervisor only talks to this; arbos is the first implementation."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class RunHandle:
    sid: str
    workspace: str          # arbos project (one per miner hotkey and account)
    chat: str = ""          # one chat per submission
    data: dict = field(default_factory=dict)


@dataclass
class RunStatus:
    busy: bool              # a turn or background job is still working
    last_activity: float    # unix time of the newest row
    pending_approvals: list = field(default_factory=list)
    error: str | None = None   # the backend cannot run the agent at all (e.g. its machine is held): ours
    usd: float | None = None   # what the run's agent has cost so far, workers included (None: not reported)
    tokens: int | None = None  # the tokens it has used so far (input, output, cache), workers included


class AgentRunner(Protocol):
    name: str            # recorded with every run (submission.json -> run.agent.runner)
    capacity: int        # runs it can take at once

    def prepare(self, sid: str, hotkey: str, busy: list[RunHandle]) -> RunHandle: ...  # busy: runs under way
    def kickoff(self, h: RunHandle, message: str, bundle: Path) -> None: ...
    def poll(self, h: RunHandle) -> RunStatus: ...
    def steer(self, h: RunHandle, message: str) -> None: ...
    def reject(self, h: RunHandle, approval_id: str, reason: str) -> None: ...
    def stop(self, h: RunHandle) -> None: ...
    def export(self, h: RunHandle, dest: Path) -> bool: ...
    def keep_alive(self) -> None: ...   # every main tick: keep the backend's sign-ins valid
