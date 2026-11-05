"""The fill tasks as the box's only score writer.

On the server `OW_SCORES_ENABLED=false` turns off core's own score writer, so these tasks are the
only thing that writes `provider='internal'` rows. Upstream's tasks began with
`if not settings.ow_scores_enabled: return {...0}`; that guard is dropped in the vendored copy (see
`ow_scores_classic/tasks.py`) — with it, the cutover would have produced zero scores while every
beat entry reported success. Nothing else in the suite would notice, so this pins it, for both tasks:
the two fills drop the guard independently, so pinning one would leave the other free to go mute.
"""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from ow_scores_classic.tasks import fill_missing_resilience_scores, fill_missing_sleep_scores
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import EventRecordAggregate
from app.models.health_score import HealthScore
from app.repositories import AggregateRepository
from app.schemas.enums import ComputedBy
from tests.factories import (
    DataSourceFactory,
    EventRecordFactory,
    SeriesTypeDefinitionFactory,
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


def test_fill_missing_resilience_scores_writes_with_core_scores_disabled(
    db: Session, set_factory_session: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five nights of HRV + core's writer switched off → the task still writes the resilience score.

    The nights carry the HRV as a value on the sleep (Oura/Whoop/Polar style, no readings inside it),
    which is the path `min_days_required` is satisfied through here.
    """
    user = UserFactory()
    data_source = DataSourceFactory(user=user)
    rmssd = SeriesTypeDefinitionFactory.get_or_create_heart_rate_variability_rmssd()
    now = datetime.now(timezone.utc)
    for night, value in enumerate([40, 45, 50, 55, 60], start=1):
        end = (now - timedelta(days=night)).replace(hour=7, minute=0, second=0, microsecond=0)
        record = EventRecordFactory(
            category="sleep",
            type_="sleep",
            start_datetime=end - timedelta(hours=8),
            end_datetime=end,
            duration_seconds=8 * 3600,
            data_source=data_source,
        )
        AggregateRepository(EventRecordAggregate).upsert(
            db,
            [
                {
                    "record_id": record.id,
                    "series_type_definition_id": rmssd.id,
                    "computed_by": ComputedBy.PROVIDER,
                    "avg": Decimal(value),
                    "count": 1,
                }
            ],
        )
    db.flush()

    monkeypatch.setattr(settings, "ow_scores_enabled", False)

    with patch("ow_scores_classic.tasks.SessionLocal", return_value=nullcontext(db)):
        result = fill_missing_resilience_scores()

    assert result["saved"] >= 1, result
    assert (
        db.scalar(select(HealthScore).where(HealthScore.user_id == user.id, HealthScore.category == "resilience"))
        is not None
    )
