"""Parity gate: do the vendored services reproduce the scores already stored in this database?

Run it against the real database BEFORE the cutover. It re-runs the same code path the Celery
tasks use (`build_internal_sleep_scores` / `get_hrv_cv_scores_for_date_range`) for every
`provider='internal'` row in the window and compares the stored `value` and `components` with the
recomputed ones. The algorithms are copied verbatim, so any mismatch means an import/copy went
wrong — investigate it, never "tune" a number.

Read-only: it writes nothing.

Invocation — it needs core's settings, models and database URL, so run it inside the app container
(it is not executable and has no shebang, and since the image `COPY` was narrowed it is not inside
the image either):
    docker cp scripts/parity_check.py open-wearables:/tmp/pc.py
    docker exec open-wearables python /tmp/pc.py --days 30

One line per stored row:  date  category  user=<id>  stored=<v>/<comps>  recomputed=<v>/<comps>  OK|MISMATCH|DRIFT
One line per gap:         date  category  user=<id>  MISSING
Exit 0 when every stored score in a closed window is reproduced; 1 on any MISMATCH. MISSING and
DRIFT are informational: they are reported, counted and never failed.

MISSING is a row the fill tasks would still write, read from their own queries, so the gate's idea
of a gap is the writer's: it is not a parity failure, just the state of a window still being filled.

A mismatching row is DRIFT, not MISMATCH, when the inputs it was computed from moved after the row
was written: any day inside that row's own lookback window got new data later than the row's own
`created_at`. Providers backfill — a night's HRV arrives the next morning, a whole day can be
re-synced, a restored backup rewrites both — so recomputing such a row legitimately sees inputs the
run that wrote it never had. Age is not the question and stops mattering entirely: a row 8 days old
whose inputs all predate it must still reproduce exactly, and a row written minutes ago whose inputs
never moved is a real mismatch. A MISMATCH therefore always means the vendored code differs.
"""

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from ow_scores_classic.algorithms.config import resilience_config, sleep_config
from ow_scores_classic.services.resilience_service import resilience_score_service
from ow_scores_classic.services.sleep_service import sleep_score_service
from ow_scores_classic.tasks import (
    _MISSING_RESILIENCE_SCORES_QUERY,
    _MISSING_SCORES_QUERY,
    _RMSSD_TYPE_ID,
    _SDNN_TYPE_ID,
)
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

# Same rows the fill tasks write, so the comparison is against the task's own code path.
_STORED_SLEEP = text("""
    SELECT hs.user_id,
           hs.event_record_id,
           hs.value,
           hs.components,
           hs.recorded_at,
           hs.created_at,
           er.data_source_id,
           (er.start_datetime + COALESCE(er.zone_offset, '+00:00')::interval) AS local_start,
           (er.end_datetime + COALESCE(er.zone_offset, '+00:00')::interval) AS local_end
    FROM health_score hs
    JOIN event_record er ON er.id = hs.event_record_id
    WHERE hs.provider = 'internal' AND hs.category = 'sleep' AND hs.recorded_at >= :cutoff
    ORDER BY hs.user_id, hs.recorded_at
""")

_STORED_RESILIENCE = text("""
    SELECT user_id, recorded_at, created_at, value, components
    FROM health_score
    WHERE provider = 'internal' AND category = 'resilience' AND recorded_at >= :cutoff
    ORDER BY user_id, recorded_at
""")

# When each input day was last written, per day, bucketed exactly the way the service that reads it
# buckets. A stored row is only re-checkable while nothing it read has been written since it was.

# The resilience score averages RMSSD samples bucketed by UTC date (and a night's session value,
# which rides on the sleep record as an aggregate), so both are inputs to it.
_INGEST_SAMPLES_BY_DAY = text("""
    SELECT (ss.recorded_at AT TIME ZONE 'UTC')::date AS day, MAX(ss.created_at) AS ingested_at
    FROM series_sample ss
    JOIN data_source ds ON ds.id = ss.data_source_id
    WHERE ds.user_id = :uid
      AND ss.series_type_definition_id = :type_id
      AND ss.recorded_at >= :cutoff
    GROUP BY 1
""")

_INGEST_SLEEP_AGGREGATES_BY_DAY = text("""
    SELECT (er.end_datetime AT TIME ZONE 'UTC')::date AS day, MAX(GREATEST(er.created_at, a.created_at)) AS ingested_at
    FROM event_record er
    JOIN event_record_aggregate a ON a.record_id = er.id
    JOIN data_source ds ON ds.id = er.data_source_id
    WHERE ds.user_id = :uid
      AND er.category = 'sleep'
      AND a.series_type_definition_id = :type_id
      AND a.avg IS NOT NULL
      AND er.end_datetime >= :cutoff
    GROUP BY 1
""")

# The sleep score reads sleep sessions only — its own night plus the rolling bedtime history —
# bucketed by the local start date it buckets them by.
_INGEST_SLEEP_SESSIONS_BY_DAY = text("""
    SELECT (er.start_datetime + COALESCE(er.zone_offset, '+00:00')::interval)::date AS day,
           MAX(GREATEST(er.created_at, sd.created_at)) AS ingested_at
    FROM event_record er
    JOIN sleep_details sd ON sd.record_id = er.id
    JOIN data_source ds ON ds.id = er.data_source_id
    WHERE ds.user_id = :uid
      AND er.category = 'sleep'
      AND (sd.is_nap IS NULL OR sd.is_nap = false)
      AND er.start_datetime >= :cutoff
    GROUP BY 1
""")

# Half of the last digit `value` can hold: see `_same_value`. NUMERIC(6,3) -> 0.0005.
_TOLERANCE = Decimal("0.0005")


def _components(stored: Any) -> dict[str, Any]:
    """Normalise a component map to `{name: value|qualifier}`.

    The stored side is the `components` JSON (`{"duration": {"value": 82, ...}}`); the recomputed
    side holds `ScoreComponent` objects. Comparing the two raw forms can never be equal, so both
    are flattened here (see tests/test_parity_check.py).
    """
    out: dict[str, Any] = {}
    for name, comp in (stored or {}).items():
        if isinstance(comp, dict):
            value = comp.get("value")
            out[name] = value if value is not None else comp.get("qualifier")
        else:
            value = getattr(comp, "value", None)
            out[name] = value if value is not None else getattr(comp, "qualifier", None)
    return out


def _num(value: Any) -> Decimal:
    return Decimal(str(round(value, 3) if isinstance(value, float) else value))


def _same_value(stored: Any, recomputed: Any) -> bool:
    """Would this recomputed number have been stored as this exact row?

    `value` is NUMERIC(6,3): the row holds what Postgres wrote with half-away-from-zero rounding,
    while Python's `round()` is banker's, so a faithful recomputation of a value that sat on the
    last digit's boundary comes back one digit away. Comparing the two rounded copies — which is
    what an exact compare, or a tolerance applied *after* rounding both sides to the stored three
    decimals, both amount to — reports that as a MISMATCH. Half the stored digit, against the
    unrounded number, is the comparison that actually allows for it.
    """
    return abs(_num(stored) - Decimal(str(recomputed))) <= _TOLERANCE


def _merge_ingest(out: dict[date, datetime], rows: list[Any]) -> dict[date, datetime]:
    """Fold `(day, ingested_at)` rows into a newest-write-per-day map."""
    for day, ingested_at in rows:
        if day not in out or ingested_at > out[day]:
            out[day] = ingested_at
    return out


def _sleep_ingest_by_day(session: Any, uid: UUID, cutoff: datetime) -> dict[date, datetime]:
    """Newest write per night, over the sleep sessions the sleep score reads for one user."""
    return _merge_ingest({}, session.execute(_INGEST_SLEEP_SESSIONS_BY_DAY, {"uid": uid, "cutoff": cutoff}).fetchall())


def _resilience_ingest_by_day(session: Any, uid: UUID, cutoff: datetime) -> dict[date, datetime]:
    """Newest write per day, over both inputs the resilience score reads for one user."""
    params = {"uid": uid, "cutoff": cutoff, "type_id": _RMSSD_TYPE_ID}
    out = _merge_ingest({}, session.execute(_INGEST_SAMPLES_BY_DAY, params).fetchall())
    return _merge_ingest(out, session.execute(_INGEST_SLEEP_AGGREGATES_BY_DAY, params).fetchall())


def _inputs_moved(
    row_created_at: datetime | None, window_days: list[date], ingest_by_day: dict[date, datetime]
) -> bool:
    """Was any day this row was computed from written to after the row itself was written?"""
    if row_created_at is None:  # a row predating `created_at` cannot be dated against the ingest
        return False
    return any(ingest_by_day.get(day, row_created_at) > row_created_at for day in window_days)


def _sleep_window(local_start: datetime) -> list[date]:
    """The nights `get_sleep_scores_for_records` reads for one row: its own + the rolling history."""
    first_night = local_start.date() - timedelta(days=sleep_config.rolling_window_nights + 1)
    return [first_night + timedelta(days=i) for i in range((local_start.date() - first_night).days + 1)]


def _resilience_window(ref_day: date) -> list[date]:
    """The days `get_hrv_cv_scores_for_date_range` reads for one row, in the same order it builds them."""
    return [ref_day - timedelta(days=i) for i in range(resilience_config.lookback_days, 0, -1)]


def check_sleep(session: Any, cutoff: datetime) -> tuple[int, int]:
    """Returns (mismatches, drift) — drift is a mismatch whose own inputs moved after the row."""
    rows = session.execute(_STORED_SLEEP, {"cutoff": cutoff}).fetchall()
    by_user: dict[UUID, list[Any]] = defaultdict(list)
    for row in rows:
        by_user[UUID(str(row.user_id))].append(row)

    mismatches = 0
    drift = 0
    for uid, user_rows in by_user.items():
        sessions = [(UUID(str(r.event_record_id)), UUID(str(r.data_source_id)), r.local_end) for r in user_rows]
        ingest = _sleep_ingest_by_day(session, uid, cutoff)
        try:
            built = {
                s.event_record_id: s for s in sleep_score_service.build_internal_sleep_scores(session, uid, sessions)
            }
        except Exception as exc:  # a recomputation that cannot run is a parity failure, not a skip
            print(f"{cutoff.date()} sleep RECOMPUTE-ERROR {exc}")
            return len(user_rows), 0

        for row in user_rows:
            record_id = UUID(str(row.event_record_id))
            created = built.get(record_id)
            day = row.recorded_at.date()
            if created is None:
                print(f"{day} sleep user={row.user_id} stored={row.value} recomputed=NONE MISMATCH")
                mismatches += 1
                continue
            stored_v, recomp_v = _num(row.value), _num(created.value)
            stored_c, recomp_c = _components(row.components), _components(created.components)
            ok = _same_value(row.value, created.value) and stored_c == recomp_c
            if ok:
                status = "OK"
            elif _inputs_moved(row.created_at, _sleep_window(row.local_start), ingest):
                # Its night's record (or a history night's) was written after this row: the inputs
                # this score was computed from are gone.
                drift += 1
                status = "DRIFT"
            else:
                mismatches += 1
                status = "MISMATCH"
            print(
                f"{day} sleep user={row.user_id} stored={stored_v}/{stored_c} recomputed={recomp_v}/{recomp_c} {status}"
            )
    return mismatches, drift


def check_resilience(session: Any, cutoff: datetime) -> tuple[int, int]:
    """Returns (mismatches, drift) — drift is a mismatch whose own inputs moved after the row."""
    rows = session.execute(_STORED_RESILIENCE, {"cutoff": cutoff}).fetchall()
    by_user: dict[UUID, list[Any]] = defaultdict(list)
    for row in rows:
        by_user[UUID(str(row.user_id))].append(row)

    mismatches = 0
    drift = 0
    for uid, user_rows in by_user.items():
        ref_dates: list[date] = sorted({r.recorded_at.date() for r in user_rows})
        ingest = _resilience_ingest_by_day(session, uid, cutoff)
        try:
            results = resilience_score_service.get_hrv_cv_scores_for_date_range(session, uid, ref_dates)
        except Exception as exc:
            print(f"{cutoff.date()} resilience RECOMPUTE-ERROR {exc}")
            return len(user_rows), 0

        for row in user_rows:
            day = row.recorded_at.date()
            result = results.get(day)
            if result is None or result.hrv_cv is None:
                print(f"{day} resilience user={row.user_id} stored={row.value} recomputed=NONE MISMATCH")
                mismatches += 1
                continue
            stored_v, recomp_v = _num(row.value), _num(result.hrv_cv)
            stored_c = _components(row.components)
            recomp_c = {
                "days_counted": result.days_counted,
                "metric_type": result.metric_type,
                "resilience_score": result.resilience_score,
            }
            ok = _same_value(row.value, result.hrv_cv) and stored_c == recomp_c
            if ok:
                status = "OK"
            elif _inputs_moved(row.created_at, _resilience_window(day), ingest):
                drift += 1
                status = "DRIFT"
            else:
                mismatches += 1
                status = "MISMATCH"
            print(
                f"{day} resilience user={row.user_id} stored={stored_v}/{stored_c} "
                f"recomputed={recomp_v}/{recomp_c} {status}"
            )
    return mismatches, drift


def check_missing(session: Any) -> tuple[int, int]:
    """Rows the fill tasks would still write today — the writer's own queries, and never a failure.

    Reusing `tasks._MISSING_*_QUERY` rather than a near-copy is the point: the gate's idea of a gap
    is then exactly the writer's, so a date the writer legitimately leaves unscored (insufficient
    HRV yet) cannot be reported here as a parity problem.
    """
    gaps_cutoff = datetime.now(timezone.utc) - timedelta(days=settings.score_backfill_days)
    sleep_gaps = session.execute(_MISSING_SCORES_QUERY, {"cutoff": gaps_cutoff}).fetchall()
    for row in sleep_gaps:
        print(f"{row.wake_date} sleep user={row.user_id} MISSING")

    resilience_gaps = session.execute(
        _MISSING_RESILIENCE_SCORES_QUERY,
        {
            "backfill_days": settings.score_backfill_days,
            "rmssd_id": _RMSSD_TYPE_ID,
            "sdnn_id": _SDNN_TYPE_ID,
            "data_lookback_days": settings.score_backfill_days + resilience_config.lookback_days,
        },
    ).fetchall()
    for row in resilience_gaps:
        print(f"{row.reference_date} resilience user={row.user_id} MISSING")
    return len(sleep_gaps), len(resilience_gaps)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Look-back window (default 30)")
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    with SessionLocal() as session:
        sleep_bad, sleep_drift = check_sleep(session, cutoff)
        resilience_bad, resilience_drift = check_resilience(session, cutoff)
        gaps = sum(check_missing(session))

    print(
        f"parity: {sleep_bad + resilience_bad} mismatch(es), {sleep_drift + resilience_drift} drift "
        f"(inputs rewritten after the row), {gaps} still to fill (informational) "
        f"over the last {args.days} days"
    )
    return 1 if sleep_bad + resilience_bad else 0


if __name__ == "__main__":
    sys.exit(main())
