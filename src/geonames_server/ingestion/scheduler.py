from __future__ import annotations

from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from geonames_server.config.config import settings
from geonames_server.ingestion.daily_update import check_and_apply_daily_updates
from geonames_server.log.mylogger import get_logger

log = get_logger()

_scheduler: AsyncIOScheduler | None = None


def start_scheduler() -> AsyncIOScheduler:
    """Start the APScheduler async scheduler with daily update jobs.

    Adds one CronTrigger job per configured hour in settings.sync_schedule_hours.
    Runs on the current asyncio event loop. Sync job functions are
    executed in the default thread-pool executor automatically.
    Returns the scheduler instance.
    """
    global _scheduler

    _scheduler = AsyncIOScheduler()

    for hour in settings.sync_schedule_hours:
        trigger = CronTrigger(hour=hour, minute=0, timezone="UTC")
        _scheduler.add_job(
            check_and_apply_daily_updates,
            trigger=trigger,
            id=f"daily_update_{hour:02d}",
            name=f"Daily GeoNames update at {hour:02d}:00 UTC",
            replace_existing=True,
        )
        log.info(f"Scheduled daily update job at {hour:02d}:00 UTC")

    _scheduler.start()
    log.info("Scheduler started")

    # Run an immediate one-shot update on startup, after the API is up
    _scheduler.add_job(
        check_and_apply_daily_updates,
        trigger=DateTrigger(run_date=datetime.now(timezone.utc)),
        id="daily_update_startup",
        name="Daily GeoNames update on startup",
        replace_existing=True,
    )
    log.info("Scheduled immediate startup daily update job")

    return _scheduler


def stop_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    global _scheduler

    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
        _scheduler = None
