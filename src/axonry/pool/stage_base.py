#!/usr/bin/env python3
"""Stage a pinned base on a machine (stdlib only; run by setup.sh): download every file of the base definition from
Hugging Face at the pinned revision, check its sha256, mark the directory VERIFIED. Nothing unverified is ever used.

  stage_base.py BASE.json DEST_DIR
"""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path


def main(doc_path, dest):
    doc, dst = json.loads(Path(doc_path).read_text()), Path(dest)
    if (dst / "VERIFIED").exists():
        return 0
    for rel, sha in sorted(doc["files"].items()):
        f = dst / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        url = f"https://huggingface.co/{doc['hf_repo']}/resolve/{doc['revision']}/{rel}"
        with urllib.request.urlopen(url, timeout=600) as r, open(f, "wb") as out:
            while chunk := r.read(1 << 20):
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest() != sha:
            raise SystemExit(f"{rel}: sha256 {h.hexdigest()} != pinned {sha}")
    (dst / "VERIFIED").write_text(json.dumps({"id": doc["id"], "revision": doc["revision"]}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
