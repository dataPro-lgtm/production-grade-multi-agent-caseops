#!/bin/sh
set -eu

api_url=${CASEOPS_API_URL:-http://127.0.0.1:8080}
tempo_url=${CASEOPS_TEMPO_URL:-http://127.0.0.1:3200}
prometheus_url=${CASEOPS_PROMETHEUS_URL:-http://127.0.0.1:9090}
trace_id=11111111111111111111111111111115
parent_span_id=2222222222222215
work_dir=$(mktemp -d)
backup_dir="$work_dir/backup"
headers_file="$work_dir/headers"
body_file="$work_dir/body"

cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT

curl --fail --silent --show-error "$api_url/health/live" >"$work_dir/live.json"
curl --fail --silent --show-error "$api_url/health/startup" >"$work_dir/startup.json"
curl --fail --silent --show-error "$api_url/health/ready" >"$work_dir/ready.json"

python3 - "$work_dir" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
startup = json.loads((root / "startup.json").read_text())
ready = json.loads((root / "ready.json").read_text())
assert startup["status"] == "ok", startup
assert startup["checks"][0]["name"] == "database_schema", startup
assert "revision 0005" in startup["checks"][0]["detail"], startup
assert ready["status"] == "ok", ready
checks = {item["name"]: item["status"] for item in ready["checks"]}
assert checks == {"database": "ok", "mcp": "ok", "a2a": "ok"}, checks
print("startup and dependency-aware readiness accepted")
PY

expired_status=$(curl --silent --show-error --output "$work_dir/expired.json" \
  --write-out '%{http_code}' \
  --header 'X-Request-Deadline: 2020-01-01T00:00:00Z' \
  "$api_url/health/live")
if [ "$expired_status" != "408" ]; then
  echo "expired deadline should return 408, got $expired_status" >&2
  exit 1
fi

idempotency_key="book-ch05-c102-$(date +%s)"
curl --fail --silent --show-error \
  --dump-header "$headers_file" \
  --output "$body_file" \
  --request POST \
  "$api_url/v1/cases/C-102/collaboration-runs" \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header "Idempotency-Key: $idempotency_key" \
  --header "traceparent: 00-$trace_id-$parent_span_id-01" \
  --header 'X-Request-Timeout-Ms: 20000' \
  --data '{"goal":"在统一截止时间和 Trace 上核对规则、材料与风险证据，并形成可追溯结论。"}'

python3 - "$headers_file" "$body_file" "$trace_id" <<'PY'
import json
import pathlib
import sys

headers = pathlib.Path(sys.argv[1]).read_text().lower()
payload = json.loads(pathlib.Path(sys.argv[2]).read_text())
trace_id = sys.argv[3]
assert f"x-trace-id: {trace_id}" in headers, headers
assert "x-request-deadline:" in headers, headers
assert payload["status"] == "completed", payload
assert payload["result"]["outcome"] == "COMPLETE_WITH_REVIEW_REQUIRED", payload
assert payload["result"]["side_effect"] == "none", payload
print(f"deadline-bound collaboration accepted: {payload['run_id']}")
PY

trace_file="$work_dir/trace.json"
attempt=0
while :; do
  if curl --fail --silent --show-error "$tempo_url/api/traces/$trace_id" \
    >"$trace_file" 2>/dev/null && python3 - "$trace_file" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
services = {
    attribute.get("value", {}).get("stringValue")
    for batch in payload.get("batches", [])
    for attribute in batch.get("resource", {}).get("attributes", [])
    if attribute.get("key") == "service.name"
}
raise SystemExit(
    0
    if {"caseops-api", "caseops-a2a", "caseops-mcp"} <= services
    else 1
)
PY
  then
    break
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    echo "complete cross-service trace was not queryable from Tempo" >&2
    exit 1
  fi
  sleep 1
done

python3 - "$trace_file" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
services = set()
for batch in payload.get("batches", []):
    for attribute in batch.get("resource", {}).get("attributes", []):
        if attribute.get("key") == "service.name":
            services.add(attribute.get("value", {}).get("stringValue"))
expected = {"caseops-api", "caseops-a2a", "caseops-mcp"}
assert expected <= services, (expected, services)
print("cross-service W3C trace accepted: " + ", ".join(sorted(services)))
PY

curl --fail --silent --show-error "$prometheus_url/-/ready" >/dev/null
curl --fail --silent --show-error "$prometheus_url/api/v1/rules" \
  >"$work_dir/rules.json"
python3 - "$work_dir/rules.json" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
names = {
    rule["name"]
    for group in payload["data"]["groups"]
    for rule in group["rules"]
}
required = {
    "caseops:http_error_ratio:rate5m",
    "CaseOpsAvailabilityFastBurn",
    "CaseOpsCriticalDependencyUnavailable",
}
assert required <= names, (required, names)
print("SLO recording and actionable alert rules accepted")
PY

docker compose stop a2a >/dev/null
attempt=0
while :; do
  curl --silent --show-error "$api_url/health/ready" >"$work_dir/degraded.json"
  if python3 - "$work_dir/degraded.json" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
checks = {item["name"]: item["status"] for item in payload["checks"]}
raise SystemExit(0 if payload["status"] == "degraded" and checks["a2a"] == "unavailable" else 1)
PY
  then
    break
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 10 ]; then
    echo "readiness did not report the optional A2A degradation" >&2
    exit 1
  fi
  sleep 1
done
echo "optional dependency degradation accepted without failing liveness"
docker compose start a2a >/dev/null

mkdir -p "$backup_dir"
archive=$(scripts/backup-postgres.sh "$backup_dir" | sed -n '1p')
scripts/restore-drill-chapter-05.sh "$archive" >"$work_dir/restore.json"
python3 - "$work_dir/restore.json" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["status"] == "passed", payload
assert payload["alembic_revision"] == "0005", payload
assert payload["business_invariants"]["tenant-demo/C-102"] is True, payload
print(
    "isolated PostgreSQL restore accepted: "
    f"rto={payload['measured_rto_seconds']}s "
    f"signature={payload['content_signature']}"
)
PY
