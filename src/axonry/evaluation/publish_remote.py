"""Upload a built model's own files from the evaluation machine straight to the public bucket, with temporary
credentials scoped to that submission's model/ prefix (the model never passes through the orchestrator).

  AXONRY_R2_CREDS='<TempCreds as JSON>' python3 -m axonry.evaluation.publish_remote DIR
Uploads every regular file directly in DIR (untouched base files are links and are not uploaded); prints
{"files": {name: size}}. Re-running is safe: the bucket keeps an identical object as it is.
"""
import json
import os
import sys
from pathlib import Path

from ..common.store import R2Store


def main(argv=None):
    src = Path((argv or sys.argv[1:])[0])
    c = json.loads(os.environ["AXONRY_R2_CREDS"])
    store = R2Store(c["endpoint"], c["bucket"], c["access_key_id"], c["secret_access_key"], c["session_token"],
                    public=True)
    files = {}
    for p in sorted(src.iterdir()):
        if p.is_file() and not p.is_symlink():
            store.put(c["prefix"] + p.name, p.read_bytes())
            files[p.name] = p.stat().st_size
    print(json.dumps({"files": files}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
