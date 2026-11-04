"""Test environment for the package's own tests.

`test_sleep_algorithms.py` imports core's kept `app.schemas.model_crud.activities.sleep.SleepStage`,
which instantiates `app.config.Settings` — and `secret_key` has no default. Upstream's CI supplies
it as an env var (`.github/workflows/ci.yml`); a local checkout has no `backend/config/.env`, so the
tests set the same value here. Runtime settings come from the image env, never from this file.
"""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-ci")
