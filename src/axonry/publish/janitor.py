"""Retention: champion models (ever) are kept forever; other models are deleted retention_days after
their decision. Only submissions/<sid>/model/ is ever deleted; records, scores, results and edit specs stay."""
import time

from ..common import layout

DAY = 86400


def due(rows, ever_champions: set, retention_days: int, now=None):
    """rows: [{"sid", "decided_at", "model_available"}] -> sids whose model must be deleted now."""
    now = now or time.time()
    return [r["sid"] for r in rows if r.get("model_available") and r["sid"] not in ever_champions
            and r.get("decided_at") is not None and now - r["decided_at"] >= retention_days * DAY]


def run(store, rows, ever_champions: set, retention_days: int, on_deleted, now=None):
    deleted = []
    for sid in due(rows, ever_champions, retention_days, now):
        if sid in ever_champions:      # re-check right before deleting
            continue
        store.delete_prefix(layout.submission_model_prefix(sid))
        on_deleted(sid)
        deleted.append(sid)
    return deleted
