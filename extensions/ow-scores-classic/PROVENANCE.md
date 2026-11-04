# Provenance

Every source file in this package is copied **verbatim** from upstream
[the-momentum/open-wearables](https://github.com/the-momentum/open-wearables) at tag
`ow-scores-last` (commit `183e8cc4`), which is the last release distributed under the MIT
license reproduced below (© 2025 Momentum). Upstream removes the Open Wearables native Sleep
Score and Resilience Score from the repository on 2026-11-06 and re-ships them as a separate
package under a **different, undisclosed license** — that future package is not MIT, and nothing
here is derived from it. Only import statements were rewritten to point at the vendored modules;
no formula, threshold, or comment was edited. The copied paths, at that tag:

| Vendored as | Upstream path at `ow-scores-last` |
|---|---|
| `ow_scores_classic/algorithms/config.py` | `backend/app/algorithms/config_algorithms.py` |
| `ow_scores_classic/algorithms/sleep.py` | `backend/app/algorithms/sleep.py` |
| `ow_scores_classic/algorithms/resilience.py` | `backend/app/algorithms/resilience.py` |
| `ow_scores_classic/algorithms/primitives.py` | `backend/app/algorithms/scoring_primitives.py` |
| `ow_scores_classic/schemas.py` | `backend/app/schemas/responses/activity/resilience.py` |
| `ow_scores_classic/services/sleep_service.py` | `backend/app/services/scores/sleep_service.py` |
| `ow_scores_classic/services/resilience_service.py` | `backend/app/services/scores/resilience_service.py` |
| `ow_scores_classic/tasks.py` | `backend/app/integrations/celery/tasks/fill_missing_sleep_scores_task.py` and `.../fill_missing_resilience_scores_task.py` |

The tests under `tests/` and `backend/tests/extensions/ow_scores_classic/` are upstream's own
tests, copied with imports rewritten and no assertion changed, so that the numbers this package
produces stay the numbers upstream computed.

## MIT license (upstream `LICENSE` at `ow-scores-last`)

```
MIT License

Copyright (c) 2025 Momentum

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
