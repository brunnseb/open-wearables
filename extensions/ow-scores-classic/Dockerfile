# One added layer on the published upstream image: install this package into core's venv.
# No app rebuild, no USER, no entrypoint change — upstream stays the source of the application.
# Build with the repo root as the context:
#   docker build -f extensions/ow-scores-classic/Dockerfile -t ow-scores-scratch:latest .
#
# The base is pinned by digest: a mutable tag would let a rebuild silently pick up a new upstream
# build, and the vendored score code is verified against one specific core version. Re-pin both
# together, and re-run the parity gate against the production DB first.
ARG UPSTREAM_TAG=0.11.0
ARG UPSTREAM_DIGEST=sha256:aaa0ccc7e6dcfbb3a36db6e0f63ec2782cf31a5bbd7cb466d11cd8d4657bc6dd
FROM themomentum/open-wearables-backend:${UPSTREAM_TAG}@${UPSTREAM_DIGEST}
# Not `COPY extensions/`: the whole tree is untracked-exempt, so a local drop-in extension, a stale
# wheel in dist/ or the package's own .venv would be baked into an image built from a clean checkout
# (all three were, until this line named what it takes). Only the package itself and its attribution
# go in; PROVENANCE.md is the MIT notice the vendored code must ship with.
COPY extensions/ow-scores-classic/pyproject.toml extensions/ow-scores-classic/PROVENANCE.md /opt/extensions/ow-scores-classic/
COPY extensions/ow-scores-classic/ow_scores_classic/ /opt/extensions/ow-scores-classic/ow_scores_classic/
RUN uv pip install --python /opt/venv/bin/python /opt/extensions/ow-scores-classic
