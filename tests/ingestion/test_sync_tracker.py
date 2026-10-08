from datetime import datetime, timezone

import pytest

from geonames_server.ingestion.sync_tracker import (
    DatasetStatus,
    DatasetSyncStatus,
    OverallStatus,
    SyncStatus,
    SyncStep,
    clear,
    complete_sync,
    create_sync,
    fail_sync,
    get_sync,
    is_running,
    set_datasets,
    update_dataset_status,
    update_step,
)


@pytest.fixture(autouse=True)
def _clean_tracker():
    """Ensure a clean tracker for every test."""
    clear()
    yield
    clear()


class TestCreateSync:
    def test_returns_hex_string(self):
        sync_id = create_sync()
        assert isinstance(sync_id, str)
        assert len(sync_id) == 32  # uuid4().hex

    def test_creates_entry_with_running_status(self):
        sync_id = create_sync()
        entry = get_sync(sync_id)
        assert entry is not None
        assert entry.status == OverallStatus.RUNNING.value
        assert entry.current_step == SyncStep.STARTING.value
        assert entry.completed_at is None
        assert entry.datasets == []
        assert entry.error is None

    def test_started_at_is_utc(self):
        sync_id = create_sync()
        entry = get_sync(sync_id)
        assert entry.started_at.tzinfo is not None


class TestGetSync:
    def test_returns_none_for_unknown_id(self):
        assert get_sync("nonexistent") is None

    def test_returns_entry_for_known_id(self):
        sync_id = create_sync()
        assert get_sync(sync_id) is not None


class TestIsRunning:
    def test_false_when_empty(self):
        assert is_running() is False

    def test_true_when_running(self):
        create_sync()
        assert is_running() is True

    def test_false_after_completion(self):
        sync_id = create_sync()
        complete_sync(sync_id)
        assert is_running() is False

    def test_false_after_failure(self):
        sync_id = create_sync()
        fail_sync(sync_id, "boom")
        assert is_running() is False


class TestUpdateStep:
    def test_updates_current_step(self):
        sync_id = create_sync()
        update_step(sync_id, SyncStep.CHECKING_REMOTE_METADATA)
        assert get_sync(sync_id).current_step == SyncStep.CHECKING_REMOTE_METADATA.value

    def test_noop_for_unknown_id(self):
        update_step("unknown", SyncStep.COMPLETED)  # should not raise


class TestSetDatasets:
    def test_sets_dataset_list(self):
        sync_id = create_sync()
        datasets = [
            DatasetStatus(name="cities500", out_of_sync=True),
            DatasetStatus(name="cities15000", out_of_sync=False),
        ]
        set_datasets(sync_id, datasets)
        entry = get_sync(sync_id)
        assert len(entry.datasets) == 2
        assert entry.datasets[0].name == "cities500"

    def test_noop_for_unknown_id(self):
        set_datasets("unknown", [])  # should not raise


class TestUpdateDatasetStatus:
    def test_updates_specific_dataset(self):
        sync_id = create_sync()
        set_datasets(
            sync_id,
            [
                DatasetStatus(name="cities500"),
                DatasetStatus(name="cities15000"),
            ],
        )
        update_dataset_status(sync_id, "cities500", DatasetSyncStatus.APPLYING_MODIFICATIONS)
        entry = get_sync(sync_id)
        assert entry.datasets[0].status == DatasetSyncStatus.APPLYING_MODIFICATIONS.value
        assert entry.datasets[1].status == DatasetSyncStatus.PENDING.value

    def test_noop_for_unknown_dataset(self):
        sync_id = create_sync()
        set_datasets(sync_id, [DatasetStatus(name="cities500")])
        update_dataset_status(sync_id, "nonexistent", DatasetSyncStatus.SYNCED)
        # no error, cities500 unchanged
        assert get_sync(sync_id).datasets[0].status == DatasetSyncStatus.PENDING.value

    def test_noop_for_unknown_sync_id(self):
        update_dataset_status("unknown", "cities500", DatasetSyncStatus.SYNCED)


class TestCompleteSync:
    def test_marks_completed(self):
        sync_id = create_sync()
        complete_sync(sync_id)
        entry = get_sync(sync_id)
        assert entry.status == OverallStatus.COMPLETED.value
        assert entry.current_step == SyncStep.COMPLETED.value
        assert entry.completed_at is not None

    def test_noop_for_unknown_id(self):
        complete_sync("unknown")  # should not raise


class TestFailSync:
    def test_marks_failed_with_error(self):
        sync_id = create_sync()
        fail_sync(sync_id, "something broke")
        entry = get_sync(sync_id)
        assert entry.status == OverallStatus.FAILED.value
        assert entry.current_step == SyncStep.FAILED.value
        assert entry.completed_at is not None
        assert entry.error == "something broke"

    def test_noop_for_unknown_id(self):
        fail_sync("unknown", "err")  # should not raise


class TestPrune:
    def test_prunes_oldest_completed_entries(self):
        # Fill up to MAX_ENTRIES completed entries, then add one more
        from geonames_server.ingestion import sync_tracker

        original_max = sync_tracker.MAX_ENTRIES
        sync_tracker.MAX_ENTRIES = 5
        try:
            ids = []
            for _ in range(5):
                sid = create_sync()
                complete_sync(sid)
                ids.append(sid)
            # All 5 should exist
            assert all(get_sync(sid) is not None for sid in ids)

            # Adding one more triggers prune
            new_id = create_sync()
            assert get_sync(new_id) is not None
            # Oldest should have been removed
            assert get_sync(ids[0]) is None
        finally:
            sync_tracker.MAX_ENTRIES = original_max
