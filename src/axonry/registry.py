"""Registry of definitions: bases, benchmarks, rules versions and initial champions.

Source files live in the repo under registry/ and are published unchanged to the public bucket:
  registry/bases/<base_id>.json                -> bases/<base_id>.json
  registry/benchmarks/<id>@<version>.json      -> benchmarks/<id>@<version>.json
  registry/seasons/<season>/<name>.json        -> seasons/<season>/rules/<sha256>.json  (+ rules.json = current)
  registry/initial/<id>.json + <id>/           a season's initial champion (rules "initial_champion"): an edit of a base
                                               (edit.json, edit.npz, REPORT.md) pinned by sha256; published as the
                                               season's first submission (orchestrator/service.py, _seed_initial)
A rules version takes effect at `effective_block`; a submission keeps the version in force when it entered the queue. Nothing else in the code names a concrete base or benchmark.
"""
from dataclasses import dataclass
from pathlib import Path

from .common.canon import canonical, read_json, sha256_file, sha256_hex

ROOT = Path(__file__).resolve().parents[2] / "registry"

BASE_FIELDS = {"schema", "id", "hf_repo", "revision", "files"}
INITIAL_FIELDS = {"schema", "id", "base", "files", "about"}
BENCH_FIELDS = {"schema", "id", "version", "adapter", "task", "samples_per_item", "items_manifest_sha256", "judge"}
RULES_FIELDS = {"schema", "season", "effective_block", "netuid", "base", "benchmarks", "judge", "fee", "run",
                "competition", "retention", "review"}


class RegistryError(ValueError):
    pass


def _need(doc, fields, what):
    missing = fields - set(doc)
    if missing:
        raise RegistryError(f"{what}: missing {sorted(missing)}")


@dataclass(frozen=True)
class Benchmark:
    id: str
    version: int
    doc: dict

    @property
    def key(self):
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class Rules:
    doc: dict

    @property
    def sha256(self):
        return sha256_hex(canonical(self.doc))

    @property
    def season(self):
        return self.doc["season"]

    def benchmark_keys(self):
        return [b["key"] for b in self.doc["benchmarks"]]

    def judge_params(self, bench_key):
        """Per-benchmark thresholds: rules override > benchmark default."""
        entry = next(b for b in self.doc["benchmarks"] if b["key"] == bench_key)
        return {**load_benchmark(bench_key).doc["judge"], **entry.get("judge", {})}

    def __getitem__(self, k):
        return self.doc[k]


def load_base(base_id, root=ROOT):
    p = Path(root) / "bases" / f"{base_id}.json"
    if not p.exists():
        raise RegistryError(f"unknown base {base_id}")
    doc = read_json(p)
    _need(doc, BASE_FIELDS, f"base {base_id}")
    if doc["id"] != base_id:
        raise RegistryError(f"base file {p.name} has id {doc['id']}")
    return doc


def load_initial(initial_id, root=ROOT) -> tuple[dict, Path]:
    """-> (definition, directory of its edit files), every file checked against its pinned sha256."""
    p = Path(root) / "initial" / f"{initial_id}.json"
    if not p.exists():
        raise RegistryError(f"unknown initial champion {initial_id}")
    doc = read_json(p)
    _need(doc, INITIAL_FIELDS, f"initial champion {initial_id}")
    d = p.with_suffix("")
    if doc["id"] != initial_id or sorted(f.name for f in d.iterdir()) != sorted(doc["files"]):
        raise RegistryError(f"initial champion {initial_id}: id or files do not match its definition")
    for name, sha in doc["files"].items():
        if sha256_file(d / name) != sha:
            raise RegistryError(f"initial champion {initial_id}: {name} does not match its sha256")
    return doc, d


def base_dirname(doc) -> str:
    """Directory name of a pinned base, as wsp expects under WSP_MODELS_DIR: <org>__<name>@<revision>."""
    return f"{doc['hf_repo'].replace('/', '__')}@{doc['revision']}"


def load_benchmark(key, root=ROOT):
    p = Path(root) / "benchmarks" / f"{key}.json"
    if not p.exists():
        raise RegistryError(f"unknown benchmark {key}")
    doc = read_json(p)
    _need(doc, BENCH_FIELDS, f"benchmark {key}")
    if f"{doc['id']}@{doc['version']}" != key:
        raise RegistryError(f"benchmark file {p.name} declares {doc['id']}@{doc['version']}")
    return Benchmark(doc["id"], doc["version"], doc)


def latest_rules(root=ROOT) -> "Rules":
    """The newest rules version of the newest season."""
    return load_rules_versions(seasons(root)[-1], root)[-1]


def load_rules_versions(season, root=ROOT):
    d = Path(root) / "seasons" / season
    out = []
    for p in sorted(d.glob("*.json")):
        doc = read_json(p)
        _need(doc, RULES_FIELDS, f"rules {p}")
        if doc["season"] != season:
            raise RegistryError(f"{p} declares season {doc['season']}")
        out.append(Rules(doc))
    if not out:
        raise RegistryError(f"no rules for season {season}")
    blocks = [r["effective_block"] for r in out]
    if len(set(blocks)) != len(blocks):
        raise RegistryError(f"season {season}: two rules versions share an effective_block")
    return sorted(out, key=lambda r: r["effective_block"])


def seasons(root=ROOT):
    """Season names in the order they start (first effective_block)."""
    names = [p.name for p in (Path(root) / "seasons").iterdir() if p.is_dir()]
    return sorted(names, key=lambda s: load_rules_versions(s, root)[0]["effective_block"])


def rules_at(block, root=ROOT):
    """The rules version in force at `block` across all seasons (latest effective_block <= block)."""
    best = None
    for s in seasons(root):
        for r in load_rules_versions(s, root):
            if r["effective_block"] <= block and (best is None or r["effective_block"] > best["effective_block"]):
                best = r
    if best is None:
        raise RegistryError(f"no rules in force at block {block}")
    return best


def validate(root=ROOT):
    """Cross-check every rules version: bases and benchmarks exist, thresholds complete. Returns a list of errors."""
    errs = []
    for s in seasons(root):
        try:
            versions = load_rules_versions(s, root)
        except RegistryError as e:
            errs.append(str(e))
            continue
        for r in versions:
            try:
                load_base(r["base"], root)
            except RegistryError as e:
                errs.append(f"{s}@{r['effective_block']}: {e}")
            for b in r["benchmarks"]:
                try:
                    bench = load_benchmark(b["key"], root)
                    params = {**bench.doc["judge"], **b.get("judge", {})}
                    for k in ("eps_tie", "delta_win"):
                        if not isinstance(params.get(k), (int, float)):
                            errs.append(f"{s}@{r['effective_block']}: {b['key']} missing judge.{k}")
                except RegistryError as e:
                    errs.append(f"{s}@{r['effective_block']}: {e}")
            if r.doc.get("initial_champion"):
                try:
                    if load_initial(r["initial_champion"], root)[0]["base"] != r["base"]:
                        errs.append(f"{s}@{r['effective_block']}: the initial champion is not an edit of {r['base']}")
                except RegistryError as e:
                    errs.append(f"{s}@{r['effective_block']}: {e}")
            if not (isinstance(r["fee"].get("usd_per_gpu_hour"), (int, float)) and r["fee"]["usd_per_gpu_hour"] > 0):
                errs.append(f"{s}@{r['effective_block']}: fee.usd_per_gpu_hour not set")
            run = r["run"]
            if not (isinstance(run.get("gpus"), int) and run["gpus"] > 0
                    and str(run.get("gpu_model", "")).replace(" ", "").isalnum() and run.get("hours", 0) > 0):
                errs.append(f"{s}@{r['effective_block']}: run needs gpus, gpu_model, hours")
            if not str(r["fee"].get("treasury", "")).startswith("5"):
                errs.append(f"{s}@{r['effective_block']}: fee.treasury not set")
            if not r["review"].get("reviewers"):
                errs.append(f"{s}@{r['effective_block']}: review.reviewers is empty")
            j = r["judge"]
            if not 1 <= j["n_win"] <= len(r["benchmarks"]):
                errs.append(f"{s}@{r['effective_block']}: n_win {j['n_win']} out of range")
    return errs
