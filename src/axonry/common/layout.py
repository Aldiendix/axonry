"""The storage layout: the only place object keys are spelled.

Public bucket (served read-only through the public domain) and private bucket. Every other module asks this module
for a key, so a path never drifts between writer, reader, validator and miner CLI.
"""

# ---- public bucket -------------------------------------------------------------------------------------------
WEIGHTS = "weights.json"          # fixed forever: the validator's only input
INDEX = "index.json"              # fixed forever: discovery entry point
STATUS = "status.json"


def base_def(base_id: str) -> str:
    return f"bases/{base_id}.json"


def benchmark_def(bench_id: str, version: int) -> str:
    return f"benchmarks/{bench_id}@{version}.json"


def season_rules_version(season: str, rules_sha256: str) -> str:
    return f"seasons/{season}/rules/{rules_sha256}.json"


def season_file(season: str, name: str) -> str:
    if name not in {"leaderboard.json", "matches.json"}:
        raise ValueError(f"unknown season file {name}")
    return f"seasons/{season}/{name}"


def submission(sid: str, name: str = "") -> str:
    return f"submissions/{sid}/{name}" if name else f"submissions/{sid}/"


def submission_results(sid: str, bench_key: str) -> str:
    return f"submissions/{sid}/results/{bench_key}.jsonl.gz"


def submission_edit_prefix(sid: str) -> str:
    return f"submissions/{sid}/edit/"


def submission_agent_prefix(sid: str) -> str:
    return f"submissions/{sid}/agent/"


# ---- private bucket ------------------------------------------------------------------------------------------
def staging_prefix(sid: str) -> str:
    """The only prefix a run's temporary R2 credentials may touch."""
    return f"staging/{sid}/"


STATE_PREFIX = "state/"

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
MUTABLE_CACHE = "public, max-age=60"
MUTABLE_PUBLIC = {WEIGHTS, INDEX, STATUS}


def is_mutable(key: str) -> bool:
    if key in MUTABLE_PUBLIC:
        return True
    parts = key.split("/")
    return len(parts) == 3 and parts[0] == "seasons" and parts[2] in {
        "leaderboard.json", "matches.json"}
