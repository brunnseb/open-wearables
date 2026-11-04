"""Parity gate: do the vendored services reproduce the scores already stored in this database?

Run it against the real database BEFORE the cutover. It re-runs the same code path the Celery
tasks use (`build_internal_sleep_scores` / `get_hrv_cv_scores_for_date_range`) for every
`provider='internal'` row in the window and compares the stored `value` and `components` with the
recomputed ones. The algorithms are copied verbatim, so any mismatch means an import/copy went
wrong — investigate it, never "tune" a number.

Read-only: it writes nothing.

One line per row:  date  category  stored  recomputed  OK|MISSING|MISMATCH|DRIFT
Exit 0 when every stored score in a closed window is reproduced; 1 on any MISMATCH.

Resilience rows whose reference date falls inside the still-open lookback window
(`resilience_config.lookback_days`) are reported as DRIFT, not MISMATCH: that window keeps
receiving syncs after the row was written, so a later recomputation legitimately sees more HRV
days than the run that produced the row. A MISMATCH there means the vendored code differs.
MISSING (nothing stored for a date the services would score) is informational, not a failure.
"""

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from ow_scores_classic.algorithms.config import resilience_config
from ow_scores_classic.services.resilience_service import resilience_score_service
from ow_scores_classic.services.sleep_service import sleep_score_service
from sqlalchemy import text

from app.database import SessionLocal

# Same rows the fill tasks write, so the comparison is against the task's own code path.
_STORED_SLEEP = text("""
    SELECT hs.user_id,
           hs.event_record_id,
           hs.value,
           hs.components,
           hs.recorded_at,
           er.data_source_id,
           (er.end_datetime + COALESCE(er.zone_offset, '+00:00')::interval) AS local_end
    FROM health_score hs
    JOIN event_record er ON er.id = hs.event_record_id
    WHERE hs.provider = 'internal' AND hs.category = 'sleep' AND hs.recorded_at >= :cutoff
    ORDER BY hs.user_id, hs.recorded_at
""")

_STORED_RESILIENCE = text("""
    SELECT user_id, recorded_at, value, components
    FROM health_score
    WHERE provider = 'internal' AND category = 'resilience' AND recorded_at >= :cutoff
    ORDER BY user_id, recorded_at
""")

# Stored as NUMERIC(6,3): the DB rounds, so compare at the stored precision.
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


def check_sleep(db: Any, cutoff: datetime) -> tuple[int, int]:
    rows = db.execute(_STORED_SLEEP, {"cutoff": cutoff}).fetchall()
    by_user: dict[UUID, list[Any]] = defaultdict(list)
    for row in rows:
        by_user[UUID(str(row.user_id))].append(row)

    mismatches = 0
    for uid, user_rows in by_user.items():
        sessions = [(UUID(str(r.event_record_id)), UUID(str(r.data_source_id)), r.local_end) for r in user_rows]
        try:
            built = {s.event_record_id: s for s in sleep_score_service.build_internal_sleep_scores(db, uid, sessions)}
        except Exception as exc:  # a recomputation that cannot run is a parity failure, not a skip
            print(f"{cutoff.date()} sleep RECOMPUTE-ERROR {exc}")
            return len(user_rows), 0

        for row in user_rows:
            record_id = UUID(str(row.event_record_id))
            created = built.get(record_id)
            day = row.recorded_at.date()
            if created is None:
                print(f"{day} sleep stored={row.value} recomputed=NONE MISMATCH")
                mismatches += 1
                continue
            stored_v, recomp_v = _num(row.value), _num(created.value)
            stored_c, recomp_c = _components(row.components), _components(created.components)
            ok = _num(stored_v) == _num(recomp_v) and stored_c == recomp_c
            if not ok:
                mismatches += 1
            print(
                f"{day} sleep stored={stored_v}/{stored_c} recomputed={recomp_v}/{recomp_c} "
                f"{'OK' if ok else 'MISMATCH'}"
            )
    return mismatches, 0


def check_resilience(db: Any, cutoff: datetime, timezone_: ZoneInfo, now: datetime | None = None) -> tuple[int, int]:
    """Returns (mismatches, drift) — drift is a score in a lookback window that is still open."""
    rows = db.execute(_STORED_RESILIENCE, {"cutoff": cutoff}).fetchall()
    by_user: dict[UUID, list[Any]] = defaultdict(list)
    for row in rows:
        by_user[UUID(str(row.user_id))].append(row)

    mismatches = 0
    drift = 0
    # Rows are dated in the user's zone, so "today" has to be their today: the server's UTC date
    # is a day behind for every zone east of Greenwich and shifts the drift/mismatch boundary.
    today = (now or datetime.now(timezone_)).date()
    for uid, user_rows in by_user.items():
        ref_dates: list[date] = sorted({r.recorded_at.date() for r in user_rows})
        try:
            results = resilience_score_service.get_hrv_cv_scores_for_date_range(db, uid, ref_dates)
        except Exception as exc:
            print(f"{cutoff.date()} resilience RECOMPUTE-ERROR {exc}")
            return len(user_rows), 0

        for row in user_rows:
            day = row.recorded_at.date()
            result = results.get(day)
            if result is None or result.hrv_cv is None:
                print(f"{day} resilience stored={row.value} recomputed=NONE MISMATCH")
                mismatches += 1
                continue
            stored_v, recomp_v = _num(row.value), _num(result.hrv_cv)
            stored_c = _components(row.components)
            recomp_c = {
                "days_counted": result.days_counted,
                "metric_type": result.metric_type,
                "resilience_score": result.resilience_score,
            }
            ok = abs(stored_v - recomp_v) <= _TOLERANCE and stored_c == recomp_c
            if ok:
                status = "OK"
            elif (today - day).days <= resilience_config.lookback_days:
                # That window has received syncs since the row was written; the numbers may move.
                drift += 1
                status = "DRIFT"
            else:
                mismatches += 1
                status = "MISMATCH"
            print(f"{day} resilience stored={stored_v}/{stored_c} recomputed={recomp_v}/{recomp_c} {status}")
    return mismatches, drift


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Look-back window (default 30)")
    parser.add_argument("--timezone", default="Europe/Vienna", help="User timezone (default Europe/Vienna)")
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    with SessionLocal() as db:
        sleep_bad, _ = check_sleep(db, cutoff)
        resilience_bad, resilience_drift = check_resilience(db, cutoff, ZoneInfo(args.timezone))

    print(
        f"parity: {sleep_bad + resilience_bad} mismatch(es), {resilience_drift} drift "
        f"(open lookback window) over the last {args.days} days"
    )
    return 1 if sleep_bad + resilience_bad else 0


if __name__ == "__main__":
    sys.exit(main())
