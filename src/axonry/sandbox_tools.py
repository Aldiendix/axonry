"""Commands available to the agent on the run's machine (installed by pool/setup.sh with this package):

  axonry-time                      time left until the run's deadline (/etc/axonry-deadline, written when the
                                   machine is handed to the run)
  axonry-check OUT_DIR             the organizer's admission check on OUT_DIR (edit.json, edit.npz, derive/, REPORT.md)
Uploading is the competition toolkit's job: python -m wsp.upload <case> <edit> --task task.json (schema: the protocol's upload.schema).
"""
import argparse
import json
import sys
import time
from pathlib import Path



def time_left(_a):
    p = Path("/etc/axonry-deadline")
    dl = int(p.read_text().strip()) if p.exists() else 0
    left = dl - int(time.time())
    print(f"{max(left, 0) // 3600} h {max(left, 0) % 3600 // 60} min left (deadline "
          f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(dl))})" if dl else "no deadline set on this machine")


def check(a):
    from .evaluation.admit_remote import admit  # noqa: PLC0415

    task = json.loads(Path(a.task).read_text()) if a.task else {}
    res = admit(a.out_dir, a.base_dir, task.get("base") or a.base_id)
    print(json.dumps(res, indent=1))
    return 0 if res["passed"] else 1


def main(argv=None):
    prog = Path(sys.argv[0]).name
    ap = argparse.ArgumentParser(prog=prog)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("time")
    c = sub.add_parser("check")
    c.add_argument("out_dir")
    c.add_argument("--task")
    c.add_argument("--base-dir", default="/workspace/base")  # the verified base, read-only
    c.add_argument("--base-id")
    argv = list(sys.argv[1:] if argv is None else argv)
    alias = {"axonry-time": "time", "axonry-check": "check"}.get(prog)
    if alias:
        argv = [alias, *argv]
    a = ap.parse_args(argv)
    return {"time": time_left, "check": check}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
