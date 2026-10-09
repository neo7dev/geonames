from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from geonames_server.api.schemas.sync import (
    DatasetStatusResponse,
    SyncAcceptedResponse,
    SyncStatusResponse,
)
from geonames_server.ingestion import sync_tracker
from geonames_server.ingestion.tracked_daily_update import run_tracked_sync

router = APIRouter(tags=["Sync"])


@router.post(
    "/",
    response_model=SyncAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_sync(request: Request, background_tasks: BackgroundTasks):
    # The startup thread rebuilds datasets and updates their metadata; a sync
    # now would apply daily updates to tables about to be swapped out.
    startup = getattr(request.app.state, "dataset_startup", "pending")
    if startup in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Datasets are still being prepared at startup; retry later",
        )
    if sync_tracker.is_running():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A sync is already in progress",
        )

    sync_id = sync_tracker.create_sync()
    background_tasks.add_task(run_tracked_sync, sync_id)
    return SyncAcceptedResponse(sync_id=sync_id, message="Sync started")


@router.get("/{sync_id}", response_model=SyncStatusResponse)
async def get_sync_status(sync_id: str):
    entry = sync_tracker.get_sync(sync_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sync '{sync_id}' not found",
        )
    return SyncStatusResponse(
        sync_id=entry.sync_id,
        status=entry.status,
        current_step=entry.current_step,
        started_at=entry.started_at,
        completed_at=entry.completed_at,
        datasets=[
            DatasetStatusResponse(
                name=ds.name,
                last_modified=ds.last_modified,
                out_of_sync=ds.out_of_sync,
                needs_rebuild=ds.needs_rebuild,
                status=ds.status,
            )
            for ds in entry.datasets
        ],
        error=entry.error,
    )
