"""axonry-miner: everything a miner needs, from the public side only.

Read (no keys; reads the public domain):
  axonry-miner matches [--season S] [--hotkey HK] [--status STATUS]   one line per submission, incl. rejection codes
  axonry-miner show SID                                                 fee, timeline, admission violations,
                                                                     per-benchmark d/SE/not-worse/win, decision
  axonry-miner leaderboard [--season S]                                 champion and Pareto front
  axonry-miner weights                                                  current weights.json, signature checked
  axonry-miner board [-n 20] [--no-color] [--watch 60]                  champion and queue / not in queue (and why) /
                                                                     recent matches with per-benchmark thresholds
  axonry-miner balance COLDKEY                                          your account in USD: deposits, charged, held, available
Submit:
  axonry-miner digest DIR                                               content digest of a submission directory
  axonry-miner price                                                    what a run costs at most, in TAO / alpha now
  axonry-miner deposit --amount X --wallet W --hotkey H [--asset tao|alpha] [--yes]
                                                                     transfer from the coldkey owning H to the subnet;
                                                                     credited in USD at that block's price to that
                                                                     coldkey's account, which pays H's run
  axonry-miner commit --dir DIR --github LOGIN --wallet W --hotkey H [--yes]
                                                                     the entry of hotkey H, once: set_commitment
                                                                     "<digest>:<GitHub id of LOGIN>" signed by H
                                                                     (registered on the subnet; no fee); only a PR by
                                                                     LOGIN, in H's directory, with exactly this
                                                                     content can carry it
One registration is one entry: only a hotkey's first commitment counts, and the content and GitHub account it names
can never change. Commit first and push only after the command returns: a content belongs to its earliest commitment,
and content pushed before it is committed can be copied and committed first by others. The run is paid by the
GPU-hour from the account of the coldkey owning the hotkey: deposit from that coldkey, as plain transfers (this
tool), not via proxies or multisig. The account must cover a whole run before review; each full hour is charged as
it passes and the rest when the run ends. A rejected review costs nothing, but the hotkey's entry is spent.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from ..common import layout
from ..common.digest import content_digest
from ..common.ids import make_commitment

def public_url(network="finney"):
    """The subnet's published data location (axonry_validator.network); AXONRY_PUBLIC_URL overrides it for testing."""
    from axonry_validator import network as published  # noqa: PLC0415
    return os.environ.get("AXONRY_PUBLIC_URL") or published.get(network)["public_url"]


class PublicReader:
    def __init__(self, base_url=None, store=None):
        self.base, self.store = (base_url or "").rstrip("/"), store

    def get(self, key):
        if self.store is not None:
            data = self.store.get(key)
            return json.loads(data) if data else None
        try:
            req = urllib.request.Request(f"{self.base}/{key}", headers={"User-Agent": "axonry-miner/1",
                                                                       "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def current_season(self):
        idx = self.get(layout.INDEX)
        return idx["current_season"] if idx else None

    def rules(self):
        """The rules version in force (index.json names it)."""
        idx = self.get(layout.INDEX)
        if not idx:
            raise SystemExit("the subnet has not published its rules yet")
        return self.get(idx["rules"])


def _ts(t):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)) if t else "-"


def _who(hotkey):
    return hotkey or "uid0 (initial)"


def cmd_matches(r, season, hotkey, status):
    season = season or r.current_season() or "s1"
    doc = r.get(layout.season_file(season, "matches.json"))
    if not doc:
        print(f"no matches published for season {season}")
        return
    rows = [x for x in doc["rows"] if (not hotkey or x["hotkey"] == hotkey)
            and (not status or x["status"].lower() == status.lower())]
    print(f"season {season}: {len(rows)} submissions (details: axonry-miner show SID)")
    print(f"{'SID':<16} {'HOTKEY':<16} {'PR':>4}  {'STATUS':<10} {'REASON / DECISION':<18} {'WINS':>4}  DECIDED (UTC)")
    for x in rows:
        hk = x["hotkey"][:14] + "…" if x["hotkey"] else _who(None)
        print(f"{x['sid']:<16} {hk:<16} {('#' + str(x['pr'])) if x.get('pr') else '-':>4}  {x['status']:<10} "
              f"{(x.get('reason_code') or '-'):<18} {x['wins'] if x.get('wins') is not None else '-':>4}  "
              f"{_ts(x.get('decided_at'))}")


def _indent(text, pad="           "):
    return ("\n" + pad).join(str(text).splitlines())


def cmd_show(r, sid):
    sub = r.get(layout.submission(sid, "submission.json"))
    if not sub:
        print(f"{sid}: not published yet")
        return
    print(f"submission {sid}: {sub['status']}" + (f" {sub['reason_code']}" if sub.get("reason_code") else ""))
    print(f"  hotkey   {_who(sub.get('hotkey'))}" + (f"  (coldkey {sub['coldkey']})" if sub.get("coldkey") else ""))
    if sub.get("reason"):
        print(f"  reason   {_indent(sub['reason'])}")
    pr = sub.get("pr") or {}
    facts = [f"PR #{pr['number']}" if pr.get("number") else None,
             f"locked commit {pr['locked_commit'][:12]}" if pr.get("locked_commit") else None,
             f"base {sub['base']}" if sub.get("base") else None, f"parent {sub['parent']}" if sub.get("parent") else None]
    if any(facts):
        print("  entry    " + " · ".join(f for f in facts if f))
    fee = sub.get("fee") or {}
    if fee:
        print(f"  fee      {fee['usd_per_gpu_hour']} USD per GPU-hour x {fee['gpus']} GPU, at most {fee['max_usd']:.2f} "
              f"USD; charged {fee.get('charged_usd', 0):.2f} USD")
    tl = sub.get("timeline") or {}
    events = sorted(((v, k) for k, v in tl.items() if v and (k != "deadline" or "agent_end" not in tl)),
                    key=lambda e: e[0])     # the deadline is a plan: shown while the run is on
    for i, (t, k) in enumerate(events):
        print(f"  {'timeline' if i == 0 else '':<8} {_ts(t)}  {k.replace('_', ' ')}")
    adm = r.get(layout.submission(sid, "admission.json"))
    if adm:
        print(f"  admission {'passed' if adm['passed'] else 'REJECTED'}")
        for v in adm.get("violations") or []:
            print(f"    - {v}")
    score = r.get(layout.submission(sid, "score.json"))
    if score:
        if score["decision"] == "INITIAL_CHAMPION":
            print("  decision INITIAL_CHAMPION (the season's starting champion; it plays no match)")
            print(f"    {'benchmark':<16} {'acc':>7}")
            for k, b in score["benchmarks"].items():
                print(f"    {k:<16} {b['acc']:>7.2f}")
        else:
            ref = next(iter(score["benchmarks"].values()))["vs_champion"]["ref"]
            print(f"  decision {score['decision']} vs champion {ref}: wins {score['wins']} (needs "
                  f"{score['judge']['n_win']}), not worse on every benchmark: {'yes' if score['tie_all'] else 'no'}")
            print(f"    {'benchmark':<16} {'acc':>7} {'d':>8} {'SE':>6}  not-worse  win")
            for k, b in score["benchmarks"].items():
                c = b["vs_champion"]
                print(f"    {k:<16} {b['acc']:>7.2f} {c['d']:>+8.2f} {c['se']:>6.2f}  {'yes' if c['tie_ok'] else 'NO':<9}  "
                      f"{'win' if c['win'] else '-'}")
    if sub.get("agent_log") == "published" or adm or score:
        print(f"  files    {r.base or '<public>'}/{layout.submission(sid, '')}"
              + ("  (agent/: the agent's log)" if sub.get("agent_log") == "published" else ""))
    ruling = r.get(layout.submission(sid, "ruling.json"))
    if ruling:
        print(f"  DISQUALIFIED by the owner: {ruling['reason']}")


def cmd_leaderboard(r, season):
    season = season or r.current_season() or "s1"
    lb = r.get(layout.season_file(season, "leaderboard.json"))
    if not lb:
        print("no leaderboard yet")
        return
    c = lb.get("champion")
    who = f"{c['sid']} ({c['hotkey'] or 'initial champion, weight burned to uid 0'})" if c else \
        "none yet (weight burned to uid 0)"
    print(f"season {season} champion: {who}")
    front = lb.get("pareto_front") or []
    if front:
        keys = list(dict.fromkeys(k for m in front for k in (m.get("accuracy") or {})))
        print("Pareto front (no other submission is at least as good on every benchmark):")
        print(f"  {'SID':<16}" + "".join(f"{k.split('@')[0]:>11}" for k in keys))
        for m in front:
            acc = m.get("accuracy") or {}
            print(f"  {m['id']:<16}" + "".join(f"{acc[k]:>11.1f}" if k in acc else f"{'-':>11}" for k in keys))


def cmd_weights(r, network):
    from axonry_validator import network as published  # noqa: PLC0415

    from ..common.sign import verify_doc  # noqa: PLC0415
    w = r.get(layout.WEIGHTS)
    if not w:
        print("no weights published yet")
        return
    print(json.dumps(w, indent=1))
    print(f"signature: {'valid' if w and verify_doc(w, published.get(network)['owner']) else 'INVALID'}")


def cmd_balance(r, coldkey):
    st = r.get(layout.STATUS) or {}
    a = (st.get("accounts") or {}).get(coldkey)
    if not a:
        print(f"no deposits from {coldkey} seen yet")
        return
    for k in ("deposited", "charged", "balance", "held", "available"):
        print(f"  {k:<10} {a[k + '_usd']:.2f} USD")
    for asset, v in sorted(a["pending_rao"].items()):
        print(f"  pending    {v / 1e9:.6f} {asset} (not priced yet)")


def cmd_price(r, network):
    """What a run costs, and what that is in TAO / alpha at this moment's price."""
    from .. import pricing  # noqa: PLC0415
    rules = r.rules()
    run, top = rules["run"], pricing.run_cost_musd(rules) / pricing.MUSD
    tao_usd, sources = pricing.tao_usd_at(int(time.time()))
    alpha = _alpha_price(network, rules) if "alpha" in rules["fee"]["assets"] else None
    now = {"tao": top / tao_usd, "alpha": top / tao_usd / alpha if alpha else None}
    print(f"a run: {run['gpus']} x {run['gpu_model']} GPU, at most {run['hours']} h; "
          f"{rules['fee']['usd_per_gpu_hour']} USD per GPU-hour, "
          f"so at most {top:.2f} USD.")
    print("your account must hold that much before review (nothing is charged until the run starts; each full hour "
          "is charged as it passes and the rest when the run ends).")
    print("now " + " or ".join(f"{v:.6f} {a}" for a, v in now.items() if v and a in rules["fee"]["assets"])
          + f" (TAO/USD {tao_usd:.2f} from {', '.join(sources)}); a deposit counts in USD at the price of its block.")


def _alpha_price(network, rules):
    import bittensor as bt  # noqa: PLC0415

    from ..chain.subtensor import Chain  # noqa: PLC0415
    return Chain(subtensor=bt.Subtensor(network=network)).alpha_price(rules["netuid"], None)


def _prepare(a, r):
    """-> (subtensor, wallet, hotkey, rules, commitment, digest) after checking the directory against the season."""
    import bittensor as bt  # noqa: PLC0415
    from bittensor_wallet import Wallet  # noqa: PLC0415

    rules = r.rules()
    for f in ("entry.json", "METHOD.md"):
        if not os.path.isfile(os.path.join(a.dir, f)):
            raise SystemExit(f"{f} missing in {a.dir}")
    entry = json.load(open(os.path.join(a.dir, "entry.json")))
    if entry.get("base") != rules["base"]:
        raise SystemExit(f"entry.json base {entry.get('base')} is not this season's base {rules['base']}")
    wallet = Wallet(name=a.wallet, hotkey=a.hotkey, **({"path": a.wallet_path} if a.wallet_path else {}))
    hk = wallet.hotkey.ss58_address
    if f"submissions/{hk}/" not in os.path.abspath(a.dir).replace(os.sep, "/") + "/":
        raise SystemExit(f"the directory must be submissions/{hk}/<entry>/")
    digest = content_digest(a.dir)
    sub = bt.Subtensor(network=a.network)
    from ..chain.subtensor import Chain  # noqa: PLC0415
    chain = Chain(subtensor=sub)
    if chain.uid_of(hk, rules["netuid"], None) is None:
        raise SystemExit(f"{hk} is not registered on subnet {rules['netuid']}: only a registered hotkey can commit")
    if chain.has_committed(hk, rules["netuid"]):
        raise SystemExit(f"{hk} has committed on subnet {rules['netuid']} before: one registration is one entry "
                         "(only its first commitment counts); use a new hotkey")
    gid = github_id(a.github)
    print(f"GitHub {a.github} = user id {gid}")
    return sub, wallet, hk, rules, make_commitment(digest, gid), digest


def github_id(login):
    """The numeric id of a GitHub account (public API, no token): commitments name it, since logins can change."""
    req = urllib.request.Request(f"https://api.github.com/users/{login}", headers={"User-Agent": "axonry-miner"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(json.loads(r.read())["id"])
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"cannot look up GitHub user {login!r}: {e}") from None


def cmd_commit(a, r):
    from ..chain.subtensor import submit_commitment  # noqa: PLC0415

    sub, wallet, hk, rules, commitment, digest = _prepare(a, r)
    print(f"digest {digest}\ncommitting {commitment}\nThis is the one entry of hotkey {hk}: the content and the GitHub "
          "account can never change afterwards.")
    if not a.yes and input("proceed? [y/N] ").strip().lower() != "y":
        return
    blk, i = submit_commitment(sub, wallet, rules["netuid"], commitment)
    print(f"committed in block {blk} extrinsic {i} (this content's priority). Now push it to your PR, opened by "
          f"{a.github}. Never push content before its commitment is in a block: whoever commits it first owns it.")


def cmd_deposit(a, r):
    """Pay for a hotkey: a transfer from the coldkey that owns it (that hotkey's run is charged to the account of its
    owning coldkey; a deposit from any other coldkey does not count for it)."""
    import bittensor as bt  # noqa: PLC0415
    from bittensor_wallet import Wallet  # noqa: PLC0415

    from .. import pricing  # noqa: PLC0415
    from ..chain.subtensor import Chain, submit_deposit  # noqa: PLC0415

    rules = r.rules()
    if a.asset not in rules["fee"]["assets"]:
        raise SystemExit(f"{a.asset} is not accepted (accepted: {', '.join(rules['fee']['assets'])})")
    wallet = Wallet(name=a.wallet, hotkey=a.hotkey, **({"path": a.wallet_path} if a.wallet_path else {}))
    sub = bt.Subtensor(network=a.network)
    hk, ck = wallet.hotkey.ss58_address, wallet.coldkeypub.ss58_address
    owner = Chain(subtensor=sub).owner_of(hk, None)
    if owner != ck:
        raise SystemExit(f"hotkey {hk} is owned by {owner}, not by this wallet's coldkey {ck}: pay from its owner")
    treasury, amount = rules["fee"]["treasury"], int(round(a.amount * pricing.RAO))
    staked = a.stake_hotkey or hk
    print(f"depositing {a.amount} {a.asset} from coldkey {ck} to {treasury}" +
          (f" (stake on {staked})" if a.asset == "alpha" else "") +
          f"\ncredited to the account of {ck}, which pays the runs of its hotkeys (incl. {hk})")
    if not a.yes and input("proceed? [y/N] ").strip().lower() != "y":
        return
    blk, i = submit_deposit(sub, wallet, treasury, rules["netuid"], a.asset, amount,
                            staked_hotkey=staked if a.asset == "alpha" else None)
    print(f"finalized in block {blk} extrinsic {i} (see `axonry-miner balance {ck}`)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="axonry-miner", description=__doc__.split("\n")[0])
    ap.add_argument("--network", default="finney")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("matches")
    m.add_argument("--season")
    m.add_argument("--hotkey")
    m.add_argument("--status")
    sub.add_parser("show").add_argument("sid")
    sub.add_parser("leaderboard").add_argument("--season")
    sub.add_parser("weights")
    b = sub.add_parser("board", help="champion and queue, not in queue, recent matches")
    b.add_argument("-n", type=int, default=20, help="recent matches to show")
    b.add_argument("--no-color", action="store_true")
    b.add_argument("--watch", type=int, default=0, metavar="SECONDS", help="redraw every SECONDS (e.g. 60)")
    sub.add_parser("digest").add_argument("dir")
    sub.add_parser("price")
    sub.add_parser("balance").add_argument("coldkey")
    c = sub.add_parser("commit")
    c.add_argument("--dir", required=True)
    c.add_argument("--hotkey", required=True)
    c.add_argument("--github", required=True, help="GitHub login that opens (or opened) the pull request")
    d = sub.add_parser("deposit")
    d.add_argument("--amount", type=float, required=True)
    d.add_argument("--asset", choices=("tao", "alpha"), default="tao")
    d.add_argument("--hotkey", required=True, help="the miner hotkey (wallet hotkey name) this deposit pays for")
    d.add_argument("--stake-hotkey", help="alpha: the hotkey (ss58) the stake to move sits on (default: --hotkey's)")
    for s in (c, d):
        s.add_argument("--wallet", required=True)
        s.add_argument("--wallet-path")
        s.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    r = PublicReader(public_url(a.network) if a.cmd != "digest" else None)
    if a.cmd == "matches":
        cmd_matches(r, a.season, a.hotkey, a.status)
    elif a.cmd == "show":
        cmd_show(r, a.sid)
    elif a.cmd == "leaderboard":
        cmd_leaderboard(r, a.season)
    elif a.cmd == "weights":
        cmd_weights(r, a.network)
    elif a.cmd == "board":
        from .board import cmd_board  # noqa: PLC0415
        cmd_board(r, a.n, False if a.no_color else None, a.watch)
    elif a.cmd == "digest":
        print(content_digest(a.dir))
    elif a.cmd == "price":
        cmd_price(r, a.network)
    elif a.cmd == "balance":
        cmd_balance(r, a.coldkey)
    elif a.cmd == "deposit":
        cmd_deposit(a, r)
    elif a.cmd == "commit":
        cmd_commit(a, r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
