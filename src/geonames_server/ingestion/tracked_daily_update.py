"""Tracked daily-update wrapper for the ``/sync`` API endpoint.

Mirrors the logic of :func:`~geonames_server.ingestion.daily_update.check_and_apply_daily_updates`
but reports progress through :mod:`~geonames_server.ingestion.sync_tracker`
so callers can poll ``GET /sync/{sync_id}`` for step-by-step status.
"""

from __future__ import annotations

from geonames_server.config.config import settings
from geonames_server.database.models.modifications import Modifications
from geonames_server.database.repository.sync.dataset_metadata_repository import (
    DatasetMetaDataRepository,
)
from geonames_server.database.session import get_session
from geonames_server.ingestion.daily_update import (
    POPULATION_THRESHOLDS,
    _delete_from_dataset,
    _ingest_daily_file,
    _ingest_deletions_file,
    _sync_metadata,
    _upsert_modifications,
    _yesterday,
)
from geonames_server.ingestion.downloader import Downloader
from geonames_server.ingestion.pipeline import _get_conn_params, check_remote_metadata
from geonames_server.ingestion.registry import get_all_datasets
from geonames_server.ingestion.remote import RemoteFileClient
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
    yesterday = _yesterday()

    log.info(f"[sync:{sync_id}] Starting tracked daily update for {yesterday}")

    # --- Step 1a: HEAD-check remote metadata ----------------------------
    update_step(sync_id, SyncStep.CHECKING_REMOTE_METADATA)
    check_remote_metadata(all_datasets)

    # --- Populate dataset statuses from DB after HEAD-check -------------
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
        out_of_sync_records = repo.get_out_of_sync()
        out_of_sync_names = {r.dataset for r in out_of_sync_records}
    set_datasets(sync_id, ds_statuses)

    all_dataset_names = {ds.name for ds in all_datasets}

    # --- Step 1b: Determine which datasets are out of sync --------------
    if not out_of_sync_names:
        log.info(f"[sync:{sync_id}] All datasets are in sync — nothing to do")
        update_step(sync_id, SyncStep.IN_SYNC)
        _refresh_dataset_statuses(sync_id, ds_statuses, mark_skipped=all_dataset_names)
        complete_sync(sync_id)
        return

    log.info(f"[sync:{sync_id}] Out-of-sync datasets: {sorted(out_of_sync_names)}")

    # --- Download daily files -------------------------------------------
    mod_file = f"modifications-{yesterday.isoformat()}.txt"
    del_file = f"deletes-{yesterday.isoformat()}.txt"

    with RemoteFileClient() as client:
        mod_url = settings.geonames_url + mod_file
        del_url = settings.geonames_url + del_file
        mod_exists = client.exists(mod_url)
        del_exists = client.exists(del_url)

    # If neither file exists on the remote server, nothing to apply
    if not mod_exists and not del_exists:
        log.info(f"[sync:{sync_id}] No daily files available — skipping ingestion")
        update_step(sync_id, SyncStep.IN_SYNC)
        _refresh_dataset_statuses(
            sync_id, ds_statuses,
            mark_skipped=all_dataset_names - out_of_sync_names,
        )
        complete_sync(sync_id)
        return

    # Download files that exist — exceptions propagate as sync failures
    mod_path = None
    del_path = None

    if mod_exists:
        update_step(sync_id, SyncStep.DOWNLOADING_MODIFICATIONS)
        log.info(f"[sync:{sync_id}] Downloading {mod_file}")
        with Downloader(
            cache_dir=settings.cache_dir, data_dir=settings.data_dir, force=True
        ) as dl:
            mod_path = dl.download(url=mod_url, file_name=mod_file).path

    if del_exists:
        update_step(sync_id, SyncStep.DOWNLOADING_DELETES)
        log.info(f"[sync:{sync_id}] Downloading {del_file}")
        with Downloader(
            cache_dir=settings.cache_dir, data_dir=settings.data_dir, force=True
        ) as dl:
            del_path = dl.download(url=del_url, file_name=del_file).path

    # --- Ingest into staging tables -------------------------------------
    if mod_path is not None:
        update_step(sync_id, SyncStep.INGESTING_MODIFICATIONS)
        log.info(f"[sync:{sync_id}] Ingesting {mod_file} into modifications table")
        try:
            _ingest_daily_file(mod_path, Modifications)
        except Exception:
            log.exception(f"[sync:{sync_id}] Failed to ingest modifications file")
            fail_sync(sync_id, "Failed to ingest modifications file")
            return

    if del_path is not None:
        update_step(sync_id, SyncStep.INGESTING_DELETES)
        log.info(f"[sync:{sync_id}] Ingesting {del_file} into deletions table")
        try:
            _ingest_deletions_file(del_path, yesterday)
        except Exception:
            log.exception(f"[sync:{sync_id}] Failed to ingest deletions file")
            fail_sync(sync_id, "Failed to ingest deletions file")
            return

    # --- Apply staged data to each out-of-sync dataset ------------------
    update_step(sync_id, SyncStep.APPLYING_DATASETS)
    conn_params = _get_conn_params()
    datasets_to_update = [ds for ds in all_datasets if ds.name in out_of_sync_names]

    for ds in datasets_to_update:
        table_name = ds.model.__table__.name
        threshold = POPULATION_THRESHOLDS.get(ds.name, 0)

        if mod_path is not None:
            update_dataset_status(
                sync_id, ds.name, DatasetSyncStatus.APPLYING_MODIFICATIONS
            )
            try:
                count = _upsert_modifications(
                    conn_params, table_name, yesterday, threshold
                )
                log.info(f"[sync:{sync_id}] [{ds.name}] Upserted {count} modification(s)")
            except Exception:
                log.exception(f"[sync:{sync_id}] [{ds.name}] Failed to apply modifications")
                update_dataset_status(sync_id, ds.name, DatasetSyncStatus.FAILED)
                continue

        if del_path is not None:
            update_dataset_status(
                sync_id, ds.name, DatasetSyncStatus.APPLYING_DELETES
            )
            try:
                count = _delete_from_dataset(conn_params, table_name, yesterday)
                log.info(f"[sync:{sync_id}] [{ds.name}] Deleted {count} row(s)")
            except Exception:
                log.exception(f"[sync:{sync_id}] [{ds.name}] Failed to apply deletions")
                update_dataset_status(sync_id, ds.name, DatasetSyncStatus.FAILED)
                continue

        update_dataset_status(sync_id, ds.name, DatasetSyncStatus.SYNCED)

    # Mark datasets that were already in-sync as skipped
    for ds_status in ds_statuses:
        if ds_status.name not in out_of_sync_names:
            update_dataset_status(sync_id, ds_status.name, DatasetSyncStatus.SKIPPED)

    # --- Sync metadata (clears out_of_sync) -----------------------------
    update_step(sync_id, SyncStep.SYNCING_METADATA)
    _sync_metadata(datasets_to_update)

    # Refresh dataset metadata from DB so the response reflects current state
    _refresh_dataset_statuses(
        sync_id, ds_statuses,
        mark_skipped=all_dataset_names - out_of_sync_names,
    )

    complete_sync(sync_id)
    log.info(f"[sync:{sync_id}] Daily update for {yesterday} complete")
