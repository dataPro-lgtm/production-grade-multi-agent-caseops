#!/usr/bin/env bash
set -euo pipefail

api_url="${CASEOPS_ACCEPTANCE_API_URL:-http://127.0.0.1:8080}"
api_key="${CASEOPS_ACCEPTANCE_API_KEY:-caseops-local-dev-key}"
run_key="book-ch04-acceptance-$(date +%s)-$$"
response_file="$(mktemp)"
replay_file="$(mktemp)"
trap 'rm -f "$response_file" "$replay_file"' EXIT

curl --fail --silent --show-error "$api_url/health/ready" >/dev/null

request_body='{
  "question": "C-102 的事故证明是否满足规则要求，适用哪个规则版本，为什么需要人工复核？",
  "purpose": "claim_investigation",
  "as_of": "2026-07-23T12:00:00+08:00",
  "evidence_token_budget": 1800,
  "max_rounds": 2
}'

curl --fail-with-body --silent --show-error \
  --request POST \
  "$api_url/v1/cases/C-102/context-investigations" \
  --header 'Content-Type: application/json' \
  --header "X-API-Key: $api_key" \
  --header "Idempotency-Key: $run_key" \
  --data "$request_body" \
  --output "$response_file"

run_id="$(
  python3 - "$response_file" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "complete", payload
result = payload["result"]
pack = result["context_pack"]
answer = result["answer"]
trace = result["trace"]

assert answer["verdict"] == "complete", answer
assert answer["side_effect"] == "none", answer
assert pack["stop_reason"] == "evidence_sufficient", pack
assert pack["evidence_token_count"] <= pack["evidence_token_budget"], pack
assert {claim["claim_id"] for claim in answer["claims"]} == {
    "claim-policy-version",
    "claim-document-status",
    "claim-manual-review",
}

evidence_ids = {item["evidence_id"] for item in pack["evidence"]}
for claim in answer["claims"]:
    assert set(claim["evidence_ids"]) <= evidence_ids, claim

selected_objects = {item["object_id"] for item in pack["evidence"]}
assert "ctx-policy-motor-2025.4" not in selected_objects
assert "ctx-untrusted-email-v1" not in selected_objects
decisions = {event["decision"] for event in trace}
assert "rejected_temporal" in decisions, decisions
assert "rejected_untrusted_instruction" in decisions, decisions
channels = set(pack["retrieval_plan"]["channels"])
assert channels == {"structured", "full_text", "graph"}, channels
assert "vector" not in channels
print(payload["run_id"])
PY
)"
echo "governed Context Pack accepted: $run_id"

curl --fail-with-body --silent --show-error \
  --request POST \
  "$api_url/v1/cases/C-102/context-investigations" \
  --header 'Content-Type: application/json' \
  --header "X-API-Key: $api_key" \
  --header "Idempotency-Key: $run_key" \
  --data "$request_body" \
  --output "$replay_file"

python3 - "$response_file" "$replay_file" <<'PY'
import json
import sys

first = json.load(open(sys.argv[1], encoding="utf-8"))
replay = json.load(open(sys.argv[2], encoding="utf-8"))
assert replay["replayed"] is True, replay
assert replay["run_id"] == first["run_id"], replay
assert replay["result"]["context_pack"]["pack_id"] == (
    first["result"]["context_pack"]["pack_id"]
), replay
print("idempotency replay accepted: Context Pack was not rebuilt")
PY

context_count="$(
  docker compose exec -T postgres \
    psql -U caseops -d caseops -Atc \
    "select count(*) from context_runs where id='$run_id';"
)"
event_count="$(
  docker compose exec -T postgres \
    psql -U caseops -d caseops -Atc \
    "select count(*) from outbox_events where aggregate_id='$run_id';"
)"
search_index_count="$(
  docker compose exec -T postgres \
    psql -U caseops -d caseops -Atc \
    "select count(*) from pg_indexes where indexname='ix_knowledge_objects_search';"
)"
graph_count="$(
  docker compose exec -T postgres \
    psql -U caseops -d caseops -Atc \
    "select count(*) from knowledge_relations where tenant_id='tenant-demo';"
)"
test "$context_count" = "1"
test "$event_count" = "1"
test "$search_index_count" = "1"
test "$graph_count" = "8"
echo "durable context evidence accepted: runs=1 events=1 fts_index=1 relations=8"
