#!/usr/bin/env bash
set -euo pipefail

api_url="${CASEOPS_ACCEPTANCE_API_URL:-http://127.0.0.1:8080}"
a2a_url="${CASEOPS_ACCEPTANCE_A2A_URL:-http://127.0.0.1:8082}"
api_key="${CASEOPS_ACCEPTANCE_API_KEY:-caseops-local-dev-key}"
run_key="book-ch03-acceptance-$(date +%s)-$$"
response_file="$(mktemp)"
replay_file="$(mktemp)"
trap 'rm -f "$response_file" "$replay_file"' EXIT

curl --fail --silent --show-error "$api_url/health/ready" >/dev/null
curl --fail --silent --show-error "$a2a_url/health/live" >/dev/null

curl --fail --silent --show-error \
  "$a2a_url/.well-known/agent-card.json" \
  | python3 -c '
import json, sys
card = json.load(sys.stdin)
assert card["name"] == "CaseOps specialist network"
assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
assert {skill["id"] for skill in card["skills"]} == {
    "caseops_coverage", "caseops_document", "caseops_risk"
}
print("A2A Agent Card accepted: three governed specialist skills")
'

unauthorized_status="$(
  curl --silent --output /dev/null --write-out '%{http_code}' \
    --request POST \
    "$a2a_url/a2a/rest/message:send" \
    --header 'Content-Type: application/json' \
    --data '{}'
)"
test "$unauthorized_status" = "401"
echo "A2A operation without task token rejected: HTTP 401"

curl --fail-with-body --silent --show-error \
  --request POST \
  "$api_url/v1/cases/C-102/collaboration-runs" \
  --header 'Content-Type: application/json' \
  --header "X-API-Key: $api_key" \
  --header "Idempotency-Key: $run_key" \
  --data '{
    "goal": "并行核对案件规则、材料完整性与风险信号，通过证据合同形成可追溯的协作结论。"
  }' \
  --output "$response_file"

run_id="$(
  python3 - "$response_file" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "completed", payload
assert payload["result"]["outcome"] == "COMPLETE_WITH_REVIEW_REQUIRED", payload
assert payload["result"]["join"]["accepted_specialists"] == [
    "coverage", "document", "risk"
], payload
assert payload["result"]["join"]["conflicts"] == [], payload
assert payload["result"]["side_effect"] == "none", payload
assert {task["status"] for task in payload["tasks"]} == {"succeeded"}, payload
assert len(payload["tasks"]) == 3, payload
print(payload["run_id"])
PY
)"
echo "full API -> A2A -> MCP -> PostgreSQL run accepted: $run_id"

curl --fail-with-body --silent --show-error \
  --request POST \
  "$api_url/v1/cases/C-102/collaboration-runs" \
  --header 'Content-Type: application/json' \
  --header "X-API-Key: $api_key" \
  --header "Idempotency-Key: $run_key" \
  --data '{
    "goal": "并行核对案件规则、材料完整性与风险信号，通过证据合同形成可追溯的协作结论。"
  }' \
  --output "$replay_file"

python3 - "$response_file" "$replay_file" <<'PY'
import json, sys
first = json.load(open(sys.argv[1], encoding="utf-8"))
replay = json.load(open(sys.argv[2], encoding="utf-8"))
assert replay["replayed"] is True, replay
assert replay["run_id"] == first["run_id"], replay
assert [task["task_id"] for task in replay["tasks"]] == [
    task["task_id"] for task in first["tasks"]
], replay
print("idempotency replay accepted: no specialist was redispatched")
PY

task_count="$(
  docker compose exec -T postgres \
    psql -U caseops -d caseops -Atc \
    "select count(*) from delegated_tasks where run_id='$run_id';"
)"
event_count="$(
  docker compose exec -T postgres \
    psql -U caseops -d caseops -Atc \
    "select count(*) from outbox_events where aggregate_id='$run_id';"
)"
test "$task_count" = "3"
test "$event_count" = "5"
echo "durable task and CloudEvents evidence accepted: tasks=3 events=5"
