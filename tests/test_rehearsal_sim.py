"""The rehearsal's simulated GitHub and chain drive the real client and parser code."""
import json

import pytest

from axonry.chain.extrinsics import Commitment, Deposit
from axonry.common.digest import content_digest_of_blobs
from axonry.common.ids import make_commitment
from axonry.intake.github import scan
from axonry.orchestrator.rehearsal import SimChain, SimGitHub, SimRepo, commit_call

COMP = json.load(open(__file__.rsplit("/", 2)[0] + "/WeightSpaceProgramming/rules.json"))
HK = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
FILES = {"entry.json": b"{}", "METHOD.md": b"m"}


def test_github_client_against_the_simulator():
    repo = SimRepo("org/comp")
    owner, reviewer, author = SimGitHub(repo, "owner"), SimGitHub(repo, "rev"), SimGitHub(repo, "alice")
    n = repo.open_pr("alice", f"submissions/{HK}/main/", FILES)
    (c,) = scan(owner, COMP)
    assert (c.number, c.author, c.author_id, c.hotkey, c.problem) == (n, "alice", repo.user("alice")["id"], HK, None)
    assert c.digest == content_digest_of_blobs(FILES)
    with pytest.raises(Exception):
        author.post_review(n, c.head, "APPROVE", "self")              # GitHub refuses self-approval
    reviewer.post_review(n, c.head, "APPROVE", "ok")
    assert owner.review_state(n, c.digest, c.directory, ["rev"]) == ("approved", "ok")
    assert owner.review_state(n, c.digest, c.directory, ["someone"]) == (None, None)
    owner.lock_tag("0000000001-0001", c.head)
    owner.lock_tag("0000000001-0001", c.head)                          # idempotent
    with pytest.raises(RuntimeError):
        owner.lock_tag("0000000001-0001", "other")
    repo.push(n, {**FILES, "METHOD.md": b"later"})                    # head moves after the lock
    owner.merge(c.head, "Champion")                                    # the locked commit is merged anyway
    assert owner.dir_blobs(repo.master, c.directory) == FILES


def test_chain_simulator_goes_through_the_real_parser():
    ch = SimChain(7)
    ch.register(HK, "COLD", 3)
    b, i = ch.submit(HK, commit_call(7, make_commitment("ab" * 32, 1001)))
    ch.submit("UNREGISTERED", commit_call(7, make_commitment("cd" * 32, 1001)))    # refused by the chain
    ch.submit("COLD", {"call_module": "Balances", "call_function": "transfer_keep_alive",
                       "call_args": [{"name": "dest", "value": "TREAS"}, {"name": "value", "value": 5}]})
    got = ch.items_in_block(b, "TREAS", 7)
    assert isinstance(got[0], Commitment) and got[0].github_id == 1001 and (got[0].block, got[0].extrinsic_index) == (b, i)
    assert isinstance(got[1], Deposit) and got[1].amount_rao == 5 and got[1].coldkey == "COLD"
