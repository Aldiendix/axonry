"""content_digest of a submission directory.

sha256 over, for every file sorted by POSIX relative path (bytewise): path \\0 size \\0 bytes.
No mtime, permissions or git metadata; __pycache__ and *.pyc are ignored so a local run does not change the digest.
The same function is used by the miner CLI, the orchestrator and the competition repo's checks.
"""
import hashlib
from pathlib import Path

IGNORED_PARTS = {"__pycache__", ".git", ".DS_Store"}


def iter_files(root):
    root = Path(root)
    files = [p for p in root.rglob("*")
             if p.is_file() and not IGNORED_PARTS & set(p.relative_to(root).parts) and p.suffix != ".pyc"]
    return sorted(files, key=lambda p: p.relative_to(root).as_posix().encode())


def content_digest(root) -> str:
    root, h = Path(root), hashlib.sha256()
    for p in iter_files(root):
        data = p.read_bytes()
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + str(len(data)).encode() + b"\0")
        h.update(data)
    return h.hexdigest()


def content_digest_of_blobs(blobs: dict) -> str:
    """Same digest from {relative path: bytes} (e.g. a tree read through the GitHub API, without a checkout)."""
    h = hashlib.sha256()
    for rel in sorted(blobs, key=lambda r: r.encode()):
        if IGNORED_PARTS & set(rel.split("/")) or rel.endswith(".pyc"):
            continue
        data = blobs[rel]
        h.update(rel.encode() + b"\0" + str(len(data)).encode() + b"\0")
        h.update(data)
    return h.hexdigest()
