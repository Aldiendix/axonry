#!/usr/bin/env python3
"""Stage a pinned base on a machine (stdlib only; run by setup.sh): every file of the base definition, checked against
its pinned sha256, then the directory is marked VERIFIED. Nothing unverified is ever used, so the file may come from
any source: Hugging Face at the pinned revision, else ModelScope's copy of the repo (hosts that cannot reach Hugging
Face), whichever delivers the pinned bytes.

  stage_base.py BASE.json DEST_DIR
"""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path


def sources(doc, rel):
    yield f"https://huggingface.co/{doc['hf_repo']}/resolve/{doc['revision']}/{rel}"
    yield f"https://www.modelscope.cn/models/{doc['hf_repo']}/resolve/master/{rel}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, dest: Path) -> str:
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=600) as r, open(dest, "wb") as out:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()


def main(doc_path, dest):
    doc, dst = json.loads(Path(doc_path).read_text()), Path(dest)
    if (dst / "VERIFIED").exists():
        return 0
    for rel, sha in sorted(doc["files"].items()):
        f = dst / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists() and sha256_of(f) == sha:      # from an interrupted earlier run
            continue
        tried = []
        for url in sources(doc, rel):
            try:
                got = fetch(url, f)
            except OSError as e:                      # unreachable, refused, timed out
                tried.append(f"{url}: {e}")
                continue
            if got == sha:
                break
            tried.append(f"{url}: sha256 {got} != pinned {sha}")
        else:
            f.unlink(missing_ok=True)
            raise SystemExit(f"{rel}: no source delivered the pinned file:\n  " + "\n  ".join(tried))
    (dst / "VERIFIED").write_text(json.dumps({"id": doc["id"], "revision": doc["revision"]}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
