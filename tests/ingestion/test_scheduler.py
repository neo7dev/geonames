from unittest.mock import MagicMock, patch

from geonames_server.ingestion.scheduler import start_scheduler, stop_scheduler


class TestStartScheduler:
    @patch("geonames_server.ingestion.scheduler.AsyncIOScheduler")
    @patch("geonames_server.ingestion.scheduler.settings")
    def test_creates_scheduler_with_jobs(self, mock_settings, mock_scheduler_cls):
        mock_settings.sync_schedule_hours = [3, 15]
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        result = start_scheduler()

        assert result is mock_scheduler
        # 2 cron jobs + 1 startup one-shot job
        assert mock_scheduler.add_job.call_count == 3
        mock_scheduler.start.assert_called_once()

    @patch("geonames_server.ingestion.scheduler.AsyncIOScheduler")
    @patch("geonames_server.ingestion.scheduler.settings")
    def test_job_ids_contain_hours(self, mock_settings, mock_scheduler_cls):
        mock_settings.sync_schedule_hours = [3, 15]
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        start_scheduler()

        calls = mock_scheduler.add_job.call_args_list
        job_ids = [call.kwargs["id"] for call in calls]
        assert "daily_update_03" in job_ids
        assert "daily_update_15" in job_ids
        assert "daily_update_startup" in job_ids

    @patch("geonames_server.ingestion.scheduler.AsyncIOScheduler")
    @patch("geonames_server.ingestion.scheduler.settings")
    def test_empty_hours_creates_no_jobs(self, mock_settings, mock_scheduler_cls):
        mock_settings.sync_schedule_hours = []
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        start_scheduler()

        # Only the startup one-shot job is added when no cron hours are configured
        assert mock_scheduler.add_job.call_count == 1
        calls = mock_scheduler.add_job.call_args_list
        assert calls[0].kwargs["id"] == "daily_update_startup"
        mock_scheduler.start.assert_called_once()


class TestStopScheduler:
    @patch("geonames_server.ingestion.scheduler._scheduler", None)
    def test_no_error_when_scheduler_is_none(self):
        # Should not raise
        stop_scheduler()

    @patch("geonames_server.ingestion.scheduler._scheduler")
    def test_calls_shutdown(self, mock_scheduler):
        stop_scheduler()
        mock_scheduler.shutdown.assert_called_once_with(wait=False)
