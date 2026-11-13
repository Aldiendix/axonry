"""The single writer of the public bucket. Dependencies are written before what references them:
  edit/ -> admission.json -> results/ -> score.json -> submission.json
  -> seasons/<s>/matches.json -> leaderboard.json -> weights.json -> index.json
Immutable objects are write-once (store enforces); weights.json carries a monotonic sequence and the owner signature.
"""
import gzip
import json
import time
from pathlib import Path

from .. import registry
from ..common import layout
from ..common.canon import pretty
from ..common.sign import sign_doc, verify_doc

SCHEMAS = {"weights": "axonry.weights/1", "score": "axonry.score/1", "submission": "axonry.submission/1",
           "admission": "axonry.admission/1", "matches": "axonry.matches/1", "leaderboard": "axonry.leaderboard/1",
           "index": "axonry.index/1", "status": "axonry.status/1", "ruling": "axonry.ruling/1"}


class PublishError(RuntimeError):
    pass


def _json(store, key, doc):
    store.put(key, pretty(doc))


def publish_registry(store, root=registry.ROOT):
    """bases/, benchmarks/ and every rules version (content-addressed; index.json names the current one)."""
    root = Path(root)
    for p in sorted((root / "bases").glob("*.json")):
        store.put(layout.base_def(p.stem), p.read_bytes())
    for p in sorted((root / "benchmarks").glob("*.json")):
        bid, _, v = p.stem.partition("@")
        store.put(layout.benchmark_def(bid, int(v)), p.read_bytes())
    for season in registry.seasons(root):
        versions = registry.load_rules_versions(season, root)
        for r in versions:
            _json(store, layout.season_rules_version(season, r.sha256), r.doc)


def publish_dir(store, prefix: str, local: Path, content_type=None):
    """Upload every file under local to prefix (follows symlinks: built models link untouched base shards)."""
    n = 0
    for p in sorted(Path(local).rglob("*")):
        if p.is_file():
            ct = content_type or ("application/json" if p.suffix == ".json" else "application/octet-stream")
            store.put_file(prefix + p.relative_to(local).as_posix(), p, ct)
            n += 1
    return n


def publish_results(store, sid, bench_key, rows_jsonl: bytes):
    """Per item and sample: what judging and any re-computation need (id, sample, correct, finish, tokens), without
    the generated text, which would make results orders of magnitude larger than the decision they support."""
    rows = [{k: v for k, v in json.loads(line).items() if k != "text"} for line in rows_jsonl.split(b"\n") if line.strip()]
    data = "\n".join(json.dumps(r, sort_keys=True) for r in rows).encode()
    store.put(layout.submission_results(sid, bench_key), gzip.compress(data, mtime=0), "application/gzip")


def publish_admission(store, sid, admission: dict):
    _json(store, layout.submission(sid, "admission.json"), {"schema": SCHEMAS["admission"], "sid": sid, **admission})


def publish_score(store, sid, score: dict):
    _json(store, layout.submission(sid, "score.json"), {"schema": SCHEMAS["score"], "sid": sid, **score})


def publish_submission(store, sid, record: dict):
    _json(store, layout.submission(sid, "submission.json"), {"schema": SCHEMAS["submission"], "sid": sid, **record})


def publish_ruling(store, sid, ruling: dict):
    """submissions/<sid>/ruling.json: the owner's disqualification of an evaluated submission (write-once)."""
    _json(store, layout.submission(sid, "ruling.json"), {"schema": SCHEMAS["ruling"], "sid": sid, **ruling})


def publish_season(store, season, matches: list, leaderboard: dict):
    """matches.json: one row per submission of the season (details live under submissions/<sid>/);
    leaderboard.json: the champion, its succession history and the Pareto front."""
    _json(store, layout.season_file(season, "matches.json"),
          {"schema": SCHEMAS["matches"], "season": season, "updated_at": int(time.time()), "rows": matches})
    _json(store, layout.season_file(season, "leaderboard.json"),
          {"schema": SCHEMAS["leaderboard"], "season": season, "updated_at": int(time.time()), **leaderboard})


def build_weights(netuid, season, champion, sequence, block, fallback_uid=0):
    """champion: {"sid", "hotkey", "uid"} or None (base model is champion -> everything to fallback uid0 = burn)."""
    if champion and champion.get("uid") is not None:
        weights = [{"uid": champion["uid"], "hotkey": champion["hotkey"], "weight": 1.0}]
    else:
        weights = [{"uid": fallback_uid, "hotkey": None, "weight": 1.0}]
    return {"schema": SCHEMAS["weights"], "netuid": netuid, "sequence": sequence, "season": season,
            "issued_at": int(time.time()), "issued_block": block, "champion": champion,
            "weights": weights,
            "fallback": {"uid": fallback_uid, "reason": "no champion or champion hotkey not registered: uid0 (burn)"}}


def publish_weights(store, doc: dict, keypair):
    old = store.get(layout.WEIGHTS)
    if old:
        prev = json.loads(old)
        if prev.get("sequence", -1) >= doc["sequence"]:
            raise PublishError(f"weights sequence {doc['sequence']} not above published {prev.get('sequence')}")
    signed = sign_doc(doc, keypair)
    _json(store, layout.WEIGHTS, signed)
    back = json.loads(store.get(layout.WEIGHTS))
    if back != signed or not verify_doc(back, keypair.ss58_address):
        raise PublishError("weights.json read-back does not verify")
    return signed


def publish_index(store, current_season, rules_sha256):
    """index.json: the entry point - the current season and the key of the rules version in force. Netuid, owner and
    this location itself are published with the code (axonry_validator.network), not repeated here."""
    _json(store, layout.INDEX, {"schema": SCHEMAS["index"], "current_season": current_season,
                                "rules": layout.season_rules_version(current_season, rules_sha256)})
