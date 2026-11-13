"""Run supervision against a fake agent backend, and the arbos chat-state reading (shapes seen in real runs)."""
import json

from axonry.agent import supervisor
from axonry.agent.arbos import ArbosError, ArbosRunner, busy
from axonry.agent.base import RunHandle, RunStatus

T0 = 1_800_000_000
TASK = {"sid": "s1", "run": {"start": T0, "deadline": T0 + 8 * 3600, "wrap_minutes": 30, "stall_minutes": 20,
                             "max_agent_usd": 10}}


class Backend:
    def __init__(self):
        self.status, self.sent, self.stopped = RunStatus(True, T0), [], False

    def poll(self, h):
        return self.status

    def steer(self, h, message):
        self.sent.append(message)

    def stop(self, h):
        self.stopped = True

    def reject(self, h, a, reason):
        pass


def row(status, job=None, role="assistant", ts=0):
    return {"role": role, "status": status, "createdAt": ts, **({"job": {"id": job}} if job else {})}


def test_a_job_that_hit_the_60_minute_cap_is_not_work():
    """Seen in both trials: the job's row ends in error, then a streaming row of the same job lingers."""
    doc = {"messages": [row("done", role="user"), row("error", job="J1"), row("done"), row("streaming", job="J1")]}
    assert not busy(doc)
    doc["messages"].append(row("streaming", job="J2"))                # another job really is working
    assert busy(doc)


def test_the_agent_cost_cap():
    b, st = Backend(), {}
    b.status = RunStatus(True, T0, usd=7.9)
    assert supervisor.step(T0 + 600, TASK, None, b, st, False) == "running" and b.sent == []
    b.status = RunStatus(True, T0 + 600, usd=8.0)                      # 80%: one reminder to upload
    assert supervisor.step(T0 + 660, TASK, None, b, st, False) == "running" and "upload" in b.sent[0]
    assert supervisor.step(T0 + 720, TASK, None, b, st, False) == "running" and len(b.sent) == 1
    b.status = RunStatus(True, T0 + 700, usd=10.0)                     # 100%: stopped, collected like a deadline
    assert supervisor.step(T0 + 780, TASK, None, b, st, False) == "budget" and b.stopped and st["usd"] == 10.0


def test_no_cost_report_means_no_cap():
    b, st = Backend(), {}
    b.status = RunStatus(True, T0, usd=None)                          # the relay does not report session usage yet
    assert supervisor.step(T0 + 600, TASK, None, b, st, False) == "running" and "usd" not in st


def test_a_backend_that_cannot_run_the_agent_loses_the_run():
    b, st = Backend(), {}
    b.status = RunStatus(False, T0, error="arbos account main: box held")
    assert supervisor.step(T0 + 60, TASK, None, b, st, False) == "running" and b.sent == []
    b.status = RunStatus(True, T0)                                     # it recovered: the clock restarts
    assert supervisor.step(T0 + 120, TASK, None, b, st, False) == "running" and "error_since" not in st
    b.status = RunStatus(False, T0, error="held")
    supervisor.step(T0 + 180, TASK, None, b, st, False)
    assert supervisor.step(T0 + 180 + 20 * 60, TASK, None, b, st, False) == "lost" and b.stopped


def test_a_held_arbos_account_takes_no_run():
    def run(account, args, timeout):
        if args[1:3] == ["machine", "status"]:
            return 0, json.dumps({"held": account == "a", "spend_today_usd": 1, "spend_cap_usd": 20}), ""
        return 0, json.dumps([]), ""
    r = ArbosRunner({"a": 3, "b": 1}, runner=run)
    assert r.pick([]) == "b"                                           # a has more room but is held
    try:
        r.pick([RunHandle("x", "p", "c", {"account": "b"})])
        raise AssertionError("expected no room")
    except ArbosError:
        pass
