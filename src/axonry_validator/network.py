"""The subnet's published constants, per chain network: where the public data lives, which hotkey signs the weights,
which netuid. They ship with the code so validators and miners never type them; the orchestrator refuses to run if
its own netuid or signing key disagree with them. Unset values mean "not launched on this network yet".
"""

NETWORKS = {
    "finney": {"netuid": 99, "owner": "5Gc37JvSDmMt9MdfALfVkDWwAaGtygc2Vs4dJvbcc3h9QPGf",
               "public_url": "https://storage.axonry.ai"},
    "test": {"netuid": None, "owner": None, "public_url": None},
}


class NotLaunched(SystemExit):
    pass


def get(network: str = "finney") -> dict:
    if network not in NETWORKS:
        raise NotLaunched(f"unknown network {network!r} (known: {', '.join(NETWORKS)})")
    n = NETWORKS[network]
    missing = [k for k, v in n.items() if v is None]
    if missing:
        raise NotLaunched(f"axonry is not launched on {network} yet ({', '.join(missing)} not published)")
    return dict(n)


def weights_url(network: str = "finney") -> str:
    return get(network)["public_url"].rstrip("/") + "/weights.json"
