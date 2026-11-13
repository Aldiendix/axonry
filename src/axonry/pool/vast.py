"""GPU machines rented on vast.ai: one machine per run (its sandbox) and one for evaluation, rented when
needed and destroyed when done.

A machine is a plain vast instance (an unprivileged container: no docker inside, no firewall): the instance itself is
the run's sandbox. The agent logs in as the unprivileged user `agent`; root (the orchestrator's key) keeps the
verified base, the competition repo and the tools read-only for it. Instances of different runs are separate vast
containers (often the two halves of one 8-GPU host), so runs never share GPUs, memory, files or processes.

Lifecycle (table machines, one row per purpose: a submission id, or "eval"):
  booting     rented; waiting for the instance to run and its sshd to answer
  setting_up  setup bundle copied, setup.sh running (tools, competition repo, verified base, the agent user)
  ready       in use
  gone        destroyed (released, failed or vanished); a vast machine whose setup failed is not rented again
Setup time is ours: a run's clock starts when its machine is ready.
"""
import io
import json
import logging
import os
import shlex
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("axonry.vast")
API = "https://console.vast.ai/api/v0"
ROOT = "/srv/axonry"          # the machine's layout (setup.sh)
EVAL = "eval"                 # the evaluation machine's purpose; a run machine's is its submission id
KEY_GRACE = 15 * 60           # a running instance that still refuses our key after this never got it: a broken host
HERE = Path(__file__).parent


class VastError(RuntimeError):
    pass


class SetupFailed(VastError):
    """The machine will not become ready (setup.sh failed, or not ready in time): it is destroyed."""


class VastAPI:
    def __init__(self, api_key: str, base=API, timeout=60):
        self.key, self.base, self.timeout = api_key, base, timeout

    def call(self, method, path, body=None, query=None):
        url = self.base + path
        if query:
            url += "?" + "&".join(f"{k}={urllib.request.quote(json.dumps(v) if not isinstance(v, str) else v)}"
                                  for k, v in query.items())
        req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise VastError(f"{method} {path}: {e.code} {e.read()[:300]!r}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise VastError(f"{method} {path}: {e}") from e

    def offers(self, gpu_name: str, gpus: int, min_reliability: float, disk_gb: int, min_down_mbps: int,
               exclude_countries=(), verified_only=False, min_cuda=0.0) -> list:
        """Rentable on-demand offers of exactly `gpus` GPUs of that model, cheapest first (only hosts vast has
        verified if verified_only). A slow link cannot pull the image in time, so offers below min_down_mbps are left
        out; so are hosts in countries whose route to PyPI, Hugging Face and R2 is unreliable (exclude_countries, ISO
        codes), and hosts whose driver cannot run the image's CUDA natively (min_cuda: consumer GPUs have no forward
        compatibility). A host that turns out bad fails its setup and is never rented again."""
        q = {"gpu_name": {"eq": gpu_name}, "num_gpus": {"eq": gpus}, "rentable": {"eq": True}, "rented": {"eq": False},
             **({"verified": {"eq": True}} if verified_only else {}),
             "external": {"eq": False}, "reliability2": {"gte": min_reliability},
             **({"cuda_max_good": {"gte": min_cuda}} if min_cuda else {}),
             "inet_down": {"gte": min_down_mbps},
             **({"geolocation": {"notin": list(exclude_countries)}} if exclude_countries else {}),
             "direct_port_count": {"gte": 1}, "disk_space": {"gte": disk_gb}, "type": "on-demand",
             "order": [["dph_total", "asc"]], "limit": 100, "allocated_storage": disk_gb}
        return self.call("POST", "/bundles/", q)["offers"]

    def create(self, offer_id, image: str, disk_gb: int, label: str) -> str:
        r = self.call("PUT", f"/asks/{offer_id}/", {"client_id": "me", "image": image, "disk": disk_gb, "label": label,
                                                    "runtype": "ssh_direc ssh_proxy", "env": {}})
        if not r.get("success"):
            raise VastError(f"create on offer {offer_id}: {r}")
        return str(r["new_contract"])

    def show(self, iid) -> dict | None:
        return self.call("GET", f"/instances/{iid}/", query={"owner": "me"}).get("instances")

    def destroy(self, iid):
        self.call("DELETE", f"/instances/{iid}/", {})

    def ensure_ssh_key(self, public_key: str):
        """The orchestrator's key on the account: vast installs the account's keys for root on every new instance."""
        listed = self.call("GET", "/ssh/")
        rows = listed if isinstance(listed, list) else listed.get("keys") or listed.get("ssh_keys") or []
        if not any((k.get("public_key") or k.get("ssh_key") or "").split()[:2] == public_key.split()[:2] for k in rows):
            self.call("POST", "/ssh/", {"ssh_key": public_key})


@dataclass
class Machine:
    """ssh access to a ready machine, as root (the orchestrator) or as the agent."""
    name: str
    ip: str
    port: int
    known_hosts: str
    proxy_command: str | None = None
    user: str = "root"

    @property
    def ssh(self):
        return f"{self.user}@{self.ip}"

    def ssh_args(self, key=None):
        args = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=30", "-p", str(self.port),
                "-o", f"UserKnownHostsFile={self.known_hosts}", "-o", "StrictHostKeyChecking=accept-new"]
        if self.proxy_command:
            args += ["-o", f"ProxyCommand={self.proxy_command}"]
        return args + (["-i", key] if key else [])

    def scp_args(self, key=None):
        return ["-P" if a == "-p" else a for a in self.ssh_args(key)]

    def host_key(self) -> str:
        """The machine's sshd key as first seen (the agent's known_hosts pins it)."""
        for line in Path(self.known_hosts).read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "ssh-ed25519":
                return f"{parts[1]} {parts[2]}"
        raise VastError(f"{self.name}: no ed25519 host key recorded")


def ssh(m: Machine, key, command: str, timeout=900, attempts=3, binary=False):
    """Run a command on the machine -> its stdout (bytes with binary). ssh's own failure to connect (exit 255: a
    dropped or refused connection) is retried; every command we run is safe to repeat. A failing command is not."""
    import subprocess  # noqa: PLC0415
    for i in range(attempts):
        try:
            r = subprocess.run(["ssh", *m.ssh_args(key), m.ssh, command], capture_output=True, text=not binary,
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise VastError(f"{m.name}: `{command[:120]}` timed out") from e
        if r.returncode != 255 or i == attempts - 1:
            break
        time.sleep(5 * (i + 1))
    if r.returncode != 0:
        err = r.stderr.decode(errors="replace") if binary else r.stderr
        raise VastError(f"{m.name}: `{command[:120]}` failed: {err.strip()[-500:]}")
    return r.stdout


def scp(m: Machine, key, srcs, dst: str, timeout=1800, attempts=3):
    """Copy local files or directories (one path or a list) to dst on the machine; retried, it is repeatable."""
    import subprocess  # noqa: PLC0415
    srcs = [str(x) for x in (srcs if isinstance(srcs, (list, tuple)) else [srcs])]
    for i in range(attempts):
        try:
            r = subprocess.run(["scp", "-q", "-r", *m.scp_args(key), *srcs, f"{m.ssh}:{dst}"], capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise VastError(f"{m.name}: scp timed out") from e
        if r.returncode == 0:
            return
        time.sleep(5 * (i + 1))
    raise VastError(f"{m.name}: scp failed: {r.stderr.strip()[-500:]}")


def keypair(comment: str):
    """-> (private key, public key line): ed25519, no passphrase, written only to a temporary directory."""
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "k"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(p)], check=True)
        return p.read_text(), p.with_suffix(".pub").read_text().strip()


def setup_bundle(project: Path, competition: Path, base_docs: list, dest: Path) -> Path:
    """What setup.sh installs: our package (as the working tree has it), the competition repo at its checked-out
    commit (the orchestrator refuses to start unless that is the season's pin), and the season's base definitions."""
    import subprocess  # noqa: PLC0415
    from ..registry import base_dirname  # noqa: PLC0415
    dest.parent.mkdir(parents=True, exist_ok=True)
    wsp = subprocess.run(["git", "-C", str(competition), "archive", "--format=tar", "HEAD"], capture_output=True,
                         check=True).stdout
    with tarfile.open(dest, "w:gz") as tf:
        def add(name, data, mode=0o644):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), mode
            tf.addfile(ti, io.BytesIO(data))
        for f in ("setup.sh", "stage_base.py"):
            add(f, (HERE / f).read_bytes(), 0o755)
        pkg = io.BytesIO()
        with tarfile.open(fileobj=pkg, mode="w:gz") as p:
            for rel in ("pyproject.toml", "README.md", "src"):
                p.add(project / rel, arcname=f"axonry/{rel}",
                      filter=lambda ti: None if "__pycache__" in ti.name else ti)
        add("axonry.tar.gz", pkg.getvalue())
        add("wsp.tar", wsp)
        for doc in base_docs:
            add(f"bases/{base_dirname(doc)}.json", json.dumps(doc).encode())
    return dest


class Fleet:
    def __init__(self, api: VastAPI, db, *, image: str, disk_gb: int, ssh_key: str, bundle, state_dir: Path,
                 min_reliability=0.95, min_down_mbps=200, exclude_countries=("CN",), verified_only=False,
                 min_cuda=0.0, setup_minutes=45,
                 proxy_command=None, clock=time.time, remote=None):
        """bundle: () -> path of the setup bundle (built once). remote: (ssh, scp) for tests."""
        self.api, self.db, self.image, self.disk_gb, self.key = api, db, image, disk_gb, os.path.expanduser(ssh_key)
        self.bundle, self.dir, self.min_rel, self.min_down = bundle, Path(state_dir), min_reliability, min_down_mbps
        self.exclude, self.verified_only, self.min_cuda = tuple(exclude_countries), verified_only, min_cuda
        self.setup_s, self.proxy, self.clock = setup_minutes * 60, proxy_command, clock
        self.ssh, self.scp = remote or (ssh, scp)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._key_on_account = False

    # ---- queries
    def get(self, purpose) -> dict | None:
        return next((m for m in self.db.machines() if m["name"] == purpose), None)

    def active(self) -> list:
        return self.db.machines()

    def machine(self, row) -> Machine:
        return Machine(row["name"], row["ssh_host"], row["ssh_port"], str(self.dir / f"{row['provider_id']}.known_hosts"),
                       self.proxy)

    # ---- renting
    def rent(self, purpose: str, gpu_name: str, gpus: int):
        """The cheapest suitable offer (never a vast machine whose setup failed before)."""
        if not self._key_on_account:
            self.api.ensure_ssh_key(Path(self.key + ".pub").read_text().strip())
            self._key_on_account = True
        bad = {m["machine_id"] for m in self.db.machines(states=("gone",)) if m["note"] and m["note"].startswith("failed")}
        offers = [o for o in self.api.offers(gpu_name, gpus, self.min_rel, self.disk_gb, self.min_down, self.exclude,
                                                  self.verified_only, self.min_cuda)
                  if str(o["machine_id"]) not in bad]
        for o in offers[:5]:                    # an offer can be taken between the search and the order
            try:
                iid = self.api.create(o["id"], self.image, self.disk_gb, f"axonry {purpose}")
                break
            except VastError as e:
                if "no_such_ask" not in str(e) and "not available" not in str(e):
                    raise
        else:
            raise VastError(f"no {gpus} x {gpu_name} offer on vast.ai right now")
        self.db.add_machine(purpose, iid, o["machine_id"], gpu_name, gpus, o["dph_total"], self.clock())
        log.info("rented %s: instance %s, %d x %s on machine %s at $%.3f/h", purpose, iid, gpus, gpu_name,
                 o["machine_id"], o["dph_total"])

    def advance(self, purpose) -> str:
        """Move a machine along booting -> setting_up -> ready. -> its state, or "failed" (then it is gone).
        A failed ssh/scp or API call is retried on the next call (connections drop); only setup.sh's own failure or
        the setup deadline give the machine up."""
        row = self.get(purpose)
        if row is None:
            return "gone"
        if row["state"] == "ready":
            return "ready"
        try:
            if self.clock() - row["created_at"] > self.setup_s:
                raise SetupFailed(f"not ready within {self.setup_s // 60} min")
            if row["state"] == "booting":
                inst = self.api.show(row["provider_id"]) or {}
                port = ((inst.get("ports") or {}).get("22/tcp") or [{}])[0].get("HostPort")
                if inst.get("actual_status") != "running" or not port:
                    return "booting"
                m = self._reachable(purpose, [(inst["public_ipaddr"].strip(), int(port)),
                                              (inst.get("ssh_host"), inst.get("ssh_port"))])
                self.scp(m, self.key, self.bundle(), "/root/axonry-setup.tgz")
                self.ssh(m, self.key, "rm -rf /root/axonry-setup && mkdir /root/axonry-setup && "
                                      "tar xzf /root/axonry-setup.tgz -C /root/axonry-setup && "
                                      f"(setsid nohup /root/axonry-setup/setup.sh {row['gpus']} "
                                      "> /root/axonry-setup.log 2>&1 < /dev/null &)", timeout=300)
                self.db.set_machine(purpose, state="setting_up")
                return "setting_up"
            m = self.machine(row)
            st = self.ssh(m, self.key, f"cat {ROOT}/READY 2>/dev/null || (test -f {ROOT}/FAILED && "
                                       "echo FAILED && tail -c 600 /root/axonry-setup.log) || true", timeout=45,
                          attempts=1).strip()
            if st.startswith("FAILED"):
                raise SetupFailed(f"setup failed: {st[6:]}")
            if st:
                self.db.set_machine(purpose, state="ready", ready_at=self.clock())
                log.info("machine %s ready (instance %s)", purpose, row["provider_id"])
                return "ready"
            return "setting_up"
        except SetupFailed as e:
            log.warning("machine %s (instance %s): %s", purpose, row["provider_id"], e)
            self.release(purpose, f"failed: {e}"[:500])
            return "failed"
        except VastError as e:                 # sshd not up yet, a dropped connection, the API hiccuping
            if "Permission denied (publickey)" in str(e) and self.clock() - row["created_at"] > KEY_GRACE:
                log.warning("machine %s (instance %s): the host never installed our key", purpose, row["provider_id"])
                self.release(purpose, "failed: the host never installed the account's ssh key")
                return "failed"
            log.info("machine %s (instance %s): %s (retrying)", purpose, row["provider_id"], str(e)[:200])
            return self.get(purpose)["state"]

    def _reachable(self, purpose, endpoints) -> Machine:
        """The first endpoint whose sshd answers: the host's direct port, else vast's ssh relay (some hosts' port
        forwarding is broken). Probes try once; the next tick retries."""
        err = None
        for host, port in endpoints:
            if not host or not port:
                continue
            self.db.set_machine(purpose, ssh_host=host, ssh_port=int(port))
            m = self.machine(self.get(purpose))
            try:
                self.ssh(m, self.key, "true", timeout=45, attempts=1)
                return m
            except VastError as e:
                if "Permission denied (publickey)" in str(e):
                    raise                     # sshd answered: the key is the problem, not the route
                err = e
        raise err or VastError("no ssh endpoint yet")

    def release(self, purpose, note="released"):
        row = self.get(purpose)
        if row is None:
            return
        try:
            self.api.destroy(row["provider_id"])
        except VastError as e:
            if "404" not in str(e):
                raise                          # kept active: retried by the next release
        self.db.set_machine(purpose, state="gone", ended_at=self.clock(), note=note)
        Path(self.dir / f"{row['provider_id']}.known_hosts").unlink(missing_ok=True)
        log.info("machine %s released (instance %s): %s", purpose, row["provider_id"], note)

    def hand_over(self, purpose, public_key: str, deadline: int) -> Machine:
        """A ready run machine goes to this run's agent: its login key, its deadline (axonry-time). -> the machine
        as the agent reaches it."""
        m = self.machine(self.get(purpose))
        q = shlex.quote
        self.ssh(m, self.key, f"install -d -m 700 -o agent -g agent ~agent/.ssh && echo {q(public_key)} > "
                              "~agent/.ssh/authorized_keys && chown agent:agent ~agent/.ssh/authorized_keys && "
                              f"chmod 600 ~agent/.ssh/authorized_keys && echo {int(deadline)} > /etc/axonry-deadline",
                 timeout=120)
        return Machine(m.name, m.ip, m.port, m.known_hosts, None, "agent")

    def probe(self, purpose) -> dict:
        """What the run's agent gets, as seen on the machine (BRIEF.md lists it)."""
        m = self.machine(self.get(purpose))
        out = self.ssh(m, self.key, "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits; "
                                    "echo CPUS $(nproc); echo MEM $(free -g | awk '/^Mem/{print $2}'); "
                                    "echo DISK $(df -BG --output=avail /workspace/work | tail -1 | tr -dc 0-9)",
                       timeout=120)
        kv = {line.split()[0]: line.split()[1] for line in out.splitlines() if line.split()[:1] in (["CPUS"], ["MEM"],
                                                                                                   ["DISK"])}
        return {"gpus": [{"name": n.strip(), "memory_gib": round(int(mem) / 1024)} for n, mem in
                         (line.split(",") for line in out.splitlines() if "," in line)],
                "cpus": int(kv["CPUS"]), "memory": f"{kv['MEM']}g", "disk_gib": int(kv["DISK"]),
                "network": "open (no allowlist)", "base_path": "/workspace/base", "work_path": "/workspace/work",
                "competition_repo": "/workspace/WeightSpaceProgramming"}

    def vanished(self, purpose) -> bool:
        """A ready machine whose instance vast no longer runs (host failure, outbid, deleted)."""
        row = self.get(purpose)
        if row is None or row["state"] != "ready":
            return False
        try:
            inst = self.api.show(row["provider_id"])
        except VastError:
            return False                       # the API is down, not the machine
        return inst is None or inst.get("actual_status") in ("exited", "offline", "stopped", "unknown") \
            or inst.get("intended_status") == "stopped"

