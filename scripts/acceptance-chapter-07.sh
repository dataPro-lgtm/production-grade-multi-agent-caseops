#!/usr/bin/env bash
set -euo pipefail

api_url="${CASEOPS_ACCEPTANCE_API_URL:-http://127.0.0.1:8080}"
a2a_url="${CASEOPS_ACCEPTANCE_A2A_URL:-http://127.0.0.1:8082}"
api_key="${CASEOPS_ACCEPTANCE_API_KEY:-caseops-local-dev-key}"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

curl --fail --silent --show-error \
  "$api_url/health/startup" >"$work_dir/startup.json"
python3 - "$work_dir/startup.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "ok", payload
assert "revision 0006" in payload["checks"][0]["detail"], payload
print("hierarchical orchestration schema accepted: revision 0006")
PY

curl --fail --silent --show-error \
  "$a2a_url/.well-known/agent-card.json" >"$work_dir/agent-card.json"
python3 - "$work_dir/agent-card.json" <<'PY'
import json
import sys

card = json.load(open(sys.argv[1], encoding="utf-8"))
skills = {skill["id"] for skill in card["skills"]}
assert {
    "caseops_coverage",
    "caseops_document",
    "caseops_risk",
} <= skills, card
interfaces = card.get("supportedInterfaces", card.get("supported_interfaces", []))
assert any(
    item.get("protocolVersion", item.get("protocol_version")) == "1.0"
    and item.get("protocolBinding", item.get("protocol_binding")) == "HTTP+JSON"
    for item in interfaces
), interfaces
print("A2A capability snapshot accepted: HTTP+JSON 1.0 and three bounded skills")
PY

run_key="book-ch07-system-$(date +%s)-$$"
curl --fail-with-body --silent --show-error \
  --request POST \
  "$api_url/v1/cases/C-102/system-runs" \
  --header 'Content-Type: application/json' \
  --header "X-API-Key: $api_key" \
  --header "Idempotency-Key: $run_key" \
  --data '{
    "goal":"合并上下文调查与多专业协作结论，对 C-102 执行系统级一致性验收。",
    "question":"本案适用什么规则，材料是否满足要求，是否触发人工风险复核？",
    "as_of":"2026-07-23T12:00:00+08:00",
    "evidence_token_budget":1800,
    "max_rounds":2
  }' \
  --output "$work_dir/system-run.json"

system_run_id="$(
  python3 - "$work_dir/system-run.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "needs_human", payload
assert payload["result"]["outcome"] == "SYSTEM_ACCEPTED_WITH_HUMAN_REVIEW", payload
assert payload["result"]["side_effect"] == "none", payload
assert len(payload["result"]["checks"]) == 7, payload
assert {item["status"] for item in payload["result"]["checks"]} == {"passed"}, payload
assert {item["status"] for item in payload["steps"]} == {"succeeded"}, payload
assert set(payload["child_runs"]) == {
    "context_run_id",
    "collaboration_run_id",
}, payload
assert all(payload["child_runs"].values()), payload
print(payload["system_run_id"])
PY
)"
echo "system convergence accepted: $system_run_id"

curl --fail --silent --show-error \
  "$api_url/v1/system-runs/$system_run_id/context-graph" \
  --header "X-API-Key: $api_key" \
  --output "$work_dir/context-graph.json"
python3 - "$work_dir/context-graph.json" <<'PY'
import json
import sys

graph = json.load(open(sys.argv[1], encoding="utf-8"))
nodes = graph["nodes"]
edges = graph["edges"]
node_types = {node["node_type"] for node in nodes}
required = {
    "goal",
    "plan",
    "step",
    "context_pack",
    "delegated_task",
    "claim",
    "evidence",
    "acceptance",
    "result",
}
assert required <= node_types, node_types
claims = {node["node_key"] for node in nodes if node["node_type"] == "claim"}
supported = {
    edge["from_node_key"]
    for edge in edges
    if edge["relation_type"] == "SUPPORTED_BY"
}
assert claims == supported, (claims, supported)
assert all(len(node["payload_digest"]) == 64 for node in nodes), nodes
print(f"runtime Context Graph accepted: nodes={len(nodes)} edges={len(edges)}")
PY

curl --fail-with-body --silent --show-error \
  --request POST \
  "$api_url/v1/cases/C-102/system-runs" \
  --header 'Content-Type: application/json' \
  --header "X-API-Key: $api_key" \
  --header "Idempotency-Key: $run_key" \
  --data '{
    "goal":"合并上下文调查与多专业协作结论，对 C-102 执行系统级一致性验收。",
    "question":"本案适用什么规则，材料是否满足要求，是否触发人工风险复核？",
    "as_of":"2026-07-23T12:00:00+08:00",
    "evidence_token_budget":1800,
    "max_rounds":2
  }' \
  --output "$work_dir/replay.json"
python3 - "$work_dir/replay.json" "$system_run_id" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["replayed"] is True, payload
assert payload["system_run_id"] == sys.argv[2], payload
print("system-level idempotent replay accepted")
PY

counts="$(
  docker compose exec -T postgres psql -U caseops -d caseops -Atc \
    "select
       (select count(*) from system_steps where system_run_id='$system_run_id'),
       (select count(*) from runtime_context_nodes where system_run_id='$system_run_id'),
       (select count(*) from runtime_context_edges where system_run_id='$system_run_id'),
       (select count(*) from security_decisions where task_id in (
          select id from delegated_tasks where run_id=(
            select collaboration_run_id from system_runs where id='$system_run_id'
          )
       ));"
)"
IFS='|' read -r step_count node_count edge_count security_count <<<"$counts"
test "$step_count" = "3"
test "$node_count" -ge 15
test "$edge_count" -ge 20
test "$security_count" -ge 6
echo "durable hierarchy accepted: steps=$step_count security_decisions=$security_count"

echo "chapter 07 hierarchical system acceptance passed"
