# Coding standards

The rules review checks code against. Anything tooling enforces (ruff, ty, prettier, the pre-commit
hooks) is deliberately absent — see [contributing/linting.md](contributing/linting.md).

## Backend

**Error text reaches the integrator verbatim.** `detail` strings, webhook payload fields and log lines are
API surface: they are copy-pasted into ticket threads and searched in logs, so they name the provider, the
endpoint and the received value (status code, actual size) instead of a paraphrase. A helper's message is
rewritten when it crosses into a route response — what a helper raises underneath is not what the caller
receives; trace the path that builds the response.

**A user-visible change needs its docs in the same change set.** If an integrator or operator can see or
configure it — endpoint, error shape, env var, webhook event, payload field — the matching page under
`docs/` and `docs/api-reference/openapi.json` move with the code. Internal mechanisms don't need docs.

## Verifying

`uv run --with pre-commit pre-commit run --from-ref origin/main --to-ref HEAD`, run from the **repo root**.
From `backend/` the hooks' path filters silently match nothing for files outside `backend/` and the run
reports success having checked nothing.
