"""Live R2 checks (opt-in: AXONRY_LIVE_R2=1 and the project's .env). Verifies the object store and the per-run temporary
credentials against Cloudflare: scoped to staging/<sid>/, refused outside it and in the public bucket."""
import os
import time
from pathlib import Path

import pytest

from axonry import creds
from axonry.common.store import R2Store

ENV = Path(__file__).resolve().parents[1] / ".env"
pytestmark = pytest.mark.skipif(os.environ.get("AXONRY_LIVE_R2") != "1" or not ENV.exists(), reason="live R2 test opt-in")


def env():
    return {k.strip(): v.strip() for k, v in (l.split("=", 1) for l in ENV.read_text().splitlines()
                                              if "=" in l and not l.startswith("#"))}


def test_temp_credentials_scoped():
    e = env()
    sid = f"live-{int(time.time())}"
    op = R2Store(e["AXONRY_R2_ENDPOINT"], "axonry-private", e["AXONRY_R2_ACCESS_KEY_ID"], e["AXONRY_R2_SECRET_ACCESS_KEY"])
    c = creds.issue(e["AXONRY_R2_ACCOUNT_ID"], e["CLOUDFLARE_CONTROL_API_TOKEN"], e["AXONRY_R2_ACCESS_KEY_ID"], "axonry-private",
                    f"staging/{sid}/", 900, e["AXONRY_R2_ENDPOINT"])
    t = R2Store(c.endpoint, c.bucket, c.access_key_id, c.secret_access_key, c.session_token)
    try:
        t.put(f"staging/{sid}/edit/edit.json", b"{}")
        assert op.get(f"staging/{sid}/edit/edit.json") == b"{}"
        assert op.last_modified(f"staging/{sid}/edit/edit.json")
        for bucket, key in (("axonry-private", "staging/other/x"), ("axonry-public", f"staging/{sid}/x")):
            with pytest.raises(Exception):
                R2Store(c.endpoint, bucket, c.access_key_id, c.secret_access_key, c.session_token).put(key, b"x")
    finally:
        op.delete_prefix(f"staging/{sid}/")
