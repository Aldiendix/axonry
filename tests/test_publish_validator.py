import json
import types

import pytest
from bittensor_wallet import Keypair

from axonry.common import layout
from axonry.common.store import ImmutableObjectError, LocalStore
from axonry.publish import publisher
from axonry_validator import main as v

OWNER = Keypair.create_from_uri("//Alice")
HK = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"


def test_store_write_once(tmp_path):
    s = LocalStore(tmp_path, public=True)
    s.put("submissions/x/score.json", b"1")
    s.put("submissions/x/score.json", b"1")  # idempotent
    with pytest.raises(ImmutableObjectError):
        s.put("submissions/x/score.json", b"2")
    s.put(layout.WEIGHTS, b"a")
    s.put(layout.WEIGHTS, b"b")  # mutable


def test_weights_sign_verify_and_monotonic(tmp_path):
    s = LocalStore(tmp_path, public=True)
    doc = publisher.build_weights(7, "s1", {"sid": "0000000001-0001", "hotkey": HK, "uid": 3}, 1, 100)
    signed = publisher.publish_weights(s, doc, OWNER)
    assert v.check(signed, OWNER.ss58_address, 7, -1) is None
    assert v.check(signed, OWNER.ss58_address, 8, -1)
    assert v.check({**signed, "sequence": 2}, OWNER.ss58_address, 7, -1) == "bad signature"
    assert v.check(signed, OWNER.ss58_address, 7, 5)
    with pytest.raises(publisher.PublishError):
        publisher.publish_weights(s, doc, OWNER)


def test_resolve_falls_back_to_uid0():
    doc = publisher.build_weights(7, "s1", {"sid": "x", "hotkey": HK, "uid": 3}, 1, 100)
    assert v.resolve(doc, {3: HK}) == {3: 1.0}
    assert v.resolve(doc, {3: "someone else"}) == {0: 1.0}
    assert v.resolve(publisher.build_weights(7, "s1", None, 1, 100), {}) == {0: 1.0}


def test_validator_cycle_keeps_last_valid(tmp_path):
    s = LocalStore(tmp_path / "pub", public=True)
    signed = publisher.publish_weights(s, publisher.build_weights(7, "s1", {"sid": "x", "hotkey": HK, "uid": 3}, 1, 9),
                                       OWNER)
    a = types.SimpleNamespace(url="u", owner=OWNER.ss58_address, netuid=7, max_age=10**9, version_key=0)
    state = v.State(tmp_path / "state.json")
    calls = []
    sub = types.SimpleNamespace(query_map=lambda item, params: [(3, HK)])
    w = v.cycle(a, state, sub, None, fetcher=lambda u: signed, setter=lambda *x: calls.append(x[3]))
    assert w == {3: 1.0}
    tampered = {**signed, "weights": [{"uid": 9, "hotkey": None, "weight": 1.0}]}
    w = v.cycle(a, state, sub, None, fetcher=lambda u: tampered, setter=lambda *x: calls.append(x[3]))
    assert w == {3: 1.0} and calls == [{3: 1.0}, {3: 1.0}]


def test_publish_registry(tmp_path):
    from axonry import registry
    s = LocalStore(tmp_path, public=True)
    publisher.publish_registry(s)
    rules = registry.load_rules_versions("s1")[-1]
    publisher.publish_index(s, "s1", rules.sha256)
    idx = json.loads(s.get(layout.INDEX))
    assert json.loads(s.get(idx["rules"])) == rules.doc          # index -> the rules version in force, no copy
    assert s.get(layout.base_def("qwen35-0.8b-think")) and s.get(layout.benchmark_def("math500", 1))
