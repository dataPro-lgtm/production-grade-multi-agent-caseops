#!/usr/bin/env bash
set -euo pipefail

api_url="${CASEOPS_ACCEPTANCE_API_URL:-http://127.0.0.1:8080}"
api_key="${CASEOPS_ACCEPTANCE_API_KEY:-caseops-local-dev-key}"
isolated_api_key="${CASEOPS_ACCEPTANCE_ISOLATED_API_KEY:-caseops-other-tenant-key}"
report_path="${CASEOPS_EVALUATION_REPORT:-artifacts/evaluation/chapter-08-report.json}"

mkdir -p "$(dirname "$report_path")"

curl --fail --silent --show-error "$api_url/health/startup" >/dev/null

python3 -m caseops.evaluation.runner \
  --base-url "$api_url" \
  --api-key "$api_key" \
  --isolated-api-key "$isolated_api_key" \
  --candidate-release "v0.8.0" \
  --output "$report_path"

python3 - "$report_path" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["passed"] is True, report
assert report["summary"]["case_count"] == 5, report["summary"]
assert report["summary"]["trial_count"] == 13, report["summary"]
assert report["summary"]["n_run_semantic_drift_cases"] == 0, report["summary"]
assert all(case["consistency"] == 1 for case in report["cases"]), report["cases"]
assert all(case["passed"] for case in report["cases"]), report["cases"]
print(
    "chapter 08 evaluation gate accepted: "
    "5 cases, 13 trials, zero semantic drift, zero paired regression"
)
PY
