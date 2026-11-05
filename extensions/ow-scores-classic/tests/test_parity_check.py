"""The parity gate's own comparison logic.

It gates a production cutover, so the normalizer that makes a stored JSON component and a
freshly built `ScoreComponent` comparable is pinned here. The first version compared a dict
against a `ScoreComponent` object and reported every sleep row as a mismatch while the numbers
were identical — this test is that bug. The OK|MISSING|MISMATCH|DRIFT rule is pinned here too, and
in particular that DRIFT means "the inputs moved after this row was written", not "this row is
old".
"""

import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

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
    """Stands in for a Session, answering each of the gate's selects with its own rows.

    The gate reads two kinds of thing — the stored scores and the per-day ingest times — and a stub
    that answered both with the same rows would feed score rows into the drift rule and pass on
    anything.
    """

    def __init__(self, stored: list[object], ingest: list[tuple[date, datetime]] | None = None) -> None:
        self._stored = stored
        self._ingest: list[object] = list(ingest or [])
        self._pending: list[object] = []

    def execute(self, statement: Any, *_a: object, **_k: object) -> "_StubDB":
        is_stored = statement is parity._STORED_SLEEP or statement is parity._STORED_RESILIENCE
        self._pending = self._stored if is_stored else self._ingest
        return self

    def fetchall(self) -> list[object]:
        return self._pending


_USER = UUID("00000000-0000-0000-0000-000000000001")
# The fill task's own run: 00:03 on the night the row covers. Nothing before this can explain a
# different recomputation; anything after it can.
_ROW_WRITTEN = datetime(2026, 11, 3, 0, 3, tzinfo=timezone.utc)


def _resilience_row(day: date, value: str, created_at: datetime = _ROW_WRITTEN) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=_USER,
        recorded_at=datetime(day.year, day.month, day.day, 0, 0, tzinfo=timezone.utc),
        created_at=created_at,
        value=Decimal(value),
        # The real `components` JSON shape, so these rows differ from a recomputation only on the
        # axis each test is about.
        components={
            "days_counted": {"value": 14},
            "metric_type": {"qualifier": "hrv"},
            "resilience_score": {"value": 62.0},
        },
    )


def _sleep_row(day: date, value: str, created_at: datetime = _ROW_WRITTEN) -> SimpleNamespace:
    local_end = datetime(day.year, day.month, day.day, 7, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        user_id=_USER,
        event_record_id=_USER,
        data_source_id=_USER,
        recorded_at=local_end,
        created_at=created_at,
        local_start=local_end - timedelta(days=1, hours=8),  # the night the session started on
        local_end=local_end,
        value=Decimal(value),
        components={},
    )


def _stub_results(_db: Any, _uid: Any, days: Any) -> dict[date, Any]:
    return {d: SimpleNamespace(hrv_cv=9.0, days_counted=14, metric_type="hrv", resilience_score=62.0) for d in days}


def test_a_mismatch_whose_inputs_moved_after_the_row_is_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """DRIFT means "the inputs moved under the row", whatever the row's age.

    The row was written 2026-11-03 00:03; the RMSSD samples for 2026-11-02 — a day inside this
    row's own lookback window — arrived on 2026-11-04, after it. A recomputation legitimately sees
    samples the writing run never had. Age is not the question: read 8 days later, or 8 days
    earlier, this row is drift because of *when the ingest happened relative to the row*.
    """
    monkeypatch.setattr(parity.resilience_config, "lookback_days", 1)
    monkeypatch.setattr(parity.resilience_score_service, "get_hrv_cv_scores_for_date_range", _stub_results)
    db = _StubDB(
        [_resilience_row(date(2026, 11, 3), "12.0")],  # stored 12.0, recomputed 9.0
        ingest=[(date(2026, 11, 2), datetime(2026, 11, 4, 9, 47, tzinfo=timezone.utc))],
    )

    assert parity.check_resilience(db, datetime(2026, 10, 1, tzinfo=timezone.utc)) == (0, 1)


def test_a_mismatch_whose_inputs_never_moved_stays_a_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The false-DRIFT half of the old age rule: a young row is not a forgiven row.

    Nothing inside the row's own window was written after it, so a recomputation that disagrees is
    a vendoring bug. Data that did arrive after the row but on a day the row never read (2026-10-20,
    outside this 1-day window) cannot explain the difference either.
    """
    monkeypatch.setattr(parity.resilience_config, "lookback_days", 1)
    monkeypatch.setattr(parity.resilience_score_service, "get_hrv_cv_scores_for_date_range", _stub_results)
    db = _StubDB(
        [_resilience_row(date(2026, 11, 3), "12.0")],
        ingest=[
            (date(2026, 11, 2), datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)),  # in window, before the row
            (date(2026, 10, 20), datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)),  # after the row, outside
        ],
    )

    assert parity.check_resilience(db, datetime(2026, 10, 1, tzinfo=timezone.utc)) == (1, 0)


def test_a_sleep_mismatch_whose_session_was_resynced_after_the_row_is_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`check_sleep` had no stale allowance at all, though the same mechanism moves its numbers.

    The night's own record was re-pushed (a provider correction, a re-sync) after the score row was
    written, so its sleep_details — the only input the sleep score has — are newer than the row.
    """
    monkeypatch.setattr(
        parity.sleep_score_service,
        "build_internal_sleep_scores",
        lambda _db, _uid, sessions: [
            SimpleNamespace(event_record_id=rid, value=70.0, components={}) for rid, _ds, _end in sessions
        ],
    )
    row = _sleep_row(date(2026, 11, 3), "80.0")  # stored 80.0, recomputed 70.0
    db = _StubDB(
        [row],
        ingest=[(row.local_start.date(), datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc))],  # re-sync, after the row
    )

    assert parity.check_sleep(db, datetime(2026, 10, 1, tzinfo=timezone.utc)) == (0, 1)


def test_a_value_that_only_differs_by_how_the_last_digit_rounded_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """`value` is NUMERIC(6,3): Postgres rounded half-away-from-zero at write time, `round()` is banker's.

    A CV of 0.1235 was stored as 0.124 and recomputes to 0.123, so comparing the two *rounded* copies
    — which is what `abs(stored - _num(recomputed)) <= 0.0005` degenerates to, since both sides then
    carry exactly three decimals — calls a faithful recomputation a MISMATCH. The stored number may
    only be off by half its last digit, which is what the comparison has to allow.
    """
    monkeypatch.setattr(parity.resilience_config, "lookback_days", 1)
    monkeypatch.setattr(
        parity.resilience_score_service,
        "get_hrv_cv_scores_for_date_range",
        lambda _db, _uid, days: {
            d: SimpleNamespace(hrv_cv=0.1235, days_counted=14, metric_type="hrv", resilience_score=62.0) for d in days
        },
    )
    db = _StubDB([_resilience_row(date(2026, 11, 3), "0.124")])

    assert parity.check_resilience(db, datetime(2026, 10, 1, tzinfo=timezone.utc)) == (0, 0)


def test_a_sleep_value_that_only_differs_by_the_last_digit_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sleep path compared the two rounded copies exactly, so the same boundary was a MISMATCH."""
    monkeypatch.setattr(
        parity.sleep_score_service,
        "build_internal_sleep_scores",
        lambda _db, _uid, sessions: [
            SimpleNamespace(event_record_id=rid, value=80.0015, components={}) for rid, _ds, _end in sessions
        ],
    )
    db = _StubDB([_sleep_row(date(2026, 11, 3), "80.002")])

    assert parity.check_sleep(db, datetime(2026, 10, 1, tzinfo=timezone.utc)) == (0, 0)


def test_a_recompute_that_cannot_run_counts_as_a_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both checks return (mismatches, drift), and a crash is never a silent 0."""

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("no such table: sleep_details")

    monkeypatch.setattr(parity.sleep_score_service, "build_internal_sleep_scores", _boom)
    db = _StubDB([_sleep_row(date(2026, 11, 3), "80.0")])

    assert parity.check_sleep(db, datetime(2026, 10, 1, tzinfo=timezone.utc)) == (1, 0)
