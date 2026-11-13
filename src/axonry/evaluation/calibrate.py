"""A/A noise calibration: the base model against itself under two seed salts, per benchmark.

The paired difference of identical models is pure sampling noise. Its SE at the season's sample sizes tells
  - whether "not worse" (d >= -max(z_tie*SE, eps_tie)) tolerates real noise, and
  - the smallest gain a challenger can prove: z_win*SE + delta_win (the detectable gain).
Thresholds are then set per benchmark in the benchmark definitions / rules, not guessed.
"""
import json
from pathlib import Path

from .. import judge, registry
from ..benchmarks import parse


def calibrate(executor, rules, registry_root=registry.ROOT, salts=("aa-1", "aa-2"), out: Path | None = None):
    base_doc = registry.load_base(rules["base"], registry_root)
    z_tie, z_win = rules["judge"]["z_tie"], rules["judge"]["z_win"]
    report = {"base": rules["base"], "salts": list(salts), "benchmarks": {}}
    keys = rules.benchmark_keys()
    benches = {k: registry.load_benchmark(k, registry_root).doc for k in keys}
    outs = [executor.evaluate(f"base:{rules['base']}", base_doc, list(benches.values()), s) for s in salts]
    for key in keys:
        bench, params = benches[key], rules.judge_params(key)
        runs = [parse(o[key]) for o in outs]
        d, se, n = judge.paired(runs[0], runs[1])
        row = {"items": n, "samples_per_item": bench["samples_per_item"], "acc": [round(judge.accuracy(r), 2) for r in runs],
               "aa_d": round(d, 3), "se": round(se, 3),
               "tie_band": round(max(z_tie * se, params["eps_tie"]), 3),
               "detectable_gain": round(z_win * se + params["delta_win"], 3),
               "aa_tie_ok": d >= -max(z_tie * se, params["eps_tie"]),
               "aa_false_win": d - z_win * se >= params["delta_win"]}
        report["benchmarks"][key] = row
        print(json.dumps({key: row}), flush=True)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(report, indent=1))
    return report
