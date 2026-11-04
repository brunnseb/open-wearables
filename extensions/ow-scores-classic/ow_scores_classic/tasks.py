"""The two fill-missing Celery tasks, copied verbatim from upstream at `ow-scores-last`.

Sources: `backend/app/integrations/celery/tasks/fill_missing_{sleep,resilience}_scores_task.py`.
The SQL, the per-user grouping, the idempotency LEFT JOINs, the skip-and-retry behaviour and the
error handling are upstream's; see PROVENANCE.md. Three things differ, all of them plumbing:

- task names are this package's own constants (upstream's `CeleryTask` enum members go away with
  the removal) so they cannot collide with core's;
- the services and `resilience_config` are the vendored copies;
- the `settings.ow_scores_enabled` early-return is dropped. That flag is core's writer switch, and
  the cutover turns it OFF — keeping the guard here would make this package a permanent no-op.
  This package's own switches are core's extension discovery (`requires_core`) and its beat entries.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from logging import getLogger
from uuid import UUID, uuid4

from celery import shared_task
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.schemas.enums import EventCategory, HealthScoreCategory, ProviderName, SeriesType, get_series_type_id
from app.schemas.model_crud.activities.health_score import HealthScoreCreate, ScoreComponent
from app.services.health_score_service import health_score_service
from app.utils.sentry_helpers import log_and_capture_error
from app.utils.structured_logging import log_structured
from ow_scores_classic.algorithms.config import resilience_config
from ow_scores_classic.services.resilience_service import resilience_score_service
from ow_scores_classic.services.sleep_service import sleep_score_service

logger = getLogger(__name__)

FILL_MISSING_SLEEP_SCORES = "ow_scores_classic.fill_missing_sleep_scores"
FILL_MISSING_RESILIENCE_SCORES = "ow_scores_classic.fill_missing_resilience_scores"

_RMSSD_TYPE_ID = get_series_type_id(SeriesType.heart_rate_variability_rmssd)
_SDNN_TYPE_ID = get_series_type_id(SeriesType.heart_rate_variability_sdnn)

# Find all non-nap sleep sessions that have no corresponding OW sleep score.
# The match is on event_record_id (direct FK) so each session gets exactly one
# score regardless of timezone-induced date collisions. wake_date (local end
# date) is used as the lookup key for get_sleep_scores_for_records and for
# result ordering. recorded_at is set to the session's exact local end datetime
# (local_end_datetime) so two sessions sharing the same wake date still produce
# distinct recorded_at values. Limited to sessions within the configured
# backfill window.
_MISSING_SCORES_QUERY = text(f"""
    SELECT DISTINCT
        ds.user_id,
        ds.id AS data_source_id,
        er.id AS record_id,
        (er.end_datetime + COALESCE(er.zone_offset, '+00:00')::interval)::date AS wake_date,
        (er.end_datetime + COALESCE(er.zone_offset, '+00:00')::interval) AS local_end_datetime
    FROM event_record er
    JOIN data_source ds   ON ds.id = er.data_source_id
    JOIN sleep_details sd ON sd.record_id = er.id
    LEFT JOIN health_score hs
           ON hs.event_record_id = er.id
          AND hs.category     = '{HealthScoreCategory.SLEEP}'
          AND hs.provider     = 'internal'
    WHERE er.category = '{EventCategory.SLEEP}'
      AND (sd.is_nap IS NULL OR sd.is_nap = false)
      AND sd.sleep_total_duration_minutes IS NOT NULL
      AND sd.sleep_total_duration_minutes > 0
      AND hs.id IS NULL
      AND er.start_datetime >= :cutoff
    ORDER BY ds.user_id, wake_date DESC
""")

# Find all (user, reference_date) pairs that are missing an OW resilience score.
# active_users: users who have any RMSSD or SDNN data in the extended lookback window, as
# readings or as a night's value on a sleep. We cross-join with a date series covering the
# backfill window and filter out any dates that already have a score, ensuring idempotency.
_MISSING_RESILIENCE_SCORES_QUERY = text("""
    SELECT DISTINCT
        active_users.user_id,
        gs.target_date::date AS reference_date
    FROM generate_series(
        CURRENT_DATE - :backfill_days * INTERVAL '1 day',
        CURRENT_DATE,
        INTERVAL '1 day'
    ) AS gs(target_date)
    CROSS JOIN (
        SELECT DISTINCT ds.user_id
        FROM data_source ds
        WHERE EXISTS (
            SELECT 1 FROM series_sample dp
            WHERE dp.data_source_id = ds.id
              AND dp.series_type_definition_id IN (:rmssd_id, :sdnn_id)
              AND dp.recorded_at >= CURRENT_DATE - :data_lookback_days * INTERVAL '1 day'
        ) OR EXISTS (
            SELECT 1 FROM event_record er
            JOIN event_record_aggregate a ON a.record_id = er.id
            WHERE er.data_source_id = ds.id
              AND a.series_type_definition_id IN (:rmssd_id, :sdnn_id)
              AND er.end_datetime >= CURRENT_DATE - :data_lookback_days * INTERVAL '1 day'
        )
    ) AS active_users
    LEFT JOIN health_score hs
           ON hs.user_id  = active_users.user_id
          AND hs.category = 'resilience'
          AND hs.provider = 'internal'
          AND hs.recorded_at::date = gs.target_date::date
    WHERE hs.id IS NULL
    ORDER BY active_users.user_id, reference_date DESC
""")


@shared_task(name=FILL_MISSING_SLEEP_SCORES)
def fill_missing_sleep_scores() -> dict:
    """Find sleep sessions without an OW sleep score and calculate them.

    Runs frequently (every few minutes) so scores appear shortly after any sync
    path (periodic pull, webhook, SDK upload). Uses a LEFT JOIN on event_record_id
    to guarantee idempotency — already-scored sessions are never re-processed.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.score_backfill_days)

    with SessionLocal() as db:
        rows = db.execute(_MISSING_SCORES_QUERY, {"cutoff": cutoff}).fetchall()

        if not rows:
            return {"saved": 0, "skipped": 0}

        # Group (record_id, wake_date, local_end_datetime) triples per user
        records_by_user: dict[UUID, list[tuple[UUID, UUID, date, datetime]]] = defaultdict(list)
        for user_id, data_source_id, record_id, wake_date, local_end in rows:
            records_by_user[user_id].append((record_id, data_source_id, wake_date, local_end))

        log_structured(
            logger,
            "info",
            f"Found {len(rows)} session(s) missing scores across {len(records_by_user)} user(s)",
            task="fill_missing_sleep_scores",
        )

        total_saved = 0
        total_skipped = 0

        for uid, record_wakes_extended in records_by_user.items():
            sessions = [(rid, dsid, local_end) for rid, dsid, _, local_end in record_wakes_extended]
            try:
                scores_to_save = sleep_score_service.build_internal_sleep_scores(db, uid, sessions)
            except Exception as e:
                total_skipped += len(sessions)
                log_and_capture_error(
                    e,
                    logger,
                    f"Failed to fetch sleep data for user {uid}",
                    extra={"user_id": str(uid), "task": "fill_missing_sleep_scores"},
                )
                continue

            if not scores_to_save:
                total_skipped += len(sessions)
                continue

            try:
                health_score_service.bulk_create(db, scores_to_save)
                db.commit()
                total_saved += len(scores_to_save)
                total_skipped += len(sessions) - len(scores_to_save)
            except Exception as e:
                db.rollback()
                total_skipped += len(sessions)
                log_and_capture_error(
                    e,
                    logger,
                    f"Failed to save sleep scores for user {uid}",
                    extra={"user_id": str(uid), "task": "fill_missing_sleep_scores"},
                )

        log_structured(
            logger,
            "info",
            f"Sleep score fill complete: {total_saved} saved, {total_skipped} skipped",
            task="fill_missing_sleep_scores",
            saved=total_saved,
            skipped=total_skipped,
        )

        return {"saved": total_saved, "skipped": total_skipped}


@shared_task(name=FILL_MISSING_RESILIENCE_SCORES)
def fill_missing_resilience_scores() -> dict[str, int]:
    """Find (user, date) pairs without an OW resilience score and calculate them.

    Runs frequently so scores appear shortly after any sync path that delivers
    HRV data. Uses a LEFT JOIN against generate_series to guarantee idempotency
    — already-scored dates are never re-processed.

    Only dates where the service returns a non-null hrv_cv are persisted.
    Dates where there is insufficient HRV data remain unscored so they are
    retried on the next run once more data arrives.
    """
    with SessionLocal() as db:
        rows = db.execute(
            _MISSING_RESILIENCE_SCORES_QUERY,
            {
                "backfill_days": settings.score_backfill_days,
                "rmssd_id": _RMSSD_TYPE_ID,
                "sdnn_id": _SDNN_TYPE_ID,
                "data_lookback_days": settings.score_backfill_days + resilience_config.lookback_days,
            },
        ).fetchall()

        if not rows:
            return {"saved": 0, "skipped": 0}

        # Group reference dates per user
        dates_by_user: dict[UUID, list[date]] = defaultdict(list)
        for user_id, reference_date in rows:
            dates_by_user[UUID(str(user_id))].append(reference_date)

        log_structured(
            logger,
            "info",
            f"Found {len(rows)} resilience score(s) missing across {len(dates_by_user)} user(s)",
            task="fill_missing_resilience_scores",
        )

        total_saved = 0
        total_skipped = 0

        for uid, reference_dates in dates_by_user.items():
            try:
                scores_by_date = resilience_score_service.get_hrv_cv_scores_for_date_range(db, uid, reference_dates)
            except Exception as e:
                total_skipped += len(reference_dates)
                log_and_capture_error(
                    e,
                    logger,
                    f"Failed to fetch HRV data for user {uid}",
                    extra={"user_id": str(uid), "task": "fill_missing_resilience_scores"},
                )
                continue

            scores_to_save = [
                HealthScoreCreate(
                    id=uuid4(),
                    user_id=uid,
                    data_source_id=None,
                    provider=ProviderName.INTERNAL,
                    category=HealthScoreCategory.RESILIENCE,
                    value=result.hrv_cv,
                    recorded_at=datetime(ref_date.year, ref_date.month, ref_date.day, tzinfo=timezone.utc),
                    components={
                        "days_counted": ScoreComponent(value=result.days_counted),
                        "metric_type": ScoreComponent(qualifier=result.metric_type),
                        "resilience_score": ScoreComponent(value=result.resilience_score),
                    },
                )
                for ref_date, result in scores_by_date.items()
                if result.hrv_cv is not None
            ]

            # Dates with hrv_cv=None have insufficient data — leave unscored for retry.
            total_skipped += len(reference_dates) - len(scores_to_save)

            if not scores_to_save:
                continue

            try:
                health_score_service.bulk_create(db, scores_to_save)
                db.commit()
                total_saved += len(scores_to_save)
            except Exception as e:
                db.rollback()
                total_skipped += len(scores_to_save)
                log_and_capture_error(
                    e,
                    logger,
                    f"Failed to save resilience scores for user {uid}",
                    extra={"user_id": str(uid), "task": "fill_missing_resilience_scores"},
                )

        log_structured(
            logger,
            "info",
            f"Resilience score fill complete: {total_saved} saved, {total_skipped} skipped",
            task="fill_missing_resilience_scores",
            saved=total_saved,
            skipped=total_skipped,
        )

        return {"saved": total_saved, "skipped": total_skipped}
