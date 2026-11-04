# One added layer on the published upstream image: install this package into core's venv.
# No app rebuild, no USER, no entrypoint change — upstream stays the source of the application.
# Build with the repo root as the context:
#   docker build -f extensions/ow-scores-classic/Dockerfile -t ow-scores-scratch:latest .
ARG UPSTREAM_TAG=0.11.0
FROM themomentum/open-wearables-backend:${UPSTREAM_TAG}
COPY extensions/ /opt/extensions/
RUN uv pip install --python /opt/venv/bin/python /opt/extensions/ow-scores-classic
