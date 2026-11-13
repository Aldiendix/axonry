"""Canonical JSON and hashing: the one serialization every signature and digest is computed over."""
import hashlib
import json
from pathlib import Path


def canonical(obj) -> bytes:
    """Sorted keys, no whitespace, UTF-8, no NaN. Signatures and digests are always over these bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def pretty(obj) -> bytes:
    """Human-readable JSON for published files (content identical to canonical after parsing)."""
    return (json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n").encode()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pretty(obj))
