"""ArbosRunner: drives the full arbos platform as the owner's account through `arbosctl`.

- Several arbos accounts ([agent.arbos] accounts: name, runs). Each account has one box (8 CPU, 16 GiB) shared by
  all its projects, so it takes at most `runs` runs at once; a new run goes to the account with the most room, and
  its handle remembers the account.
- One arbos project (workspace: its own Linux user and folder on the box) per miner hotkey and account:
  `axonry-<hotkey[:12]>`, created on first use and reused.
- One chat per submission (title = sid). The task bundle (task.json, BRIEF.md, submission/) is sent as an
  attachment of the kickoff message; arbos stores it in the workspace under in/<ref>-<name> and names the path.
- Status comes from `chat dump` (the CRDT replica as JSON), as arbos' own eval driver does:
  streaming rows / an ack without its background result mean the agent is still working.
- Each account is signed in once (python -m axonry.agent.arbos.auth --account NAME); arbosctl runs with that
  account's config.
"""
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from ..base import RunHandle, RunStatus


log = logging.getLogger(__name__)


class ArbosError(RuntimeError):
    pass


def project_name(hotkey: str) -> str:
    return f"axonry-{hotkey[:12].lower()}"


def _ts(row):
    for k in ("updatedAt", "createdAt", "ts", "time"):
        v = row.get(k)
        if isinstance(v, (int, float)):
            return v / 1000 if v > 1e12 else v
    return 0.0


def pending_approvals(doc):
    out = []
    for m in doc.get("messages", []):
        for p in m.get("parts", []):
            if p.get("kind") == "approval" and p.get("state") in (None, "pending") and not p.get("decision") \
                    and not p.get("decidedBy"):
                out.append(p.get("approvalId") or p.get("id"))
    return [a for a in out if a]


def busy(doc) -> bool:
    """Is the agent still working on the last user turn (main turn or its background job)?
    A background job that ended (arbos caps one at 60 minutes) can leave a "streaming" row of the same job behind:
    a job with an ended row is not working."""
    msgs = doc.get("messages", [])
    ended = {(m.get("job") or {}).get("id") for m in msgs if m.get("job") and m.get("status") in ENDED}
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    rows = [m for m in msgs[last_user + 1:] if m.get("role") == "assistant"
            and not (m.get("status") == "streaming" and (m.get("job") or {}).get("id") in ended)]
    if not rows:
        return last_user >= 0
    if any(r.get("status") == "streaming" for r in rows):
        return True
    last = rows[-1]
    if last.get("turnRole") == "ack" or (last.get("decision") in ("new", "wake") and not last.get("subagentId")):
        return True
    return last.get("status") not in ENDED


ENDED = ("done", "error", "failed")
BACKGROUND = "Run this as a background job until the deadline. "   # arbos: forces a job instead of a chat turn


class ArbosRunner:
    name = "arbos"

    @classmethod
    def from_config(cls, section: dict):
        """[agent.arbos] bin, instructions, accounts = [{name, runs}]; tokens from `python -m axonry.agent.arbos.auth
        --account NAME` (refreshed automatically)."""
        from . import auth  # noqa: PLC0415
        accounts = {a["name"]: int(a["runs"]) for a in section.get("accounts") or [{"name": "main", "runs": 6}]}
        return cls(accounts, section.get("bin", "arbosctl"), auth.arbosctl_config,
                   instructions=section.get("instructions", ""), refresh=auth.ensure_fresh,
                   exec_prefix=section.get("exec_prefix") or [])

    def __init__(self, accounts: dict, arbosctl="arbosctl", config_of=None, instructions="", runner=None,
                 refresh=None, exec_prefix=()):
        """accounts: {name: runs at once}. config_of(name) -> arbosctl config path; refresh(name) keeps its token
        fresh (axonry.agent.arbos.auth); both None in tests. exec_prefix: command prefix for arbosctl, e.g. a proxy
        wrapper where the host has no direct internet (arbosctl's chat WebSocket ignores HTTP proxy settings)."""
        self.accounts, self.bin, self.instructions = accounts, arbosctl, instructions
        self.config_of, self.refresh = config_of, refresh
        self.prefix = [os.path.expanduser(str(x)) for x in exec_prefix]
        self._run = runner or self._subprocess

    @property
    def capacity(self) -> int:
        return sum(self.accounts.values())

    def _subprocess(self, account, args, timeout):
        env = {**os.environ, **({"ARBOSCTL_CONFIG": str(self.config_of(account))} if self.config_of else {})}
        p = subprocess.run([*self.prefix, self.bin, *args], capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr

    def ctl(self, account, *args, timeout=300):
        if self.refresh:
            self.refresh(account)   # the relay's access token lives 1 h; arbosctl cannot refresh it itself
        code, out, err = self._run(account, list(args), timeout)
        if code != 0:
            raise ArbosError(f"arbosctl {' '.join(args[:2])} ({account}): {err.strip()[-400:]}")
        return out

    def keep_alive(self):
        """Refresh every account's token before it lapses, idle or not (a lapsed refresh token needs a new sign-in)."""
        for account in self.accounts if self.refresh else ():
            try:
                self.refresh(account)
            except Exception as e:
                log.warning("arbos account %s: token refresh failed: %s", account, e)

    def _json(self, account, *args, timeout=300):
        out = self.ctl(account, "--json", *args, timeout=timeout)
        i = min([j for j in (out.find("{"), out.find("[")) if j >= 0], default=-1)
        if i < 0:
            raise ArbosError(f"no JSON from arbosctl {args[:2]}")
        return json.loads(out[i:])

    def ensure_project(self, account, hotkey: str) -> str:
        name = project_name(hotkey)
        listed = self._json(account, "project", "list")
        items = listed if isinstance(listed, list) else (listed.get("projects") or listed.get("items") or [])
        for p in items:
            if p.get("name") == name:
                return p.get("id") or name
        created = self._json(account, "project", "create", "--name", name, "--instructions", self.instructions)
        return created.get("id") or created.get("project", {}).get("id") or name

    def pick(self, busy) -> str:
        """The account with the most room left, given the handles of the runs under way; an account whose box is held
        (e.g. at its spending cap) takes none."""
        load = {a: 0 for a in self.accounts}
        for h in busy:
            a = h.data.get("account")
            if a in load:
                load[a] += 1
        for account in sorted(self.accounts, key=lambda a: load[a] - self.accounts[a]):
            if load[account] >= self.accounts[account]:
                break
            if not self._held(account):
                return account
        raise ArbosError("no arbos account can take a run (at their run limits or held)")

    def _held(self, account) -> str | None:
        box = self._json(account, "machine", "status", timeout=120)
        if box.get("held") or (box.get("spend_cap_usd") and box.get("spend_today_usd", 0) >= box["spend_cap_usd"]):
            return (f"arbos account {account}: box held (spent {box.get('spend_today_usd')} of "
                    f"{box.get('spend_cap_usd')} USD today)")
        return None

    def prepare(self, sid, hotkey, busy=()) -> RunHandle:
        account = self.pick(busy)
        project = self.ensure_project(account, hotkey)
        opened = self._json(account, "chat", "open", project, "--title", f"submission {sid}")
        chat = opened.get("id") or opened.get("chat", {}).get("id")
        if not chat:
            raise ArbosError(f"chat open returned no id: {opened}")
        time.sleep(3)  # arbos' own driver: let the box join a brand-new room before the first send
        return RunHandle(sid, project, chat, {"account": account})

    def kickoff(self, h, message, bundle: Path):
        self.ctl(h.data["account"], "chat", "send", h.workspace, h.chat, BACKGROUND + message, "--attach", str(bundle),
                 "--timeout", "120", timeout=300)
        h.data["kickoff_at"] = time.time()

    def dump(self, h) -> dict:
        try:
            return self._json(h.data["account"], "chat", "dump", h.workspace, h.chat, timeout=180)
        except (ArbosError, json.JSONDecodeError):
            return {"messages": []}

    def poll(self, h) -> RunStatus:
        """Raises when arbos cannot be read: an unreadable chat is not an idle agent (the supervisor retries).
        error: the account's box is held (e.g. its spending cap), so no agent of it can work."""
        account = h.data["account"]
        doc = self._json(account, "chat", "dump", h.workspace, h.chat, timeout=180)
        msgs = doc.get("messages", [])
        last = max((_ts(m) for m in msgs), default=0.0) or h.data.get("kickoff_at", 0.0)
        return RunStatus(busy(doc), last, pending_approvals(doc), self._held(account), self.cost(h))

    def cost(self, h) -> float | None:
        """USD spent by the run's agent session (its chat) and the workers it started; None while the relay does not
        report session usage (arbos PR 3024)."""
        try:
            out = self._json(h.data["account"], "usage", h.workspace, "--session", h.chat, "--include-children",
                             timeout=120)
        except (ArbosError, ValueError):
            return None
        rows = out if isinstance(out, list) else out.get("sessions") or [out]
        usd = (rows[0] if rows else {}).get("usd")      # reported cost plus estimates: what the cap counts
        return float(usd) if isinstance(usd, (int, float)) else None

    def steer(self, h, message):
        self.ctl(h.data["account"], "chat", "send", h.workspace, h.chat, BACKGROUND + message, "--timeout", "120",
                 timeout=300)

    def reject(self, h, approval_id, reason):
        self.ctl(h.data["account"], "chat", "reject", h.workspace, h.chat, approval_id, "--reason", reason)

    def stop(self, h):
        try:
            self.ctl(h.data["account"], "chat", "stop", h.workspace, h.chat, timeout=120)
        except ArbosError:
            pass  # nothing running

    def export(self, h, dest: Path) -> bool:
        """Full chat record + the workspace audit. True only if the chat record could be exported."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        doc = self.dump(h)
        if not doc.get("messages"):
            return False
        (dest / "chat.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False))
        try:
            audit = self._json(h.data["account"], "audit", h.workspace, "--limit", "100000", timeout=300)
            (dest / "audit.json").write_text(json.dumps(audit, indent=1, ensure_ascii=False))
        except (ArbosError, json.JSONDecodeError):
            pass
        return True
