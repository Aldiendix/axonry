"""All GPU machines: the hosts we own first (pool/owned.py), then machines rented on vast.ai (pool/vast.py) when that is
enabled. One table holds both; each row is driven by the pool it came from."""
from .machine import MachineError, sample
from .owned import OWNED


class NoMachine(MachineError):
    """No machine is free (the owned hosts are full and vast.ai is disabled): wait for one, nothing failed."""


class Machines:
    def __init__(self, db, key: str, owned=None, vast=None, vast_max_gpus: int | None = None):
        self.db, self.key, self.owned, self.vast, self.vast_max_gpus = db, key, owned, vast, vast_max_gpus

    def _of(self, row):
        return self.owned if row["provider_id"].startswith(OWNED) else self.vast

    def get(self, purpose) -> dict | None:
        return next((m for m in self.db.machines() if m["name"] == purpose), None)

    def active(self) -> list:
        return self.db.machines()

    def machine(self, row):
        return self._of(row).machine(row)

    def rent(self, purpose: str, gpu_name: str, gpus: int):
        if self.owned and self.owned.rent(purpose, gpu_name, gpus):
            return
        if self.vast is None:
            raise NoMachine(f"no owned machine free for {purpose} ({gpus} x {gpu_name}) and vast.ai is disabled")
        used = sum(m["gpus"] for m in self.db.machines() if not m["provider_id"].startswith(OWNED))
        if self.vast_max_gpus is not None and used + gpus > self.vast_max_gpus:
            raise NoMachine(f"no owned machine free for {purpose} and vast.ai is at {used} of {self.vast_max_gpus} GPUs")
        self.vast.rent(purpose, gpu_name, gpus)

    def advance(self, purpose) -> str:
        row = self.get(purpose)
        return "gone" if row is None else self._of(row).advance(purpose)

    def release(self, purpose, note="released"):
        row = self.get(purpose)
        if row is not None:
            self._of(row).release(purpose, note)

    def hand_over(self, purpose, public_key: str, deadline: int):
        return self._of(self.get(purpose)).hand_over(purpose, public_key, deadline)

    def sample(self, purpose) -> dict:
        row = self.get(purpose)
        return sample(self.machine(row), self.key)

    def probe(self, purpose) -> dict:
        return self._of(self.get(purpose)).probe(purpose)

    def vanished(self, purpose) -> bool:
        row = self.get(purpose)
        return row is not None and self._of(row).vanished(purpose)
