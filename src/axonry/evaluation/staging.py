"""Collect a run's upload from the private bucket (the competition protocol's upload section).

The final result is the manifest present at the deadline: its listed files must all exist with matching sha256
and size, be exactly the protocol files (competition rules: upload.files), and fit the size cap. Objects written after the deadline are ignored (the grace
period only lets an upload in flight finish; a manifest written in it does not count).
"""
import json
from pathlib import Path

from ..common import layout
from ..common.canon import sha256_hex

MAX_TOTAL_BYTES = 8 * 1024 * 1024


class UploadError(ValueError):
    pass


def collect(store, sid: str, deadline: float, dest: Path, upload_rules: dict) -> dict:
    """upload_rules: the competition protocol's `upload` section (schema, files); the uploader is wsp.upload."""
    allowed_paths, schema = set(upload_rules["files"]), upload_rules["schema"]
    """-> manifest dict; files written under dest/ (edit/ tree). Raises UploadError with a public reason."""
    prefix = layout.staging_prefix(sid)
    mkey = prefix + "manifest.json"
    lm = store.last_modified(mkey)
    if lm is None:
        raise UploadError("no manifest.json uploaded")
    if lm > deadline:
        raise UploadError("manifest.json written after the deadline")
    try:
        man = json.loads(store.get(mkey))
    except (TypeError, ValueError) as e:
        raise UploadError(f"manifest.json unreadable: {e}") from e
    if man.get("schema") != schema or not isinstance(man.get("files"), list):
        raise UploadError(f"manifest.json: schema must be {schema} with a files list")
    total = 0
    dest = Path(dest)
    for f in man["files"]:
        rel = str(f.get("path", ""))
        if rel not in allowed_paths:
            raise UploadError(f"{rel}: not a file of the upload protocol ({', '.join(sorted(allowed_paths))})")
        key = prefix + rel
        flm = store.last_modified(key)
        if flm is None:
            raise UploadError(f"{rel}: listed but not uploaded")
        if flm > deadline:
            raise UploadError(f"{rel}: written after the deadline")
        data = store.get(key)
        if len(data) != f.get("size") or sha256_hex(data) != f.get("sha256"):
            raise UploadError(f"{rel}: size or sha256 does not match the manifest")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise UploadError(f"upload larger than {MAX_TOTAL_BYTES} bytes")
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    if not (dest / "edit" / "edit.json").exists():
        raise UploadError("edit/edit.json missing")
    return man

