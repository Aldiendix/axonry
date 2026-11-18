"""Work on the owner-only evaluation machine (an owned host's or a rented one: pool/): admission build and
benchmark runs with the competition repo and the verified base. No miner code runs there: a submission is data (an
edit spec), applied by our admission code.

Machine layout (pool/setup.sh, root /srv/axonry): models/<org>__<name>@<revision>/ (verified),
repos/WeightSpaceProgramming/ (the pinned commit), wsp_work/ (WSP_WORK: wsp's benchmark data cache, filled on first use;
its norm cache), eval/<model_id>/ (spec, built model, outputs; base -> the verified base). Jobs run in eval/<model_id>/.
"""
import json
import shlex
import time
from pathlib import Path

from ..pool import machine as machines


class ExecError(RuntimeError):
    """Our side: the host is unreachable, a transfer failed, the frozen data does not match. Never the submission's
    fault: retried with backoff, never counted against a submission."""


class JobFailed(ExecError):
    """A job ran and exited non-zero (or never finished). When it is a candidate's own job, the submission may be
    the cause, so it is counted."""


class SshExecutor:
    def __init__(self, machine, ssh_key: str | None = None, gpus: int = 8, concurrency_per_gpu: int = 256):
        """machine: () -> pool.machine.Machine of the evaluation machine (raises ExecError while there is none);
        every evaluation uses all its GPUs (one vLLM server, data parallel)."""
        self.machine, self.key, self.root, self.gpus = machine, ssh_key, machines.ROOT, gpus
        self.concurrency = concurrency_per_gpu * gpus

    def ready(self) -> bool:
        try:
            self.machine()
            return True
        except ExecError:
            return False

    def _ssh(self, remote_cmd: str, timeout=600, binary=False):
        try:
            return machines.ssh(self.machine(), self.key, remote_cmd, timeout=timeout, binary=binary)
        except machines.MachineError as e:
            raise ExecError(f"eval host: {e}") from e

    def fetch_file(self, path: str, attempts=3) -> bytes:
        """A file from the evaluation host, verified: sha256 computed there, gzip transfer, checked here (a long
        transfer through a proxy can be cut silently)."""
        import gzip  # noqa: PLC0415
        import hashlib  # noqa: PLC0415
        want = self._ssh(f"sha256sum {shlex.quote(path)}").split()[0]
        for _ in range(attempts):
            try:
                data = gzip.decompress(self._ssh(f"gzip -c {shlex.quote(path)}", timeout=3600, binary=True))
            except (OSError, EOFError, ExecError):
                continue
            if hashlib.sha256(data).hexdigest() == want:
                return data
        raise ExecError(f"could not fetch {path} intact")

    def _run(self, model_id: str, base_dirname: str, argv: list[str], gpus: bool, timeout=24 * 3600) -> str:
        r = self.root
        env = {"PYTHONPATH": f"{r}/repos/WeightSpaceProgramming", "WSP_MODELS_DIR": f"{r}/models",
               "NLTK_DATA": "/usr/share/nltk_data", "WSP_WORK": f"{r}/wsp_work",
               # vLLM's compile cache is kept: it depends on the architecture, not on the weights
               "VLLM_CACHE_ROOT": f"{r}/wsp_work/vllm_cache"}
        ln = f"ln -sfn {r}/models/{base_dirname} {r}/eval/{model_id}/base"
        return self._job(model_id, ["env", *(f"{k}={v}" for k, v in env.items()), *argv], gpus, timeout, setup=ln)

    def _job(self, model_id: str, cmd: list, gpus: bool, timeout: int, poll: int = 20, setup: str = "true") -> str:
        """Run a command on the evaluation host detached from this ssh connection (a long generation must survive
        network hiccups) and return its stdout. GPU jobs hold the host-side lock of the evaluation GPUs (flock, also
        across orchestrator restarts). The job writes out / err / exit files; we poll with short connections and
        fetch the output verified."""
        q = shlex.quote
        job = f"{self.root}/eval/{model_id}/jobs/{time.time_ns()}"
        run = " ".join(map(q, cmd))
        if gpus:
            run = f"flock {q(self.root + '/eval/gpus.lock')} {run}"
        body = (f"cd {q(self.root + '/eval/' + model_id)} && {setup} && {run} > {q(job)}/out 2> {q(job)}/err; "
                f"echo $? > {q(job)}/exit")
        # two statements: only the fully redirected nohup goes to the background, so ssh returns at once
        self._ssh(f"mkdir -p {q(job)}; nohup sh -c {q(body)} > /dev/null 2>&1 < /dev/null &", timeout=120)
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(poll)
            try:
                code = self._ssh(f"cat {q(job)}/exit 2>/dev/null || true", timeout=60).strip()
            except ExecError:
                continue          # the host is unreachable for a moment; the job keeps running
            if code:
                break
        else:
            raise JobFailed(f"job {job} did not finish within {timeout}s")
        if code == "3":             # eval_remote.ITEMS_MISMATCH: our frozen data, never the model's fault
            raise ExecError(f"job {job}: {self._ssh(f'tail -c 800 {q(job)}/err')}")
        if code != "0":
            raise JobFailed(f"job {job} exited {code}: {self._ssh(f'tail -c 800 {q(job)}/err')}")
        return self.fetch_file(f"{job}/out").decode()

    def push_dir(self, local: Path, model_id: str, sub: str):
        dst = f"{self.root}/eval/{model_id}/{sub}"
        self._ssh(f"rm -rf {shlex.quote(dst)} && mkdir -p {shlex.quote(dst)}")
        try:
            machines.scp(self.machine(), self.key, sorted(Path(local).iterdir()), f"{dst}/", timeout=600)
        except machines.MachineError as e:
            raise ExecError(f"eval host: {e}") from e


    def admit(self, sid: str, spec_dir: Path, base_doc: dict) -> dict:
        from ..registry import base_dirname  # noqa: PLC0415
        base_id = base_doc["id"]
        self.push_dir(spec_dir, sid, "edit")
        out = self._run(sid, base_dirname(base_doc), [   # CPU only: runs alongside an evaluation
            "python3", "-m", "axonry.evaluation.admit_remote", "--spec-dir", "edit",
            "--base-dir", "base", "--base-id", base_id, "--build-dir", "model"],
            gpus=False, timeout=3600)
        i = out.find("{")
        if i < 0:
            raise ExecError(f"admission produced no result: {out[-500:]}")
        return json.loads(out[i:])

    def evaluate(self, model_id: str, base_doc: dict, bench_docs: list, salt: str) -> dict:
        """One model on the given benchmarks, all evaluation GPUs, one job (axonry.evaluation.eval_remote).
        model_id "base:<id>" runs the verified base itself; otherwise the built model of that submission.
        -> {bench key: output JSON lines}. Partial output survives a failure and is resumed by the next call."""
        from ..registry import base_dirname  # noqa: PLC0415
        model_dir = "base" if model_id.startswith("base:") else "model"
        safe = model_id.replace(":", "_")
        spec = [{"key": f"{b['id']}@{b['version']}", "task": b["task"], "n": b["samples_per_item"],
                 "limit": b.get("limit"), "manifest": b["items_manifest_sha256"]} for b in bench_docs]
        self._run(safe, base_dirname(base_doc), [
            "python3", "-m", "axonry.evaluation.eval_remote", "--model", model_dir, "--profile", base_doc["id"],
            "--salt", salt, "--out", f"out/{salt}", "--gpus", str(self.gpus),
            "--concurrency", str(self.concurrency), "--benchmarks", json.dumps(spec)], gpus=True)
        return {b["key"]: self.fetch_file(f"{self.root}/eval/{safe}/out/{salt}/{b['key']}.jsonl")
                for b in spec}
