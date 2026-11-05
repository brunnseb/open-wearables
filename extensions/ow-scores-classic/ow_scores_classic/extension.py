"""The extension entry point core discovers through the `open_wearables.extensions` group.

Core's contract (`backend/app/extensions/__init__.py`): the subclass must be a `@dataclass`
(core disables it otherwise), `name` must fullmatch `[a-z][a-z0-9_]*`, and a beat entry may use
only Celery's documented fields. `requires_core` is the safety net for Review Focus #1: on a core
that has removed `app/algorithms` and `app/services/scores`, core logs "Extension disabled" and
keeps serving, instead of this package importing modules that no longer exist.
"""

from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.extensions import OWExtension
from ow_scores_classic.tasks import FILL_MISSING_RESILIENCE_SCORES, FILL_MISSING_SLEEP_SCORES

SLEEP_BEAT_KEY = "ow-scores-classic-fill-missing-sleep-scores"
RESILIENCE_BEAT_KEY = "ow-scores-classic-fill-missing-resilience-scores"

# Core ran both fills on `settings.{sleep,resilience}_score_interval_seconds`; both default to
# 600 s and the fork pins one number so the beat entries stay identical across core releases that
# rename those settings.
_FILL_INTERVAL_SECONDS = 600.0


@dataclass
class OwScoresClassicExtension(OWExtension):
    name: str = "ow_scores_classic"
    display_name: str = "OW Scores (classic)"
    version: str = "0.1.0"
    requires_core: str = ">=0.11,<0.12"
    celery_task_packages: list[str] = field(default_factory=lambda: ["ow_scores_classic"])
    # Writes only into the existing `health_score` table, through core's service: no own history.
    migrations: str = ""

    def beat_schedule(self) -> dict[str, dict[str, Any]]:
        """Our two fills, but only while core's own score writer is off.

        Core adds its own two entries under `if settings.ow_scores_enabled` and merges extension
        entries unconditionally, so emitting ours unconditionally would ship an image with two
        writers on the same beat and leave the one-writer rule to a manual `.env` flip. The flag
        selects WHO writes: on = core's tasks, off = this extension's.
        """
        if settings.ow_scores_enabled:
            return {}
        return {
            SLEEP_BEAT_KEY: {
                "task": FILL_MISSING_SLEEP_SCORES,
                "schedule": _FILL_INTERVAL_SECONDS,
                "args": (),
                "kwargs": {},
            },
            RESILIENCE_BEAT_KEY: {
                "task": FILL_MISSING_RESILIENCE_SCORES,
                "schedule": _FILL_INTERVAL_SECONDS,
                "args": (),
                "kwargs": {},
            },
        }
