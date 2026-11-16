"""A run's agent log is published; the run's secrets are not. Before the exported log (chat, audit) is published, every
secret the run was given is replaced: the sandbox's private key and addresses (an owned host's address stays valid
after the run), and the R2 credentials and account. They are read from the run's task bundle, the only place they were
ever written (the database keeps the task without them). Any private key block is removed as well, whatever its
origin.
"""
import json
import re
import tarfile
from pathlib import Path

MARK = "[redacted]"
KEY_BLOCK = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S)


def secrets_of(task: dict, private_key: str = "") -> list:
    sb, r2 = task.get("sandbox") or {}, task.get("r2") or {}
    found = [private_key, *private_key.splitlines(), sb.get("host"), (sb.get("jump") or {}).get("host"),
             r2.get("access_key_id"), r2.get("secret_access_key"), r2.get("session_token"), r2.get("endpoint")]
    account = re.match(r"https://([0-9a-f]{16,})\.", r2.get("endpoint") or "")
    if account:
        found.append(account.group(1))
    # long values first (a key line inside the whole key); short ones would hit ordinary text
    return sorted({s.strip() for s in found if s and len(s.strip()) >= 7 and s.strip() not in ("127.0.0.1",)},
                  key=len, reverse=True)


def secrets_in_bundle(bundle: Path) -> list:
    with tarfile.open(bundle) as tf:
        task = json.load(tf.extractfile("task.json"))
        key = tf.extractfile("ssh/id_sandbox").read().decode()
    return secrets_of(task, key)


def redact(text: str, secrets) -> str:
    text = KEY_BLOCK.sub(MARK, text)
    for s in secrets:
        text = text.replace(s, MARK)
    return text


def redact_dir(d: Path, secrets):
    for p in sorted(Path(d).rglob("*")):
        if p.is_file():
            raw = p.read_text(errors="replace")
            clean = redact(raw, secrets)
            if clean != raw:
                p.write_text(clean)
