import random

import pytest

from axonry import judge

PARAMS = {"a": {"eps_tie": 1.0, "delta_win": 1.0}, "b": {"eps_tie": 1.0, "delta_win": 1.0},
          "c": {"eps_tie": 1.0, "delta_win": 1.0}}
JUDGE = {"z_tie": 2.0, "z_win": 2.0, "n_win": 2}


def scores(p, n=400, k=4, seed=0):
    rng = random.Random(seed)
    return {f"q{i}": [1.0 if rng.random() < p else 0.0 for _ in range(k)] for i in range(n)}


def test_paired_identical_is_zero():
    s = scores(0.5)
    assert judge.paired(s, s) == (0.0, 0.0, 400)


def test_paired_rejects_item_mismatch():
    with pytest.raises(judge.JudgeError):
        judge.paired({"a": [1]}, {"b": [1]})


def test_clear_gain_wins_and_noise_ties():
    base = {"a": scores(0.4, seed=1), "b": scores(0.4, seed=2), "c": scores(0.4, seed=3)}
    better = {"a": {k: [1.0] * 4 if i % 5 == 0 else v for i, (k, v) in enumerate(base["a"].items())},
              "b": {k: [1.0] * 4 if i % 5 == 0 else v for i, (k, v) in enumerate(base["b"].items())},
              "c": base["c"]}
    r = judge.compare(better, base, PARAMS, JUDGE)
    assert r["benchmarks"]["a"]["win"] and r["benchmarks"]["b"]["win"]
    assert not r["benchmarks"]["c"]["win"] and r["benchmarks"]["c"]["tie_ok"]
    assert r["wins"] == 2 and r["dominates"]


def test_one_regression_blocks_domination():
    base = {"a": scores(0.4, seed=1), "b": scores(0.4, seed=2), "c": scores(0.6, seed=3)}
    up = lambda s: {k: [1.0] * 4 if i % 4 == 0 else v for i, (k, v) in enumerate(s.items())}  # noqa: E731
    down = {k: [0.0] * 4 if i % 4 == 0 else v for i, (k, v) in enumerate(base["c"].items())}
    r = judge.compare({"a": up(base["a"]), "b": up(base["b"]), "c": down}, base, PARAMS, JUDGE)
    assert r["wins"] == 2 and not r["benchmarks"]["c"]["tie_ok"] and not r["dominates"]


def test_pareto_front():
    base = {"a": scores(0.4, seed=1), "b": scores(0.4, seed=2), "c": scores(0.4, seed=3)}
    up = lambda s: {k: [1.0] * 4 if i % 4 == 0 else v for i, (k, v) in enumerate(s.items())}  # noqa: E731
    x = {"a": up(base["a"]), "b": up(base["b"]), "c": base["c"]}
    assert judge.pareto_front({"base": base, "x": x}, PARAMS, JUDGE) == ["x"]
