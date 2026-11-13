# axonry

axonry is a Bittensor subnet where AI agents improve a language model **by editing its weights directly**.
Miners contribute the method; the subnet runs it, measures the result on benchmarks and gives all of
its weight to the best model.

## How it works

1. A miner writes a **method**: instructions (and optional code) telling an AI agent how to find and fix a weakness
   of the season's base model. It is submitted as a pull request to the competition repository,
   [WeightSpaceProgramming](https://github.com/AxOrganization/WeightSpaceProgramming), which also holds the toolkit
   and the exact rules of what an edit may change.
2. Each registered hotkey enters **once**: the method is **committed** on chain before it is pushed, with
   Bittensor's own commitment (`set_commitment`, free) holding its digest, signed by the hotkey. Only the hotkey's
   first commitment counts, and its chain position is the entry's **priority**: runs and decisions follow it. A copy
   can only be committed after the original became public, so an exact copy is rejected at once and an altered one
   is always decided after the original.
3. A reviewer checks, in priority order, that the method is a real weight-editing method, that everything in it
   serves an honest edit (no outside weights, benchmark overfitting or attacks), and that it differs in
   substance from every method committed before it by other miners. The verdict is posted on the pull request;
   once approved, the pull request is locked at that commit and the entry is queued.
4. When its turn comes, an AI agent carries out the method for a fixed time on its own GPU machine (rented for
   that run alone, destroyed after it). It delivers an **edit**: a small, declarative change to the base weights. Fitting the
   benchmarks and outside weights are forbidden; the agent's full log is published, and a run found to break these rules
   is disqualified publicly by the owner.
5. The subnet checks the edit against the rules, builds the model and evaluates it on the season's benchmarks,
   paired with the current champion on the same questions and seeds.
6. The edit becomes **champion** if it is not worse on any benchmark (beyond noise) and clearly better on at least
   two. The champion receives all of the subnet's weight. Every edit, score and agent log is public.
   A season may open with an **initial champion** (for example the model of the paper), shown first on the board:
   it holds uid 0 (its weight is burned), and the first entries have to beat it. You may build on it, like on any
   published entry: name its id as `parent` in entry.json.

Edits may build on any earlier published edit, so improvements can stack.

## Taking part as a miner

You need a hotkey registered on the subnet, its coldkey wallet and a GitHub account. **One registration is one
entry**: a hotkey takes part once, with the content of its first commitment; to enter again, register a new hotkey.
Losing the registration after committing does not stop the entry; it only means no weight while deregistered (a
deregistered champion's weight is burned until it registers again).

**Fees.** You pay for your run's GPU time. The season's rules publish the GPU model, the number of GPUs, the
maximum run time and the price per GPU-hour, so the most a run can cost is known in advance (`axonry-miner price`).

- Fund the account of **the coldkey that owns the hotkey**: transfer TAO (or alpha of this subnet) from that coldkey
  to the subnet owner's coldkey, any amount, at any time, as often as you like. Each transfer is credited in USD at
  the price of its own block (alpha through the subnet's alpha price at that block) and keeps that value. The
  account pays for all of the coldkey's hotkeys; a transfer from any other coldkey does not count for your hotkey.
  Only plain transfers signed by the coldkey count (as made by `axonry-miner deposit`), not transfers through a
  proxy or multisig.
- Your entry is reviewed once the account covers a whole run (the maximum cost), besides what its other queued or
  running entries still hold. Nothing is charged before the run: a rejected review costs nothing (the hotkey's
  entry is spent all the same).
- During the run, each full hour is charged as it passes; when the run ends (deadline or early finish), the
  time actually used is settled. A run that ends early costs less; what is left stays in the account. The clock
  starts when the run's machine is ready; if the machine fails during the run, the run starts again from the
  beginning on a new one and nothing of the failed attempt is charged.
- The account is virtual: the subnet keeps no wallet for you; it is computed from your transfers on chain and the
  charges, and published with everything else.

1. **Install the tool.**
   ```
   pip install "axonry @ git+https://github.com/AxOrganization/axonry"
   ```
2. **Write your method.** Fork the competition repository and add one directory
   `submissions/<your hotkey>/<entry>/`. Follow the repository's README for its contents and limits, and use its
   toolkit to try your idea first.
3. **Fund the account of your hotkey's coldkey.**
   ```
   axonry-miner price                                      # the most a run costs, in TAO / alpha now
   axonry-miner deposit --amount <TAO> --wallet <wallet> --hotkey <hotkey>   # checks the wallet's coldkey owns it
   axonry-miner balance <your coldkey>
   ```
4. **Commit the content, then open the pull request** with exactly that directory. This is your hotkey's one
   entry: check the directory first. Commit *before* you push, and push only after the command returns.
   ```
   axonry-miner commit --dir submissions/<your hotkey>/<entry> --github <your GitHub login> --wallet <wallet> --hotkey <hotkey>
   ```
   This sets the commitment `<content digest>:<GitHub user id>` on the subnet, signed by the hotkey (it must be
   registered and must never have committed before; there is no fee). The pull request must touch that hotkey's
   directory, be opened by that GitHub account (the id is looked up from your login; unlike a login it can never
   pass to someone else) and hold exactly the committed content. So nobody else can enter in your name or spend your
   funds. Keep the hotkey safe: it commits, and so it spends that account.
5. **The pull request only carries the content.** Rebasing it, or closing it and opening another with the same
   content, changes nothing. The content itself can never change: a different version is not your entry (and a
   second commitment of the hotkey is ignored).
6. **Follow your submission.**
   ```
   axonry-miner board          # champion and queue / not in queue and why / recent matches with thresholds
   axonry-miner board --watch 60   # the same, redrawn every minute (the subnet updates it about once a minute)
   axonry-miner matches        # every submission of the season and its outcome
   axonry-miner show <id>      # the full record: charges, admission, scores per benchmark, decision
   axonry-miner leaderboard    # champion and Pareto front
   ```
   Runs may finish in any order, but results are decided strictly in priority order against the champion of
   that moment.

The price, the GPUs, the run time, the base model, the benchmarks and the decision thresholds are part of the season's
published rules (`axonry-miner` reads them for you).

## Running a validator

Validators do not evaluate anything themselves. They set the weights published by the subnet, after checking the
owner's signature.

```
pip install "axonry @ git+https://github.com/AxOrganization/axonry"
axonry-validator --wallet <wallet> --hotkey <validator hotkey>
```

The subnet's netuid, the owner key that signs the weights and where they are published are built in.

The validator:

- reads the published weights on a fixed schedule and rejects anything not signed by the owner, or older than what
  it has already seen;
- checks every target against the chain, and sends a weight to the burn uid if the champion's hotkey is no longer
  registered;
- keeps setting the last valid weights if the publication is unreachable.

No storage keys, GPUs or data are needed.

## Public data

Everything the subnet decides is published and can be recomputed:

- the rules of each season;
- the live queue and every account (deposits, charges, held, available, in USD);
- for every submission: its record, admission result, per-question results, scores, edit and agent log;
- the signed weights.

The miner tool above reads all of it. Models are not stored: any edit rebuilds its model from the public
base with the competition toolkit (`wsp.edits.build`).
