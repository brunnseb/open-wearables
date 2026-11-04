"""The parity gate's own comparison logic.

It gates a production cutover, so the normalizer that makes a stored JSON component and a
freshly built `ScoreComponent` comparable is pinned here. The first version compared a dict
against a `ScoreComponent` object and reported every sleep row as a mismatch while the numbers
were identical — this test is that bug.
"""

import importlib.util
import sys
from pathlib import Path

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
