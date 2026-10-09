from datetime import date, datetime, timedelta

from sqlalchemy import Boolean, Date, DateTime, String, event
from sqlalchemy.orm import Mapped, mapped_column, validates  # noqa: F401

from geonames_server.database.models.base import Base
from geonames_server.log.mylogger import get_logger

log = get_logger()


class DatasetMetaData(Base):
    __tablename__ = "dataset_metadata"

    dataset: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )

    # zip file can be null as there are many files that don't have a zip version, just the txt file
    zip_file: Mapped[str] = mapped_column(
        String,
        nullable=True,
    )

    # as far as I know all datasets have a txt file, so this is not nullable
    txt_file: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    out_of_sync: Mapped[bool | None] = mapped_column(
        Boolean,
        index=True,
    )

    needs_rebuild: Mapped[bool | None] = mapped_column(
        Boolean,
        index=True,
    )

    # out_of_sync: Mapped[bool | None] = mapped_column(
    #     Boolean,
    #     Computed("(remote_etag IS DISTINCT FROM etag) AND "
    #              "(remote_last_modified > last_modified)",),
    #     persisted=True,
    #     index=True,
    # )

    # needs_rebuild: Mapped[bool | None] = mapped_column(
    #     Boolean,
    #     Computed("out_of_sync AND needs_rebuild",),
    #     persisted=True,
    # )

    etag: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    remote_etag: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )

    last_modified: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    remote_last_modified: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # Last day whose daily modifications/deletes files were applied.
    last_applied_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # ! Convert dataset to lowercase but on second thought, may be just leave it as is
    # @validates("dataset")
    # def validate_dataset_lowercase(self, _key, value):
    #     return value.lower()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"dataset={self.dataset}, "
            f"zip_file={self.zip_file}, "
            f"txt_file={self.txt_file}, "
            f"out_of_sync={self.out_of_sync}, "
            f"needs_rebuild={self.needs_rebuild}, "
            f"etag={self.etag}, "
            f"remote_etag={self.remote_etag}, "
            f"last_modified={self.last_modified}, "
            f"remote_last_modified={self.remote_last_modified}, "
            f"last_applied_date={self.last_applied_date}>"
        )

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "zip_file": self.zip_file,
            "txt_file": self.txt_file,
            "out_of_sync": self.out_of_sync,
            "needs_rebuild": self.needs_rebuild,
            "etag": self.etag,
            "remote_etag": self.remote_etag,
            "last_modified": self.last_modified.isoformat()
            if self.last_modified
            else None,
            "remote_last_modified": self.remote_last_modified.isoformat()
            if self.remote_last_modified
            else None,
            "last_applied_date": self.last_applied_date.isoformat()
            if self.last_applied_date
            else None,
        }


def refresh_sync_status(target) -> None:
    # 1. Capture the values
    etag = target.etag
    r_etag = target.remote_etag
    l_modified = target.last_modified
    r_modified = target.remote_last_modified

    # 2. Check for NULLs (If any mandatory field is missing, it's out of sync)
    if any(v is None for v in [etag, r_etag, l_modified, r_modified]):
        is_out_of_sync = True
    else:
        # 3. Compare values (None of these are None anymore)
        etag_different = etag != r_etag

        # Strip microseconds for a fair comparison
        local_dt = l_modified.replace(microsecond=0)
        remote_dt = r_modified.replace(microsecond=0)
        remote_is_newer = remote_dt > local_dt

        # Use OR: Out of sync if EITHER the tag changed OR the remote is newer
        is_out_of_sync = etag_different or remote_is_newer

    # 4. Log and Apply
    log.debug(
        "Dataset: %s | ETag Match: %s | Remote Newer: %s | Result: %s",
        target.dataset,
        (etag == r_etag),
        (remote_dt > local_dt if "remote_dt" in locals() else "N/A"),
        is_out_of_sync,
    )

    target.out_of_sync = is_out_of_sync


def refresh_needs_rebuild(target) -> None:
    local_dt = target.last_modified
    remote_dt = target.remote_last_modified

    log.debug(
        "dataset is %s, Checking rebuild: local=%s, remote=%s",
        target.dataset,
        local_dt,
        remote_dt,
    )

    # Dataset was never built — always needs a rebuild
    if not local_dt or not remote_dt:
        target.needs_rebuild = True
        return

    td: timedelta = remote_dt.replace(microsecond=0) - local_dt.replace(microsecond=0)

    log.debug(
        "dataset is %s, local datetime is %s, remote datetime is %s, difference is %s",
        target.dataset,
        local_dt,
        remote_dt,
        td,
    )

    target.needs_rebuild = td.days >= 2


@event.listens_for(DatasetMetaData, "before_insert")
@event.listens_for(DatasetMetaData, "before_update")
def receive_refresh(mapper, connection, target):
    refresh_sync_status(target)
    refresh_needs_rebuild(target)
