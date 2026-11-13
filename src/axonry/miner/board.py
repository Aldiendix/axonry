"""axonry-miner board: the subnet at a glance, from public data only (status.json, matches.json).

  1. Champion and queue   the champion (a season may open with an initial champion held by uid 0), then every
                          submission in the queue, in priority order
  2. Not in queue         submissions waiting (no PR yet, fee not covered, ...) or stopped before a run (and why),
                          PRs that carry no submission (and why), and registered miners with a funded account but no
                          commitment
  3. Recent matches       the last N decisions; per benchmark "score[not-worse at, win at]" against the champion
                          of that moment (green: win, red: worse, plain: not worse)

One registration, one entry: a hotkey's first commitment is its submission for good. A uid is shown while the
hotkey is registered. Being deregistered after committing does not stop a submission; it only means no weight: a
deregistered champion's weight is burned (uid 0).
"""
import os
import re
import sys
import time
import unicodedata

from ..common import layout

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, RED, YELLOW, CYAN, MAGENTA = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[35m"
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ANSI = re.compile(r"\033\[[0-9;]*m|\033\]8;;[^\033]*\033\\")

STAGE = {   # status -> (label, colour)
    "REVIEW": ("in review", YELLOW), "QUEUED": ("queued", CYAN), "RUNNING": ("running", MAGENTA),
    "COLLECTING": ("collecting", MAGENTA), "ADMITTED": ("evaluating", MAGENTA),
    "SCORED": ("scored", MAGENTA)}
WHY = {     # reason code -> short label
    "NO_PR": "no PR yet", "UNCOMMITTED": "not committed", "PARTICIPATED": "hotkey used",
    "INSUFFICIENT_FUNDS": "insufficient funds", "COPY": "copy",
    "HOTKEY_UNKNOWN": "hotkey unknown", "BAD_PR": "bad PR", "BAD_ENTRY": "bad entry.json",
    "BASE_NOT_ALLOWED": "wrong base", "BAD_PARENT": "bad parent", "REVIEW_CHANGES": "review: rejected",
    "CONFLICT": "conflict"}


def clean(text, limit=None):
    """Miner-written text (titles, reasons) never reaches the terminal with control characters."""
    t = _CTRL.sub(" ", str(text or "")).strip()
    return t if limit is None or len(t) <= limit else t[:limit - 1] + "…"


def width(s):
    s = _ANSI.sub("", s)
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


class Style:
    def __init__(self, color):
        self.color = color

    def __call__(self, text, *codes):
        codes = [c for c in codes if c]
        return "".join(codes) + text + RESET if self.color and codes else text

    def link(self, text, url):
        return f"\033]8;;{url}\033\\{text}\033]8;;\033\\" if self.color and url else text


def table(title, headers, rows, st, right=()):
    """Aligned table; cells may carry colour and links (measured without them)."""
    if not rows:
        return f"{st(title, BOLD)}\n  (none)\n"
    ws = [max(width(h), *(width(r[i]) for r in rows)) for i, h in enumerate(headers)]

    def line(cells, head=False):
        out = []
        for i, c in enumerate(cells):
            pad = " " * (ws[i] - width(c))
            out.append(pad + c if i in right else c + pad)
        text = "  ".join(out).rstrip()
        return "  " + (st(text, BOLD) if head else text)
    rule = "  " + st("─" * (sum(ws) + 2 * (len(ws) - 1)), DIM)
    return "\n".join([st(title, BOLD), line(headers, True), rule, *(line(r) for r in rows)]) + "\n"


def board(status: dict, matches: dict | None, n=20, color=None) -> str:
    st = Style(sys.stdout.isatty() and not os.environ.get("NO_COLOR") if color is None else color)
    miners = {m["hotkey"]: m for m in status.get("miners", [])}
    repo = status.get("repo")

    def uid(hk):
        m = miners.get(hk)
        return str(m["uid"]) if m else st("-", DIM)

    def hot(hk):
        return hk[:10] + "…" if hk else "-"

    def pr(n_, title):
        if not n_:                     # no PR: the initial champion, shown by its description
            return clean(title, 28) if title else st("-", DIM)
        url = f"https://github.com/{repo}/pull/{n_}" if repo else None
        return st.link(f"#{n_}", url) + (" " + clean(title, 28) if title else "")

    # 1. champion and queue
    rows = []
    champ = status.get("champion")
    if champ:
        online = champ["hotkey"] in miners
        if champ.get("initial"):
            detail = "the season's initial champion: weight burned (uid 0)"
        else:
            detail = "receives all weight" if online else st("deregistered: weight burned (uid 0)", RED)
        rows.append([st("★", YELLOW), uid(champ["hotkey"]) if champ["hotkey"] else "0", hot(champ["hotkey"]),
                     champ["sid"], pr(champ.get("pr"), champ.get("title")), st("CHAMPION", BOLD, YELLOW), detail])
    else:
        rows.append([st("★", YELLOW), st("-", DIM), "-", "-", "base model", st("CHAMPION", BOLD, YELLOW),
                     "no champion yet: weight burned (uid 0)"])
    queue = sorted(status.get("running", []) + status.get("evaluating", []) + status.get("queue", [])
                   + status.get("review", []), key=lambda x: tuple(x.get("priority") or (1 << 62, 0)))
    for i, x in enumerate(queue, 1):
        label, col = STAGE.get(x["status"], (x["status"].lower(), ""))
        detail = {"RUNNING": f"agent at work until {_ts(x.get('deadline'))} UTC",
                  "QUEUED": f"position {x.get('position', '-')}: waiting for a machine",
                  "REVIEW": "waiting for the reviewer", "COLLECTING": "run over: model upload and admission",
                  "ADMITTED": "benchmarks on the evaluation machine",
                  "SCORED": "decided once every earlier submission is"}.get(x["status"], "")
        rows.append([str(i), uid(x["hotkey"]), hot(x["hotkey"]), x["sid"], pr(x["pr"], x.get("title")),
                     st(label, col), detail])
    out = [table("1. Champion and queue (priority order)", ["#", "UID", "HOTKEY", "SID", "PR", "STATUS", "DETAIL"],
                 rows, st, right=(0, 1))]

    # 2. not in queue
    rows, seen = [], set()
    for x, col in [(w, YELLOW) for w in status.get("waiting", [])] + [(w, RED) for w in status.get("stopped", [])]:
        seen.add(x["hotkey"])
        rows.append([uid(x["hotkey"]), hot(x["hotkey"]), x["sid"], pr(x["pr"], x.get("title")),
                     st(WHY.get(x["reason_code"], (x["reason_code"] or "").lower()), col), clean(x.get("reason"), 70)])
    for w in status.get("prs", []):
        rows.append([uid(w["hotkey"]) if w.get("hotkey") else st("-", DIM), hot(w.get("hotkey")), st("-", DIM),
                     pr(w["pr"], w.get("title")), st(WHY.get(w["reason_code"], w["reason_code"]), DIM),
                     clean(w.get("reason"), 70)])
    active = ({x["hotkey"] for x in queue} | {c["hotkey"] for c in status.get("contents", [])}
              | ({champ["hotkey"]} if champ else set()))       # hotkeys that took part (one entry each)
    accounts = status.get("accounts", {})
    for m in status.get("miners", []):
        bal = (accounts.get(m.get("coldkey")) or {}).get("available_usd", 0)
        if m["hotkey"] not in seen | active and bal > 0:
            rows.append([str(m["uid"]), hot(m["hotkey"]), st("-", DIM), st("-", DIM), st("not committed", DIM),
                         f"account: {bal:.2f} USD available"])
    out.append(table("2. Not in queue (full reason: axonry-miner show SID)", ["UID", "HOTKEY", "SID", "PR", "STATUS",
                                                                           "DETAIL"], rows, st, right=(0,)))

    # 3. recent matches
    decided = sorted((r for r in (matches or {}).get("rows", []) if r.get("scores")),
                     key=lambda r: r.get("decided_at") or 0, reverse=True)[:n]
    benches = list(dict.fromkeys(k for r in decided for k in r["scores"]))
    rows = []
    for r in decided:
        cells = []
        for k in benches:
            acc, tie_at, win_at, verdict = r["scores"].get(k, [None] * 4)
            text = "-" if acc is None else f"{acc:.1f}[{tie_at:.1f},{win_at:.1f}]"
            cells.append(st(text, {"win": GREEN, "lose": RED}.get(verdict, "")))
        won = r.get("decision") == "NEW_CHAMPION"
        rows.append([_ts(r.get("decided_at")), uid(r["hotkey"]), hot(r["hotkey"]), r["sid"], pr(r.get("pr"), None),
                     "base" if (r.get("reference") or "").startswith("base:") else (r.get("reference") or "-"), *cells,
                     st("NEW CHAMPION", BOLD, GREEN) if won else st(f"no ({r.get('wins', 0)} wins)", DIM)])
    heads = ["DECIDED (UTC)", "UID", "HOTKEY", "SID", "PR", "VS", *(k.split("@")[0] for k in benches), "RESULT"]
    out.append(table(f"3. Recent matches (last {n}): score[not-worse at, win at] vs the champion of the moment",
                     heads, rows, st, right=(1,)))
    out.append(st(f"status {_ts(status.get('updated_at'))} UTC · season {status.get('season', '-')} · a uid is shown "
                  "while the hotkey is registered", DIM))
    return "\n".join(out)


def _ts(t):
    return time.strftime("%m-%d %H:%M", time.gmtime(t)) if t else "-"


def cmd_board(reader, n=20, color=None, watch=0):
    """watch: redraw every that many seconds (the subnet publishes its status about once a minute)."""
    while True:
        status = reader.get(layout.STATUS)
        season = status and (status.get("season") or reader.current_season())
        text = board(status, reader.get(layout.season_file(season, "matches.json")) if season else None, n, color) \
            if status else "no status published yet"
        print(("\033[H\033[2J" if watch else "") + text, flush=True)
        if not watch:
            return
        time.sleep(watch)
