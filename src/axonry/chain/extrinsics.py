"""What axonry reads from the chain (pure parsing of decoded extrinsics, testable without a node).

  commitment  Commitments.set_commitment(netuid, info.fields[0] = Raw "<content_digest>:<GitHub user id>")
              signed by a hotkey registered on the subnet (the chain refuses it otherwise; free of fees). Every
              version of a submission's content is committed before it is pushed; the (block, extrinsic index) of
              the content's earliest commitment is its priority. One extrinsic carries at most one commitment.
  deposit     a transfer to the treasury (the owner coldkey), credited to the sender coldkey's account:
              Balances.transfer_keep_alive | transfer_allow_death (dest = treasury)                    -> "tao"
              SubtensorModule.transfer_stake | transfer_stake_and_hotkey
                                                 (destination_coldkey = treasury, netuid both = netuid) -> "alpha"
Both may also come together in one Utility.batch_all (atomic: it succeeded as a whole or not at all); deposits of
one extrinsic are summed per asset. Anything else (Utility.batch, proxies, multisig, MevShield-encrypted
extrinsics) is not read. Decoding checked against finney extrinsics (transfer_allow_death / transfer_keep_alive).
"""
from dataclasses import dataclass, replace

from ..common.ids import parse_commitment

TAO_CALLS = {("Balances", "transfer_keep_alive"), ("Balances", "transfer_allow_death")}
ALPHA_CALLS = {("SubtensorModule", "transfer_stake"), ("SubtensorModule", "transfer_stake_and_hotkey")}
COMMIT_CALL = ("Commitments", "set_commitment")


@dataclass(frozen=True)
class Commitment:
    block: int
    extrinsic_index: int
    signer: str                    # the hotkey that committed
    content_digest: str
    github_id: int                 # the GitHub account whose PR may carry this content
    timestamp: int                 # unix seconds of the block

    @property
    def priority(self) -> tuple:
        return (self.block, self.extrinsic_index)


@dataclass(frozen=True)
class Deposit:
    block: int
    extrinsic_index: int
    coldkey: str                   # the sender: the account credited
    asset: str                     # "tao" | "alpha"
    amount_rao: int
    timestamp: int


def _args(call):
    a = call.get("call_args", [])
    return {x["name"]: x["value"] for x in a} if isinstance(a, list) else dict(a)


def _key(call):
    return call.get("call_module"), call.get("call_function")


def _addr(v):
    """substrate-interface renders AccountId / MultiAddress either as str or {'Id': str}."""
    if isinstance(v, dict):
        return v.get("Id") or v.get("id") or next(iter(v.values()), None)
    return v


def _commitment_text(info):
    """The first field of a CommitmentInfo ({"fields": [{"Raw<n>": "0x..."}]}) as text, or None."""
    fields = (info or {}).get("fields") if isinstance(info, dict) else None
    if not fields or not isinstance(fields[0], dict) or len(fields[0]) != 1:
        return None
    kind, v = next(iter(fields[0].items()))
    return _text(v) if kind.startswith("Raw") else None


def _text(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    if isinstance(v, str) and v.startswith("0x"):
        try:
            return bytes.fromhex(v[2:]).decode("utf-8", "replace")
        except ValueError:
            return v
    return str(v)


def _one(call, signer, base, treasury, netuid):
    a = _args(call)
    if _key(call) == COMMIT_CALL:
        r = parse_commitment(_commitment_text(a.get("info")) or "")
        if r and int(a.get("netuid", -1)) == netuid:
            return Commitment(content_digest=r[0], github_id=r[1], signer=signer, **base)
    elif _key(call) in TAO_CALLS:
        if _addr(a.get("dest")) == treasury:
            return Deposit(coldkey=signer, asset="tao", amount_rao=int(a.get("value", 0)), **base)
    elif _key(call) in ALPHA_CALLS:
        if (_addr(a.get("destination_coldkey")) == treasury and int(a.get("origin_netuid", -1)) == netuid
                and int(a.get("destination_netuid", -1)) == netuid):
            return Deposit(coldkey=signer, asset="alpha", amount_rao=int(a.get("alpha_amount", 0)), **base)
    return None


def parse_extrinsic(call: dict, *, signer: str, block: int, extrinsic_index: int, timestamp: int, success: bool,
                    treasury: str, netuid: int) -> list:
    """-> the commitments and deposits in one successful extrinsic (empty if none)."""
    if not success:
        return []
    base = dict(block=block, extrinsic_index=extrinsic_index, timestamp=timestamp)
    calls = (_args(call).get("calls") or []) if _key(call) == ("Utility", "batch_all") else [call]
    out, deposits = [], {}
    for x in (_one(c, signer, base, treasury, netuid) for c in calls):
        if isinstance(x, Deposit):
            deposits[x.asset] = replace(x, amount_rao=x.amount_rao + deposits[x.asset].amount_rao) \
                if x.asset in deposits else x
        elif isinstance(x, Commitment) and not any(isinstance(y, Commitment) for y in out):
            out.append(x)
    return out + list(deposits.values())
