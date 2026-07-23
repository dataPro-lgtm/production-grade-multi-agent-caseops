#!/bin/sh
set -eu

alembic upgrade head
caseops seed

exec uvicorn caseops.api.app:app \
  --host 0.0.0.0 \
  --port 8080 \
  --proxy-headers \
  --forwarded-allow-ips="${CASEOPS_FORWARDED_ALLOW_IPS:-127.0.0.1}"
