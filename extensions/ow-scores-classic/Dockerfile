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
COPY extensions/ /opt/extensions/
RUN uv pip install --python /opt/venv/bin/python /opt/extensions/ow-scores-classic
