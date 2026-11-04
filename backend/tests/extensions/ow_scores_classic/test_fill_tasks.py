"""The fill tasks as the box's only score writer.

On the server `OW_SCORES_ENABLED=false` turns off core's own score writer, so these tasks are the
only thing that writes `provider='internal'` rows. Upstream's tasks began with
`if not settings.ow_scores_enabled: return {...0}`; that guard is dropped in the vendored copy (see
`ow_scores_classic/tasks.py`) — with it, the cutover would have produced zero scores while every
beat entry reported success. Nothing else in the suite would notice, so this pins it.
"""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from ow_scores_classic.tasks import fill_missing_sleep_scores
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.health_score import HealthScore
from tests.factories import (
    DataSourceFactory,
    EventRecordFactory,
    SleepDetailsFactory,
    UserFactory,
)


def test_fill_missing_sleep_scores_writes_with_core_scores_disabled(
    db: Session, set_factory_session: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unscored sleep session + core's writer switched off → the task still writes the score."""
    user = UserFactory()
    data_source = DataSourceFactory(user=user)
    end = datetime.now(timezone.utc) - timedelta(days=1)
    record = EventRecordFactory(
        category="sleep",
        type_="sleep",
        start_datetime=end - timedelta(hours=8),
        end_datetime=end,
        duration_seconds=8 * 3600,
        data_source=data_source,
    )
    SleepDetailsFactory(
        event_record=record,
        sleep_total_duration_minutes=450,
        sleep_deep_minutes=100,
        sleep_rem_minutes=90,
        sleep_awake_minutes=10,
        sleep_light_minutes=260,
        is_nap=False,
    )
    db.flush()

    monkeypatch.setattr(settings, "ow_scores_enabled", False)

    with patch("ow_scores_classic.tasks.SessionLocal", return_value=nullcontext(db)):
        assert fill_missing_sleep_scores() == {"saved": 1, "skipped": 0}
    assert (
        db.scalar(select(HealthScore).where(HealthScore.user_id == user.id, HealthScore.category == "sleep"))
        is not None
    )
