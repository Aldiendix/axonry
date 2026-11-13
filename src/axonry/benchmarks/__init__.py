"""Benchmarks: a registry definition (registry/benchmarks/<id>@<v>.json: the competition toolkit's task
name, samples per item, the frozen item-set sha256, judging thresholds). Every benchmark is run by the toolkit
(adapter "wsp") through axonry.evaluation.eval_remote; adding one is a data change only. Judging never sees names.
"""
import json


def parse(data: bytes) -> dict:
    """Output JSON lines -> {item_id: [sample scores in [0, 1]]}. Split on \\n only (str.splitlines also breaks
    on U+2028 inside answers)."""
    scores = {}
    for line in data.decode().split("\n"):
        if line.strip():
            r = json.loads(line)
            scores.setdefault(str(r["id"]), []).append(1.0 if r["correct"] else 0.0)
    return scores
