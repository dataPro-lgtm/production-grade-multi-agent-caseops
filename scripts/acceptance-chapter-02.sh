#!/bin/sh
set -eu

api_url="${CASEOPS_ACCEPTANCE_API_URL:-http://localhost:8080}"
api_key="${CASEOPS_ACCEPTANCE_API_KEY:-caseops-local-dev-key}"
run_key="${CASEOPS_ACCEPTANCE_RUN_KEY:-book-ch02-acceptance-$(date +%s)}"
first_response="$(mktemp)"
replay_response="$(mktemp)"
trap 'rm -f "$first_response" "$replay_response"' EXIT

curl --fail-with-body --silent --show-error \
  --request POST \
  "${api_url}/v1/cases/C-102/agent-runs" \
  --header "Content-Type: application/json" \
  --header "X-API-Key: ${api_key}" \
  --header "Idempotency-Key: ${run_key}" \
  --data '{"goal":"判断案件材料是否满足其绑定规则，并给出可追溯结论。"}' \
  >"$first_response"

python - "$first_response" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "completed", payload
assert payload["step_count"] == 4, payload
assert payload["result"]["outcome"] == "DOCUMENTS_COMPLETE_AFTER_NORMALIZATION", payload
assert payload["result"]["resolved_document_codes"] == ["ACCIDENT_CERTIFICATE"], payload
assert payload["result"]["missing_document_codes"] == [], payload
print(f"first run accepted: {payload['run_id']}")
PY

curl --fail-with-body --silent --show-error \
  --request POST \
  "${api_url}/v1/cases/C-102/agent-runs" \
  --header "Content-Type: application/json" \
  --header "X-API-Key: ${api_key}" \
  --header "Idempotency-Key: ${run_key}" \
  --data '{"goal":"判断案件材料是否满足其绑定规则，并给出可追溯结论。"}' \
  >"$replay_response"

python - "$first_response" "$replay_response" <<'PY'
import json
import sys

first = json.load(open(sys.argv[1], encoding="utf-8"))
replay = json.load(open(sys.argv[2], encoding="utf-8"))
assert replay["replayed"] is True, replay
assert replay["run_id"] == first["run_id"], (first, replay)
print("idempotency replay accepted: no second Agent run was allocated")
PY
