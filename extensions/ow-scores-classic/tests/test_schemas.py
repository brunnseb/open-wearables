"""The resilience result types keep the exact shape core's API returned.

The key sets below are read from `backend/app/schemas/responses/activity/resilience.py` at
`ow-scores-last` — the file upstream deletes with the score removal. `components`/`metric_type`
names reach the portal and the MCP server, so a rename here is an API break.
"""

from datetime import date

from ow_scores_classic.schemas import DailyHrvScore, HrvCvScoreResult

CORE_DAILY_KEYS = {"date", "hrv_value_ms", "has_data"}
CORE_RESULT_KEYS = {
    "hrv_cv",
    "resilience_score",
    "metric_type",
    "days_counted",
    "lookback_days",
    "daily_scores",
}


def _result() -> HrvCvScoreResult:
    return HrvCvScoreResult(
        hrv_cv=0.087,
        resilience_score=95,
        metric_type="RMSSD",
        days_counted=7,
        lookback_days=30,
        daily_scores=[
            DailyHrvScore(date=date(2026, 11, 2), hrv_value_ms=42.1, has_data=True),
            DailyHrvScore(date=date(2026, 11, 1), hrv_value_ms=None, has_data=False),
        ],
    )


def test_hrv_result_serialises_like_core() -> None:
    dumped = _result().model_dump()

    assert set(dumped) == CORE_RESULT_KEYS
    assert all(set(d) == CORE_DAILY_KEYS for d in dumped["daily_scores"])
    # Stored as NUMERIC(6,3) and shown as a percentage: the value itself stays a fraction.
    assert dumped["hrv_cv"] == 0.087
    assert dumped["daily_scores"][1]["hrv_value_ms"] is None
    assert dumped["daily_scores"][1]["has_data"] is False


def test_daily_score_builds_from_an_orm_row_like_core() -> None:
    """`from_attributes=True` is how the service builds these from `SeriesSample` rows."""

    class Row:
        date = date(2026, 11, 2)
        hrv_value_ms = 42.1
        has_data = True

    assert DailyHrvScore.model_validate(Row()).hrv_value_ms == 42.1
