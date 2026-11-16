"""The season's benchmark items for evaluation hosts that cannot load them from Hugging Face as it serves them. Loaded
through a mirror, `datasets` types some columns differently (it cannot read the Hub's metadata there), which makes
other items: IFEval's no longer match its frozen manifest. So the orchestrator, which reaches Hugging Face, loads them
with the competition toolkit, checks them against the frozen manifests, and packs them as the toolkit caches them
($WSP_WORK/data/bench_*.jsonl). Only an evaluation host receives the pack; a run's machine never does.

  python3 -m axonry.evaluation.benchdata BENCHMARKS.json     on the evaluation host: the items found match the
                                                             manifests (offline), exit 1 otherwise
"""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def spec(bench_docs) -> list:
    return [{"key": f"{b['id']}@{b['version']}", "task": b["task"], "manifest": b["items_manifest_sha256"]}
            for b in bench_docs]


def check(benchmarks) -> list:
    """-> the benchmarks whose items (loaded by the toolkit, in this process) differ from their manifests."""
    from wsp import data  # noqa: PLC0415
    bad = []
    for b in benchmarks:
        got = data.manifest(b["task"])["sha256"]
        if got != b["manifest"]:
            bad.append(f"{b['key']}: items {got[:12]} != frozen {b['manifest'][:12]}")
    return bad


def build(competition: Path, bench_docs, dest_dir: Path) -> Path:
    """-> benchdata-<sha>.tar (data/bench_*.jsonl), built once per set of benchmarks."""
    s = spec(bench_docs)
    out = Path(dest_dir) / f"benchdata-{hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest()[:12]}.tar"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        r = subprocess.run([sys.executable, "-m", "axonry.evaluation.benchdata", "-"], input=json.dumps(s),
                           capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(Path(competition).resolve()), "WSP_WORK": work})
        if r.returncode != 0:
            raise RuntimeError(f"benchmark items: {(r.stdout + r.stderr).strip()[-600:]}")
        tmp = out.with_suffix(".tmp")
        with tarfile.open(tmp, "w") as tf:
            tf.add(Path(work) / "data", arcname="data")
        tmp.replace(out)
    return out


def main(argv=None):
    arg = (argv or sys.argv[1:])[0]
    bad = check(json.loads(sys.stdin.read() if arg == "-" else Path(arg).read_text()))
    if bad:
        print("; ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
