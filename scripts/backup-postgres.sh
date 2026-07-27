#!/bin/sh
set -eu

output_dir=${1:-artifacts/recovery}
mkdir -p "$output_dir"

if ! docker compose ps --status running --services | grep -qx postgres; then
  echo "postgres service is not running" >&2
  exit 1
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$output_dir/caseops-$timestamp.dump"
manifest="$output_dir/caseops-$timestamp.manifest.json"

docker compose exec -T postgres \
  pg_dump \
  --username=caseops \
  --dbname=caseops \
  --format=custom \
  --compress=6 \
  --no-owner \
  --no-privileges >"$archive"

if command -v sha256sum >/dev/null 2>&1; then
  archive_sha=$(sha256sum "$archive" | awk '{print $1}')
else
  archive_sha=$(shasum -a 256 "$archive" | awk '{print $1}')
fi

revision=$(docker compose exec -T postgres \
  psql --username=caseops --dbname=caseops --tuples-only --no-align \
  --command="select version_num from alembic_version;")

archive_bytes=$(wc -c <"$archive" | tr -d ' ')
cat >"$manifest" <<EOF
{
  "schema_version": "caseops.backup-manifest.v1",
  "created_at": "$timestamp",
  "database": "caseops",
  "alembic_revision": "$revision",
  "archive_format": "postgresql-custom",
  "archive_file": "$(basename "$archive")",
  "archive_bytes": $archive_bytes,
  "sha256": "$archive_sha",
  "restore_command": "scripts/restore-drill-chapter-05.sh $archive"
}
EOF

printf '%s\n' "$archive"
printf '%s\n' "$manifest"
