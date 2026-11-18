"""Judging. Pure functions over per-item scores; no I/O, no benchmark names.

ItemScores: {item_id: [sample scores in [0, 1], one per sample]} for one benchmark and one model.
Results:    {bench_key: ItemScores} for one model.

Per benchmark, candidate C vs reference R on the same items and per-item seeds:
  d   = mean over items of (mean_C(item) - mean_R(item)), in percentage points
  SE  = std of the per-item differences / sqrt(n_items), in points (clustered by item, as wsp.stats)
  tie_ok (not worse): d >= -max(z_tie * SE, eps_tie)
  win:                d - z_win * SE >= delta_win
C dominates R: tie_ok on every judged benchmark and win on at least n_win of them.
"""
import math
from dataclasses import asdict, dataclass


class JudgeError(ValueError):
    pass


@dataclass
class BenchComparison:
    d: float
    se: float
    n: int
    tie_ok: bool
    win: bool


def _item_means(scores):
    return {k: sum(v) / len(v) for k, v in scores.items() if v}


def paired(c_scores: dict, r_scores: dict):
    """-> (d, se, n) in percentage points. Items must match exactly."""
    c, r = _item_means(c_scores), _item_means(r_scores)
    if set(c) != set(r):
        raise JudgeError(f"item sets differ ({len(set(c) ^ set(r))} items not shared)")
    n = len(c)
    if n == 0:
        raise JudgeError("no items")
    diffs = [c[k] - r[k] for k in c]
    mean = sum(diffs) / n
    var = sum((x - mean) ** 2 for x in diffs) / (n - 1) if n > 1 else 0.0
    return 100 * mean, 100 * math.sqrt(var / n), n


def accuracy(scores: dict) -> float:
    m = _item_means(scores)
    return 100 * sum(m.values()) / len(m) if m else float("nan")


def compare_bench(c_scores, r_scores, params: dict, z_tie: float, z_win: float) -> BenchComparison:
    d, se, n = paired(c_scores, r_scores)
    tie_ok = d >= -max(z_tie * se, params["eps_tie"])
    win = d - z_win * se >= params["delta_win"]
    return BenchComparison(round(d, 6), round(se, 6), n, tie_ok, win)


def compare(c_results: dict, r_results: dict, bench_params: dict, judge: dict) -> dict:
    """bench_params: {bench_key: {eps_tie, delta_win}} for the judged benchmarks (defines which are judged).
    -> {"benchmarks": {key: BenchComparison as dict}, "wins": int, "tie_all": bool, "dominates": bool}"""
    out = {}
    for key, params in bench_params.items():
        if key not in c_results or key not in r_results:
            raise JudgeError(f"missing results for {key}")
        out[key] = compare_bench(c_results[key], r_results[key], params, judge["z_tie"], judge["z_win"])
    wins = sum(b.win for b in out.values())
    tie_all = all(b.tie_ok for b in out.values())
    return {"benchmarks": {k: asdict(v) for k, v in out.items()}, "wins": wins, "tie_all": tie_all,
            "dominates": tie_all and wins >= judge["n_win"]}

