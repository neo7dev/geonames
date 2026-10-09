"""The sync session scope the ingestion code relies on to commit its writes."""

from unittest.mock import patch

import pytest

from geonames_server.database import session as session_module
from geonames_server.database.session import get_session


@pytest.fixture
def fake_session():
    with patch.object(session_module, "sessionLocal") as factory:
        yield factory.return_value


def test_commits_and_closes_when_the_block_succeeds(fake_session):
    with get_session() as session:
        assert session is fake_session
        fake_session.commit.assert_not_called()

    fake_session.commit.assert_called_once()
    fake_session.rollback.assert_not_called()
    fake_session.close.assert_called_once()


def test_rolls_back_closes_and_reraises_when_the_block_fails(fake_session):
    with pytest.raises(ValueError, match="boom"):
        with get_session():
            raise ValueError("boom")

    fake_session.commit.assert_not_called()
    fake_session.rollback.assert_called_once()
    fake_session.close.assert_called_once()


def test_rolls_back_when_the_commit_fails(fake_session):
    fake_session.commit.side_effect = RuntimeError("serialization failure")

    with pytest.raises(RuntimeError, match="serialization failure"):
        with get_session():
            pass

    fake_session.rollback.assert_called_once()
    fake_session.close.assert_called_once()
