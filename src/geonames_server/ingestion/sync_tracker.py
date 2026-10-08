"""In-memory sync status tracker for the /sync API endpoint.

Stores sync state keyed by UUID. Each sync tracks the current pipeline
step and per-dataset status so callers can poll ``GET /sync/{sync_id}``
for progress.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

MAX_ENTRIES = 50

_lock = threading.Lock()
_syncs: dict[str, SyncStatus] = {}


# ---------------------------------------------------------------------------
# Enums / dataclasses
# ---------------------------------------------------------------------------


class SyncStep(str, Enum):
    STARTING = "starting"
    CHECKING_REMOTE_METADATA = "checking_remote_metadata"
    DOWNLOADING_MODIFICATIONS = "downloading_modifications"
    DOWNLOADING_DELETES = "downloading_deletes"
    INGESTING_MODIFICATIONS = "ingesting_modifications"
    INGESTING_DELETES = "ingesting_deletes"
    APPLYING_DATASETS = "applying_datasets"
    SYNCING_METADATA = "syncing_metadata"
    COMPLETED = "completed"
    FAILED = "failed"
    IN_SYNC = "in_sync"


class OverallStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DatasetSyncStatus(str, Enum):
    PENDING = "pending"
    APPLYING_MODIFICATIONS = "applying_modifications"
    APPLYING_DELETES = "applying_deletes"
    SYNCED = "synced"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class DatasetStatus:
    name: str
    last_modified: datetime | None = None
    out_of_sync: bool | None = None
    needs_rebuild: bool | None = None
    status: str = DatasetSyncStatus.PENDING.value


@dataclass
class SyncStatus:
    sync_id: str
    status: str = OverallStatus.RUNNING.value
    current_step: str = SyncStep.STARTING.value
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    datasets: list[DatasetStatus] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_sync() -> str:
    """Create a new sync entry and return its id. Prunes old entries."""
    sync_id = uuid.uuid4().hex
    with _lock:
        _prune()
        _syncs[sync_id] = SyncStatus(sync_id=sync_id)
    return sync_id


def get_sync(sync_id: str) -> SyncStatus | None:
    with _lock:
        return _syncs.get(sync_id)


def is_running() -> bool:
    with _lock:
        return any(s.status == OverallStatus.RUNNING.value for s in _syncs.values())


def update_step(sync_id: str, step: SyncStep) -> None:
    with _lock:
        entry = _syncs.get(sync_id)
        if entry is not None:
            entry.current_step = step.value


def set_datasets(sync_id: str, datasets: list[DatasetStatus]) -> None:
    with _lock:
        entry = _syncs.get(sync_id)
        if entry is not None:
            entry.datasets = datasets


def update_dataset_status(sync_id: str, dataset_name: str, status: DatasetSyncStatus) -> None:
    with _lock:
        entry = _syncs.get(sync_id)
        if entry is None:
            return
        for ds in entry.datasets:
            if ds.name == dataset_name:
                ds.status = status.value
                break


def complete_sync(sync_id: str) -> None:
    with _lock:
        entry = _syncs.get(sync_id)
        if entry is not None:
            entry.status = OverallStatus.COMPLETED.value
            entry.current_step = SyncStep.COMPLETED.value
            entry.completed_at = datetime.now(timezone.utc)


def fail_sync(sync_id: str, error: str) -> None:
    with _lock:
        entry = _syncs.get(sync_id)
        if entry is not None:
            entry.status = OverallStatus.FAILED.value
            entry.current_step = SyncStep.FAILED.value
            entry.completed_at = datetime.now(timezone.utc)
            entry.error = error


def clear() -> None:
    """Remove all entries. Useful for testing."""
    with _lock:
        _syncs.clear()


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _prune() -> None:
    """Keep at most MAX_ENTRIES entries, dropping oldest completed/failed first."""
    if len(_syncs) < MAX_ENTRIES:
        return
    finished = sorted(
        (
            (sid, s)
            for sid, s in _syncs.items()
            if s.status != OverallStatus.RUNNING.value
        ),
        key=lambda t: t[1].started_at,
    )
    to_remove = len(_syncs) - MAX_ENTRIES + 1  # make room for the new entry
    for sid, _ in finished[:to_remove]:
        del _syncs[sid]
