"""Sign the orchestrator into an arbos relay with the owner's GitHub account, and keep the token fresh.

The relay's flow (crates/arbos-relay/src/signin.rs):
  1. the browser opens GET <relay>/auth/github/start?redirect=<loopback URL>&device_name=...
  2. after GitHub, the relay redirects to <loopback URL>?code=<grant>   (the grant is valid for 60 s, one use)
  3. POST <relay>/auth/token {"grant_code"} -> {access_token (JWT, 1 h), refresh_token, device_id, user}
  4. POST <relay>/auth/refresh {"refresh_token"} -> a new pair (rotation)
arbosctl only knows a static `device_token`, so `ensure_fresh` refreshes the pair and rewrites arbosctl's config.

Several arbos accounts can run tasks (each has its own box: [agent.arbos] accounts); each is signed in once:
  python -m axonry.agent.arbos.auth [--account NAME] url      [--relay URL] [--port 53682]   print the sign-in link
  python -m axonry.agent.arbos.auth [--account NAME] listen   [--relay URL] [--port 53682]   catch the redirect on
                                                                                              loopback, exchange it
  python -m axonry.agent.arbos.auth [--account NAME] complete '<redirected URL>'   exchange a pasted redirect URL
                                                                                   (within 60 s)
  python -m axonry.agent.arbos.auth [--account NAME] status | refresh
  python -m axonry.agent.arbos.auth import FILE      accounts issued as JSON lines ({"handle", "tokens"}): each is
                                                    saved under its handle and refreshed at once
Per account (default "main"): ~/.config/axonry/arbos/<NAME>/tokens.json (0600) and arbosctl.json (arbosctl's config).
Sign in each account with its own GitHub login (a private browser window per account).
"""
import argparse
import fcntl
import http.server
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

RELAY = os.environ.get("ARBOS_RELAY_URL", "https://relay.arbos.life")
DIR = Path(os.environ.get("AXONRY_ARBOS_DIR", "~/.config/axonry/arbos")).expanduser()
DEVICE = "axonry-orchestrator"


def tokens_path(account: str) -> Path:
    return DIR / account / "tokens.json"


def arbosctl_config(account: str) -> Path:
    return DIR / account / "arbosctl.json"


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "axonry-orchestrator"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url}: {e.code} {e.read()[:300]!r}") from e


def _write_private(path: Path, doc: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _save(account, relay, resp):
    doc = {"relay": relay, "access_token": resp["access_token"], "refresh_token": resp["refresh_token"],
           "expires_at": int(time.time()) + int(resp.get("expires_in", 3600)),
           "device_id": resp.get("device_id") or load(account).get("device_id", ""),
           "user": (resp.get("user") or {}).get("login") or (resp.get("user") or {}).get("handle")
           or load(account).get("user")}
    _write_private(tokens_path(account), doc)
    _write_private(arbosctl_config(account), {"relay_url": relay, "device_token": doc["access_token"],
                                     "device_id": doc["device_id"]})
    return doc


def load(account):
    p = tokens_path(account)
    return json.loads(p.read_text()) if p.exists() else {}


def login_url(relay=RELAY, port=53682, device=DEVICE):
    redirect = f"http://127.0.0.1:{port}/callback"
    return f"{relay}/auth/github/start?" + urllib.parse.urlencode({"redirect": redirect, "device_name": device})


def exchange(account, code, relay=RELAY):
    return _save(account, relay, _post(f"{relay}/auth/token", {"grant_code": code}))


def complete(account, redirected_url, relay=RELAY):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(redirected_url).query)
    if "error" in q:
        raise RuntimeError(f"sign-in failed: {q.get('error')} {q.get('message')}")
    return exchange(account, q["code"][0], relay)


def ensure_fresh(account, margin=600):
    """Refresh the pair when the access token expires within `margin` seconds. Returns the token doc. Several
    processes refresh the same account: the lock makes one rotate the pair and the others read the new one."""
    if not tokens_path(account).exists():
        raise RuntimeError(f"arbos account {account}: not signed in "
                           f"(python -m axonry.agent.arbos.auth --account {account} url / complete)")
    with open(tokens_path(account).with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = load(account)
        if doc["expires_at"] - time.time() > margin:
            return doc
        return _save(account, doc["relay"], _post(f"{doc['relay']}/auth/refresh",
                                                  {"refresh_token": doc["refresh_token"]}))


def import_accounts(path, relay=RELAY):
    """JSON lines {"handle", "tokens": {access_token, refresh_token, device_id, user}} -> one account per handle."""
    names = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            acc = json.loads(line)
            _save(acc["handle"], relay, acc["tokens"])
            ensure_fresh(acc["handle"], margin=10**9)     # proves the pair works and sets the true expiry
            names.append(acc["handle"])
    return names


def listen(account, relay=RELAY, port=53682, timeout=1800):
    result = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                if "code" not in q:
                    raise RuntimeError(f"no code in redirect: {q}")
                result["doc"] = exchange(account, q["code"][0], relay)
                body = b"Signed in. You can close this tab."
            except Exception as e:  # report on the page and in the terminal
                result["error"] = str(e)
                body = f"Sign-in failed: {e}".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    srv.timeout = 5
    end = time.time() + timeout
    while not result and time.time() < end:
        srv.handle_request()
    if "error" in result:
        raise RuntimeError(result["error"])
    if not result:
        raise TimeoutError("no sign-in redirect received")
    return result["doc"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="axonry-arbos-auth")
    ap.add_argument("--account", default="main")
    ap.add_argument("cmd", choices=("url", "listen", "complete", "status", "refresh", "import"))
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--relay", default=RELAY)
    ap.add_argument("--port", type=int, default=53682)
    a = ap.parse_args(argv)
    if a.cmd == "url":
        print(login_url(a.relay, a.port))
    elif a.cmd == "listen":
        print(login_url(a.relay, a.port), flush=True)
        doc = listen(a.account, a.relay, a.port)
        print(f"signed in as {doc.get('user')} (device {doc['device_id']}); token until {time.ctime(doc['expires_at'])}")
    elif a.cmd == "complete":
        doc = complete(a.account, a.arg, a.relay)
        print(f"signed in as {doc.get('user')} (device {doc['device_id']})")
    elif a.cmd == "import":
        for name in import_accounts(a.arg, a.relay):
            print(f"{name}: token until {time.ctime(load(name)['expires_at'])}")
    elif a.cmd == "refresh":
        doc = ensure_fresh(a.account, margin=10**9)
        print(f"refreshed; token until {time.ctime(doc['expires_at'])}")
    else:
        doc = load(a.account)
        print(json.dumps({k: doc.get(k) for k in ("relay", "user", "device_id", "expires_at")}) if doc else "not signed in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
