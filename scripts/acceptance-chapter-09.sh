#!/usr/bin/env bash
set -euo pipefail

api_url="${CASEOPS_ACCEPTANCE_API_URL:-http://127.0.0.1:8080}"
a2a_url="${CASEOPS_ACCEPTANCE_A2A_URL:-http://127.0.0.1:8082}"
api_key="${CASEOPS_ACCEPTANCE_API_KEY:-caseops-local-dev-key}"
report_path="${CASEOPS_OPERATIONS_REPORT:-artifacts/operations/chapter-09-gameday.json}"
work_dir="$(mktemp -d)"

restore_platform() {
  docker compose start a2a >/dev/null 2>&1 || true
  rm -rf "$work_dir"
}
trap restore_platform EXIT

mkdir -p "$(dirname "$report_path")"

curl --fail --silent --show-error \
  "$api_url/health/startup" >"$work_dir/startup.json"
python3 - "$work_dir/startup.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "ok", payload
assert "revision 0007" in payload["checks"][0]["detail"], payload
print("operations schema accepted: revision 0007")
PY

create_system_run() {
  local idempotency_key="$1"
  local output="$2"
  curl --fail-with-body --silent --show-error \
    --request POST \
    "$api_url/v1/cases/C-102/system-runs" \
    --header 'Content-Type: application/json' \
    --header "X-API-Key: $api_key" \
    --header "Idempotency-Key: $idempotency_key" \
    --data '{
      "goal":"合并上下文调查与多专业协作结论，对 C-102 执行系统级一致性验收。",
      "question":"本案适用什么规则，材料是否满足要求，是否触发人工风险复核？",
      "as_of":"2026-07-23T12:00:00+08:00",
      "evidence_token_budget":1800,
      "max_rounds":2
    }' \
    --output "$output"
}

assess_system_run() {
  local system_run_id="$1"
  local idempotency_key="$2"
  local output="$3"
  curl --fail-with-body --silent --show-error \
    --request POST \
    "$api_url/v1/system-runs/$system_run_id/operational-assessments" \
    --header "X-API-Key: $api_key" \
    --header "Idempotency-Key: $idempotency_key" \
    --output "$output"
}

nonce="$(date +%s)-$$"
baseline_key="book-ch09-baseline-$nonce"
create_system_run "$baseline_key" "$work_dir/baseline-run.json"
baseline_run_id="$(
  python3 - "$work_dir/baseline-run.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] in {"completed", "needs_human"}, payload
assert payload["result"]["side_effect"] == "none", payload
print(payload["system_run_id"])
PY
)"
assess_system_run \
  "$baseline_run_id" \
  "book-ch09-baseline-assessment-$nonce" \
  "$work_dir/baseline-assessment.json"
python3 - "$work_dir/baseline-assessment.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
report = payload["report"]
assert report["status"] == "healthy", report
assert report["severity"] == "none", report
assert report["impact"]["goal_succeeded"] is True, report
assert report["impact"]["external_side_effect_count"] == 0, report
assert report["first_failure"] is None, report
assert report["evidence"]["context_graph_node_count"] >= 15, report
assert report["evidence"]["security_decision_count"] >= 6, report
assert report["cost"]["pricing_status"] == "not_configured", report
assert report["cost"]["monetary_cost_microunits"] is None, report
print("healthy baseline accepted with durable evidence and honest unit attribution")
PY

docker compose stop a2a >/dev/null
fault_key="book-ch09-a2a-fault-$nonce"
fault_http_code="$(
  curl --silent --show-error \
    --request POST \
    "$api_url/v1/cases/C-102/system-runs" \
    --header 'Content-Type: application/json' \
    --header "X-API-Key: $api_key" \
    --header "Idempotency-Key: $fault_key" \
    --data '{
      "goal":"在 A2A 故障窗口内执行系统级一致性验收并保留诊断证据。",
      "question":"A2A 不可用时系统能否定位首个失败点并保持无外部副作用？",
      "as_of":"2026-07-23T12:00:00+08:00",
      "evidence_token_budget":1800,
      "max_rounds":2
    }' \
    --output "$work_dir/fault-response.txt" \
    --write-out '%{http_code}'
)"
test "$fault_http_code" = "201"

fault_run_id="$(
  docker compose exec -T postgres psql -U caseops -d caseops -Atc \
    "select id from system_runs
     where tenant_id='tenant-demo' and idempotency_key='$fault_key';"
)"
test -n "$fault_run_id"

docker compose start a2a >/dev/null
for _ in $(seq 1 30); do
  if curl --fail --silent "$a2a_url/health/live" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error "$a2a_url/health/live" >/dev/null

assess_system_run \
  "$fault_run_id" \
  "book-ch09-incident-assessment-$nonce" \
  "$work_dir/incident-assessment.json"
python3 - "$work_dir/incident-assessment.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
report = payload["report"]
assert report["status"] == "incident", report
assert report["severity"] == "SEV-2", report
assert report["impact"]["goal_succeeded"] is False, report
assert report["impact"]["external_side_effect_count"] == 0, report
assert report["impact"]["completed_step_count"] == 3, report
assert report["impact"]["failed_step_count"] == 0, report
assert report["impact"]["failed_delegated_task_count"] == 3, report
failure = report["first_failure"]
assert failure["layer"] == "collaboration", failure
assert failure["step_key"] == "specialist-collaboration", failure
assert failure["error_code"], failure
measures = {item["resource_type"]: item for item in report["cost"]["measures"]}
assert measures["context_run"]["quantity"] == 1, measures
assert measures["context_run"]["attribution"] == "wasted", measures
assert measures["context_run"]["per_successful_goal"] is None, measures
assert measures["delegated_task_attempt"]["quantity"] == 3, measures
assert [item["action"] for item in report["recommended_controls"]] == [
    "wait_for_dependency_readiness",
    "route_to_human",
    "retry_from_failed_step",
], report
print(
    "incident bundle accepted: first failure=collaboration, "
    "safe side effects=0, wasted context unit=1"
)
PY

recovery_key="book-ch09-recovery-$nonce"
create_system_run "$recovery_key" "$work_dir/recovery-run.json"
recovery_run_id="$(
  python3 - "$work_dir/recovery-run.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] in {"completed", "needs_human"}, payload
assert payload["result"]["side_effect"] == "none", payload
print(payload["system_run_id"])
PY
)"
assess_system_run \
  "$recovery_run_id" \
  "book-ch09-recovery-assessment-$nonce" \
  "$work_dir/recovery-assessment.json"
python3 - "$work_dir/recovery-assessment.json" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))["report"]
assert report["status"] == "healthy", report
assert report["impact"]["goal_succeeded"] is True, report
assert report["impact"]["external_side_effect_count"] == 0, report
print("post-recovery run accepted: A2A readiness and goal convergence restored")
PY

python3 - \
  "$work_dir/baseline-assessment.json" \
  "$work_dir/incident-assessment.json" \
  "$work_dir/recovery-assessment.json" \
  "$fault_http_code" \
  "$report_path" <<'PY'
import json
import sys

baseline_path, incident_path, recovery_path, fault_code, report_path = sys.argv[1:]
report = {
    "schema_version": "caseops.gameday-report.v1",
    "release": "v0.9.0",
    "scenario": "A2A dependency interruption and verified recovery",
    "fault_injection_http_status": int(fault_code),
    "baseline": json.load(open(baseline_path, encoding="utf-8")),
    "incident": json.load(open(incident_path, encoding="utf-8")),
    "recovery": json.load(open(recovery_path, encoding="utf-8")),
}
with open(report_path, "w", encoding="utf-8") as target:
    json.dump(report, target, ensure_ascii=False, indent=2)
    target.write("\n")
print(f"GameDay evidence written to {report_path}")
PY

counts="$(
  docker compose exec -T postgres psql -U caseops -d caseops -Atc \
    "select
       (select count(*) from operational_assessments
        where system_run_id in ('$baseline_run_id','$fault_run_id','$recovery_run_id')),
       (select count(*) from operational_cost_events
        where system_run_id in ('$baseline_run_id','$fault_run_id','$recovery_run_id')),
       (select count(*) from audit_events
        where action='system.operations.assess'
          and subject_id in ('$baseline_run_id','$fault_run_id','$recovery_run_id'));"
)"
IFS='|' read -r assessment_count cost_event_count audit_count <<<"$counts"
test "$assessment_count" = "3"
test "$cost_event_count" = "12"
test "$audit_count" = "3"
echo "durable operations evidence accepted: assessments=3 cost_events=12 audits=3"

echo "chapter 09 AgentOps GameDay acceptance passed"
