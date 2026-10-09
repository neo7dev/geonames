"""Tracked daily-update wrapper for the ``/sync`` API endpoint.

Mirrors the logic of :func:`~geonames_server.ingestion.daily_update.check_and_apply_daily_updates`
but reports progress through :mod:`~geonames_server.ingestion.sync_tracker`
so callers can poll ``GET /sync/{sync_id}`` for step-by-step status.
"""

from __future__ import annotations

from geonames_server.database.repository.sync.dataset_metadata_repository import (
    DatasetMetaDataRepository,
)
from geonames_server.database.session import get_session
from geonames_server.ingestion.daily_update import apply_daily_updates
from geonames_server.ingestion.pipeline import check_remote_metadata
from geonames_server.ingestion.registry import get_all_datasets
from geonames_server.ingestion.sync_tracker import (
    DatasetStatus,
    DatasetSyncStatus,
    SyncStep,
    complete_sync,
    fail_sync,
    set_datasets,
    update_dataset_status,
    update_step,
)
from geonames_server.log.mylogger import get_logger

log = get_logger()


def run_tracked_sync(sync_id: str) -> None:
    """Run the daily-update pipeline with progress tracking.

    This is a **sync** function executed in a thread-pool by FastAPI's
    ``BackgroundTasks``.
    """
    try:
        _run(sync_id)
    except Exception as exc:
        log.exception("Tracked sync failed")
        fail_sync(sync_id, str(exc))


def _refresh_dataset_statuses(
    sync_id: str,
    ds_statuses: list[DatasetStatus],
    mark_skipped: set[str] | None = None,
) -> None:
    """Re-read metadata from DB and push updated statuses to the tracker."""
    with get_session() as session:
        repo = DatasetMetaDataRepository(session)
        for ds_status in ds_statuses:
            record = repo.get_by_dataset(ds_status.name)
            if record:
                ds_status.last_modified = record.last_modified
                ds_status.out_of_sync = record.out_of_sync
                ds_status.needs_rebuild = record.needs_rebuild
            if mark_skipped and ds_status.name in mark_skipped:
                ds_status.status = DatasetSyncStatus.SKIPPED.value
    set_datasets(sync_id, ds_statuses)


def _run(sync_id: str) -> None:
    all_datasets = get_all_datasets()

    log.info(f"[sync:{sync_id}] Starting tracked daily update")

    update_step(sync_id, SyncStep.CHECKING_REMOTE_METADATA)
    check_remote_metadata(all_datasets)

    with get_session() as session:
        repo = DatasetMetaDataRepository(session)
        ds_statuses: list[DatasetStatus] = []
        for ds in all_datasets:
            record = repo.get_by_dataset(ds.name)
            ds_statuses.append(
                DatasetStatus(
                    name=ds.name,
                    last_modified=record.last_modified if record else None,
                    out_of_sync=record.out_of_sync if record else None,
                    needs_rebuild=record.needs_rebuild if record else None,
                )
            )
        out_of_sync_names = {r.dataset for r in repo.get_out_of_sync()}
    set_datasets(sync_id, ds_statuses)

    all_names = {ds.name for ds in all_datasets}
    skipped = all_names - out_of_sync_names

    if not out_of_sync_names:
        log.info(f"[sync:{sync_id}] All datasets are in sync — nothing to do")
        update_step(sync_id, SyncStep.IN_SYNC)
        _refresh_dataset_statuses(sync_id, ds_statuses, mark_skipped=all_names)
        complete_sync(sync_id)
        return

    log.info(f"[sync:{sync_id}] Out-of-sync datasets: {sorted(out_of_sync_names)}")

    apply_daily_updates(
        all_datasets,
        out_of_sync_names,
        on_step=lambda step: update_step(sync_id, step),
        on_dataset=lambda name, status: update_dataset_status(sync_id, name, status),
    )

    _refresh_dataset_statuses(sync_id, ds_statuses, mark_skipped=skipped)
    complete_sync(sync_id)
    log.info(f"[sync:{sync_id}] Daily update complete")
