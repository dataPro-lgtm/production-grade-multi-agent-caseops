#!/bin/sh
set -eu

if [ "${CASEOPS_RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
  caseops seed
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec uvicorn caseops.api.app:app \
  --host 0.0.0.0 \
  --port 8080 \
  --proxy-headers \
  --forwarded-allow-ips="${CASEOPS_FORWARDED_ALLOW_IPS:-127.0.0.1}"
