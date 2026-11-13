"""Submission ids: the chain position of the commitment a submission entered with; globally unique, lexically
sortable = chain order."""
import re

SID_RE = re.compile(r"^(\d{10})-(\d{4})$")


def make_sid(block: int, extrinsic_index: int) -> str:
    if not (0 <= block < 10**10 and 0 <= extrinsic_index < 10**4):
        raise ValueError("block or extrinsic index out of range")
    return f"{block:010d}-{extrinsic_index:04d}"


def make_commitment(content_digest: str, github_id: int) -> str:
    """The text of one content commitment: "<content digest>:<GitHub user id>", set with Commitments.set_commitment
    on the subnet's netuid and signed by the hotkey (the chain says who signed and requires it to be registered).

    The digest proves the content existed at this chain position before it was public; the GitHub id (the numeric
    account id, which survives renames and is never reused) names the only account whose PR may carry it."""
    if not re.fullmatch(r"[0-9a-f]{64}", content_digest) or not isinstance(github_id, int) or github_id <= 0:
        raise ValueError("a commitment needs a sha256 hex digest and a positive GitHub user id")
    return f"{content_digest}:{github_id}"


def parse_commitment(text: str):
    """-> (content_digest, github_id) or None if it is not ours / malformed."""
    m = re.fullmatch(r"([0-9a-f]{64}):([1-9][0-9]{0,18})", text)
    return (m.group(1), int(m.group(2))) if m else None
