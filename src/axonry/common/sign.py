"""Owner signatures over canonical JSON (sr25519, the owner hotkey). Used for weights.json; verifiable by anyone
who knows the owner's ss58 address."""
from .canon import canonical

SIG_FIELD = "signature"
SIGNER_FIELD = "signer"


def signing_bytes(doc: dict) -> bytes:
    return canonical({k: v for k, v in doc.items() if k != SIG_FIELD})


def sign_doc(doc: dict, keypair) -> dict:
    """keypair: bittensor_wallet.Keypair with a private key. Sets signer and signature."""
    out = dict(doc)
    out[SIGNER_FIELD] = keypair.ss58_address
    out.pop(SIG_FIELD, None)
    out[SIG_FIELD] = "0x" + keypair.sign(signing_bytes(out)).hex()
    return out


def verify_doc(doc: dict, expected_signer: str) -> bool:
    from bittensor_wallet import Keypair  # noqa: PLC0415

    sig = doc.get(SIG_FIELD)
    if doc.get(SIGNER_FIELD) != expected_signer or not isinstance(sig, str) or not sig.startswith("0x"):
        return False
    try:
        return bool(Keypair(ss58_address=expected_signer).verify(signing_bytes(doc), bytes.fromhex(sig[2:])))
    except Exception:  # malformed signature or address
        return False


def load_keypair(wallet_name: str, hotkey: str, path: str | None = None):
    """The owner hotkey from a standard bittensor wallet directory."""
    from bittensor_wallet import Wallet  # noqa: PLC0415

    w = Wallet(name=wallet_name, hotkey=hotkey, **({"path": path} if path else {}))
    return w.hotkey
