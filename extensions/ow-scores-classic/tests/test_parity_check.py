"""The parity gate's own comparison logic.

It gates a production cutover, so the normalizer that makes a stored JSON component and a
freshly built `ScoreComponent` comparable is pinned here. The first version compared a dict
against a `ScoreComponent` object and reported every sleep row as a mismatch while the numbers
were identical — this test is that bug.
"""

import importlib.util
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.schemas.model_crud.activities import ScoreComponent

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "parity_check.py"
_spec = importlib.util.spec_from_file_location("parity_check", _SCRIPT)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise RuntimeError(f"cannot import {_SCRIPT}")
parity = importlib.util.module_from_spec(_spec)
sys.modules["parity_check"] = parity
_spec.loader.exec_module(parity)


def test_stored_json_and_built_component_compare_equal() -> None:
    stored = {"duration": {"value": 100, "qualifier": None}, "metric_type": {"value": None, "qualifier": "RMSSD"}}
    built = {
        "duration": ScoreComponent(value=100, qualifier=None),
        "metric_type": ScoreComponent(value=None, qualifier="RMSSD"),
    }

    expected = {"duration": 100, "metric_type": "RMSSD"}
    assert parity._components(stored) == expected
    assert parity._components(built) == expected


def test_numeric_compare_works_across_decimal_and_float() -> None:
    # Stored as NUMERIC(6,3) -> Decimal; the service returns a float.
    assert parity._num("0.095") == parity._num(0.095)
    assert parity._num(0.0950000001) == parity._num(0.095)


class _StubDB:
    """Stands in for a Session: the checks only call execute(...).fetchall()."""

    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def execute(self, *_a: object, **_k: object) -> "_StubDB":
        return self

    def fetchall(self) -> list[object]:
        return self._rows


_SG = ZoneInfo("Asia/Singapore")  # UTC+8: the server's UTC date is a day behind the user's
_USER = UUID("00000000-0000-0000-0000-000000000001")


def _resilience_row(day: date, value: str) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=_USER,
        recorded_at=datetime(day.year, day.month, day.day, 0, 0, tzinfo=_SG),
        value=Decimal(value),
        components={"days_counted": 14, "metric_type": "hrv", "resilience_score": 62.0},
    )


def test_resilience_drift_window_is_the_users_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale mismatch must not be masked as drift because the server clock is a day behind.

    Now is 2026-11-05 00:30 +08:00, which is still 2026-11-04 in UTC. With `date.today()` the
    reference day 2026-11-03 looks 1 day old instead of 2, falls inside the 1-day open window and
    is reported as DRIFT — the gate then exits 0 over a real mismatch.
    """
    monkeypatch.setattr(parity.resilience_config, "lookback_days", 1)
    monkeypatch.setattr(
        parity.resilience_score_service,
        "get_hrv_cv_scores_for_date_range",
        lambda _db, _uid, _days: {
            date(2026, 11, 3): SimpleNamespace(hrv_cv=9.0, days_counted=14, metric_type="hrv", resilience_score=62.0)
        },
    )
    db = _StubDB([_resilience_row(date(2026, 11, 3), "12.0")])  # stored 12.0, recomputed 9.0

    mismatches, drift = parity.check_resilience(
        db, datetime(2026, 10, 1, tzinfo=timezone.utc), _SG, now=datetime(2026, 11, 5, 0, 30, tzinfo=_SG)
    )

    assert (mismatches, drift) == (1, 0)


def test_resilience_inside_the_open_window_is_drift_not_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row the DRIFT branch exists for: 2026-11-04 is the user's yesterday, inside the window."""
    monkeypatch.setattr(parity.resilience_config, "lookback_days", 1)
    monkeypatch.setattr(
        parity.resilience_score_service,
        "get_hrv_cv_scores_for_date_range",
        lambda _db, _uid, _days: {
            date(2026, 11, 4): SimpleNamespace(hrv_cv=9.0, days_counted=14, metric_type="hrv", resilience_score=62.0)
        },
    )
    db = _StubDB([_resilience_row(date(2026, 11, 4), "12.0")])

    mismatches, drift = parity.check_resilience(
        db, datetime(2026, 10, 1, tzinfo=timezone.utc), _SG, now=datetime(2026, 11, 5, 0, 30, tzinfo=_SG)
    )

    assert (mismatches, drift) == (0, 1)


def test_a_recompute_that_cannot_run_counts_as_a_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both checks return (mismatches, drift), and a crash is never a silent 0."""
    row = SimpleNamespace(
        user_id=_USER,
        event_record_id=_USER,
        data_source_id=_USER,
        local_end=datetime(2026, 11, 3, tzinfo=timezone.utc),
        recorded_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
        value=Decimal("80.0"),
        components={},
    )

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("no such table: sleep_details")

    monkeypatch.setattr(parity.sleep_score_service, "build_internal_sleep_scores", _boom)

    assert parity.check_sleep(_StubDB([row]), datetime(2026, 10, 1, tzinfo=timezone.utc)) == (1, 0)
