#!/bin/sh
# Task 6 image gate (Review Focus #2 + #3), corrected for two defects in the plan's commands:
#  - the image carries no settings, so Settings() needs SECRET_KEY (upstream CI passes the same)
#  - `celery_app` is NOT exported by app.integrations.celery.core (core.py:162 uses
#    current_celery_app inside configure_celery); the app object is app.main:celery_app
set -e
IMG="${1:-ow-scores-scratch:latest}"

echo "== extension discovery =="
docker run --rm -e SECRET_KEY=smoke-test-only --entrypoint python "$IMG" -c \
  "from app.extensions import get_extensions; print([(e.name, e.active, e.error) for e in get_extensions()])"

echo "== beat, core writer ON (core writes: the extension must schedule nothing) =="
docker run --rm -e SECRET_KEY=smoke-test-only --entrypoint python "$IMG" -c \
  "from app.main import celery_app as c; import ow_scores_classic.tasks; \
   print(sorted(n for n in c.tasks if n.startswith('ow_scores_classic'))); \
   bs = sorted(k for k in c.conf.beat_schedule if 'score' in k); \
   print(bs); \
   assert bs == ['fill-missing-resilience-scores', 'fill-missing-sleep-scores'], bs; \
   print('ONE WRITER OK (core)')"

echo "== tasks + beat, core writer OFF (cutover state: exactly the extension's two keys) =="
docker run --rm -e SECRET_KEY=smoke-test-only -e OW_SCORES_ENABLED=false --entrypoint python "$IMG" -c \
  "from app.main import celery_app as c; import ow_scores_classic.tasks; \
   ts = sorted(n for n in c.tasks if n.startswith('ow_scores_classic')); \
   bs = sorted(k for k in c.conf.beat_schedule if 'score' in k); \
   print(ts); print(bs); \
   assert ts == ['ow_scores_classic.fill_missing_resilience_scores', 'ow_scores_classic.fill_missing_sleep_scores'], ts; \
   assert bs == ['ow-scores-classic-fill-missing-resilience-scores', 'ow-scores-classic-fill-missing-sleep-scores'], bs; \
   print('SMOKE OK')"
