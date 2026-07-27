#!/bin/sh
set -eu

archive=${1:?usage: restore-drill-chapter-05.sh <custom-format-dump>}
restore_db=caseops_restore_drill_ch05
started_at=$(date +%s)

if [ ! -f "$archive" ]; then
  echo "backup archive does not exist: $archive" >&2
  exit 1
fi
if ! docker compose ps --status running --services | grep -qx postgres; then
  echo "postgres service is not running" >&2
  exit 1
fi

cleanup() {
  docker compose exec -T postgres \
    dropdb --username=caseops --if-exists --force "$restore_db" >/dev/null
}
trap cleanup EXIT

cleanup
docker compose exec -T postgres \
  createdb --username=caseops --template=template0 "$restore_db"
docker compose exec -T postgres \
  pg_restore \
  --username=caseops \
  --dbname="$restore_db" \
  --no-owner \
  --no-privileges \
  --exit-on-error <"$archive"

signature_sql=$(cat <<'SQL'
with snapshot(entity, value) as (
  select 'case', id || ':' || tenant_id || ':' || status from cases
  union all
  select 'investigation', id || ':' || request_hash from investigations
  union all
  select 'agent_run', id || ':' || request_hash from agent_runs
  union all
  select 'collaboration_run', id || ':' || request_hash from collaboration_runs
  union all
  select 'context_run', id || ':' || request_hash from context_runs
  union all
  select 'knowledge_object', object_id || ':' || content_hash from knowledge_objects
  union all
  select 'security_decision', id || ':' || context_digest from security_decisions
  union all
  select 'system_run', id || ':' || request_hash from system_runs
  union all
  select 'system_step', id || ':' || step_key || ':' || status from system_steps
  union all
  select 'runtime_context_node', id || ':' || node_key || ':' || payload_digest from runtime_context_nodes
  union all
  select 'runtime_context_edge', id || ':' || edge_key from runtime_context_edges
  union all
  select 'outbox', id || ':' || topic || ':' || aggregate_id from outbox_events
)
select md5(coalesce(string_agg(entity || ':' || value, E'\n' order by entity, value), ''))
from snapshot;
SQL
)

source_signature=$(docker compose exec -T postgres \
  psql --username=caseops --dbname=caseops --tuples-only --no-align \
  --command="$signature_sql")
source_revision=$(docker compose exec -T postgres \
  psql --username=caseops --dbname=caseops --tuples-only --no-align \
  --command="select version_num from alembic_version;")
restored_signature=$(docker compose exec -T postgres \
  psql --username=caseops --dbname="$restore_db" --tuples-only --no-align \
  --command="$signature_sql")
restored_revision=$(docker compose exec -T postgres \
  psql --username=caseops --dbname="$restore_db" --tuples-only --no-align \
  --command="select version_num from alembic_version;")
case_count=$(docker compose exec -T postgres \
  psql --username=caseops --dbname="$restore_db" --tuples-only --no-align \
  --command="select count(*) from cases where tenant_id='tenant-demo' and case_id='C-102';")

if [ "$source_signature" != "$restored_signature" ]; then
  echo "restore signature does not match the source database" >&2
  exit 1
fi
if [ "$restored_revision" != "$source_revision" ]; then
  echo "restored schema revision does not match the source database" >&2
  exit 1
fi
if [ "$case_count" != "1" ]; then
  echo "restored business invariant failed for tenant-demo/C-102" >&2
  exit 1
fi

finished_at=$(date +%s)
rto_seconds=$((finished_at - started_at))
cat <<EOF
{
  "schema_version": "caseops.restore-evidence.v1",
  "status": "passed",
  "source_database": "caseops",
  "isolated_restore_database": "$restore_db",
  "alembic_revision": "$restored_revision",
  "content_signature": "$restored_signature",
  "business_invariants": {
    "tenant-demo/C-102": true
  },
  "measured_rto_seconds": $rto_seconds
}
EOF
