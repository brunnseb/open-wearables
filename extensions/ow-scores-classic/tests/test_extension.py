"""Core discovers this extension, schedules its two beat entries, and switches it off on a new core.

These are Review Focus #1 and #3: on core 0.12/1.0 (which deletes `app/algorithms` and
`app/services/scores`) the app must load with the extension disabled rather than crash, and
core's own score tasks must not be scheduled alongside these ones — `ow_scores_enabled` selects
WHICH of the two writers runs, in code, not in a `.env` file.
"""

import pytest
from ow_scores_classic.extension import RESILIENCE_BEAT_KEY, SLEEP_BEAT_KEY, OwScoresClassicExtension

from app.config import settings
from app.extensions import collect_beat_schedule, get_extensions

BEAT_KEYS = {
    "ow-scores-classic-fill-missing-sleep-scores",
    "ow-scores-classic-fill-missing-resilience-scores",
}
TASK_NAMES = {
    "ow_scores_classic.fill_missing_sleep_scores",
    "ow_scores_classic.fill_missing_resilience_scores",
}


@pytest.fixture
def extension_is_the_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cutover state: core's own score writer is off, so these beat entries are the only writer."""
    monkeypatch.setattr(settings, "ow_scores_enabled", False)


def test_no_beat_entries_while_core_still_writes_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one-writer invariant, in code: the flag selects WHO writes, so two can never run.

    Core adds its own two entries only under `if settings.ow_scores_enabled` and merges extension
    entries unconditionally, so returning ours unconditionally shipped an image with both writers
    scheduled and left the pairing to a manual `.env` flip.
    """
    monkeypatch.setattr(settings, "ow_scores_enabled", True)

    assert OwScoresClassicExtension().beat_schedule() == {}
    assert not BEAT_KEYS & set(collect_beat_schedule())


@pytest.mark.usefixtures("extension_is_the_writer")
def test_beat_entries_use_only_documented_fields() -> None:
    sched = OwScoresClassicExtension().beat_schedule()
    assert set(sched) == {
        "ow-scores-classic-fill-missing-sleep-scores",
        "ow-scores-classic-fill-missing-resilience-scores",
    }
    for entry in sched.values():
        assert set(entry) <= {"task", "schedule", "args", "kwargs", "options", "relative"}
        assert entry["schedule"] == 600.0
    assert {entry["task"] for entry in sched.values()} == TASK_NAMES


@pytest.mark.usefixtures("extension_is_the_writer")
def test_beat_intervals_come_from_cores_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The box's only score writer must honour the cadence core's settings ask for.

    The fork pinned one hardcoded 600 s, which silently ignored `SLEEP_SCORE_INTERVAL_SECONDS` /
    `RESILIENCE_SCORE_INTERVAL_SECONDS` — still real settings in this core, and the operator's only
    knob for how often the fills run.
    """
    monkeypatch.setattr(settings, "sleep_score_interval_seconds", 900)
    monkeypatch.setattr(settings, "resilience_score_interval_seconds", 1200)

    sched = OwScoresClassicExtension().beat_schedule()

    assert sched[SLEEP_BEAT_KEY]["schedule"] == 900.0
    assert sched[RESILIENCE_BEAT_KEY]["schedule"] == 1200.0


@pytest.mark.usefixtures("extension_is_the_writer")
def test_core_discovers_the_extension() -> None:
    """The entry point is installed, so core loads it and schedules its beats."""
    get_extensions.cache_clear()
    mine = [e for e in get_extensions() if e.name == "ow_scores_classic"]

    assert mine, f"extension not discovered: {[e.name for e in get_extensions()]}"
    assert mine[0].active, f"extension disabled: {mine[0].error}"
    assert mine[0].extension is not None
    assert mine[0].extension.requires_core == ">=0.11,<0.12"

    sched = collect_beat_schedule()
    assert set(sched) >= BEAT_KEYS, f"beats not collected: {sorted(sched)}"


@pytest.mark.usefixtures("extension_is_the_writer")
def test_core_disables_the_extension_on_a_newer_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Core 0.12 removes the modules this package imports: it must disable, not crash the app."""
    monkeypatch.setattr("app.extensions.core_version", "0.12.0")
    get_extensions.cache_clear()
    try:
        mine = [e for e in get_extensions() if e.name == "ow_scores_classic"]

        assert mine
        assert not mine[0].active
        assert "requires core" in (mine[0].error or "")
        # Disabled extensions contribute no beat entries, so core's tasks stay the only writers.
        assert not (BEAT_KEYS & set(collect_beat_schedule()))
    finally:
        get_extensions.cache_clear()
