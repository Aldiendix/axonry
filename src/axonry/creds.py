"""Temporary R2 credentials scoped to one run's staging prefix.

Cloudflare API: POST /accounts/{account_id}/r2/temp-access-credentials
  {"bucket", "parentAccessKeyId", "permission": "object-read-write", "ttlSeconds", "prefixes": [...]}
  -> result {"accessKeyId", "secretAccessKey", "sessionToken"}
The parent access key is dedicated to issuing these and only covers the private bucket. Temporary credentials
cannot be revoked one by one: the TTL is the end of the run.
"""
import json
import time
import urllib.request
from dataclasses import asdict, dataclass

CF_API = "https://api.cloudflare.com/client/v4"


@dataclass
class TempCreds:
    endpoint: str
    bucket: str
    prefix: str
    access_key_id: str
    secret_access_key: str
    session_token: str
    expires_at: int

    def as_dict(self):
        return asdict(self)


def issue(account_id: str, api_token: str, parent_access_key_id: str, bucket: str, prefix: str, ttl_seconds: int,
          endpoint: str, opener=urllib.request.urlopen) -> TempCreds:
    body = {"bucket": bucket, "parentAccessKeyId": parent_access_key_id, "permission": "object-read-write",
            "ttlSeconds": int(ttl_seconds), "prefixes": [prefix]}
    req = urllib.request.Request(f"{CF_API}/accounts/{account_id}/r2/temp-access-credentials",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"})
    with opener(req, timeout=30) as r:
        resp = json.loads(r.read())
    if not resp.get("success"):
        raise RuntimeError(f"temp credentials refused: {resp.get('errors')}")
    res = resp["result"]
    return TempCreds(endpoint, bucket, prefix, res["accessKeyId"], res["secretAccessKey"], res["sessionToken"],
                     int(time.time()) + int(ttl_seconds))
