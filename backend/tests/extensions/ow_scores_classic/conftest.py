"""Put the vendored package on `sys.path` so these tests can import it without an install.

CI runs `uv sync --group dev --extra otel` before pytest, and `uv sync` enforces
`backend/pyproject.toml`'s lock — which deliberately does not depend on `ow-scores-classic` (the
fork must not touch core's manifest). Without this, all three modules here fail to collect with
`ModuleNotFoundError: No module named 'ow_scores_classic'` and the whole CI job goes red.

Caveat: that makes CI test the SOURCE TREE, not a built wheel. Entry-point discovery — core finding
the extension through the `open_wearables.extensions` group — is not covered here; it stays covered
by the image smoke test (`scripts/smoke.sh` in the SDD folder), which runs against the installed
package inside the published image. A local dev checkout may instead `uv pip install -e
../extensions/ow-scores-classic`; `uv sync` prunes that install again.
"""

import sys
from pathlib import Path

_SOURCE_TREE = Path(__file__).resolve().parents[4] / "extensions" / "ow-scores-classic"
if str(_SOURCE_TREE) not in sys.path:
    sys.path.insert(0, str(_SOURCE_TREE))
