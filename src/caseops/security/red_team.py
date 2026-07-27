from __future__ import annotations

import argparse
import json
from importlib.resources import files
from typing import Any

from caseops.agent.tools import TOOL_REGISTRY

from .contracts import DataClassification, SecurityContext
from .manifests import TOOL_SECURITY_MANIFESTS
from .privacy import OutputGuard
from .tool_guard import ToolGuard

ALL_SCOPES = frozenset(
    {"case:read", "policy:read", "document:read", "document:resolve", "risk:read"}
)


def run_dataset() -> dict[str, Any]:
    dataset = json.loads(
        files("caseops.security")
        .joinpath("red_team_cases.json")
        .read_text(encoding="utf-8")
    )
    results: list[dict[str, Any]] = []
    unauthorized_side_effects = 0
    secret_leakage_count = 0
    guard = ToolGuard()
    output_guard = OutputGuard()
    for case in dataset["cases"]:
        effect: str
        reasons: tuple[str, ...]
        if case["kind"] == "tool":
            user_scopes = set(ALL_SCOPES)
            workload_scopes = set(ALL_SCOPES)
            delegation_scopes = set(ALL_SCOPES)
            user_scopes.discard(case.get("remove_user_scope"))
            workload_scopes.discard(case.get("remove_workload_scope"))
            delegation_scopes.discard(case.get("remove_delegation_scope"))
            context = SecurityContext(
                tenant_id="tenant-demo",
                actor_id="red-team-runner",
                user_scopes=frozenset(user_scopes),
                workload_id="caseops-red-team",
                workload_scopes=frozenset(workload_scopes),
                delegation_id=case["case_id"],
                delegation_scopes=frozenset(delegation_scopes),
                purpose=case.get("purpose", "case_investigation"),
                resource_type="case",
                resource_id="C-102",
                environment="test",
            )
            tool_id = case["tool_id"]
            decision = guard.evaluate(
                definition=TOOL_REGISTRY.get(tool_id),
                manifest=TOOL_SECURITY_MANIFESTS.get(tool_id),
                arguments=case["arguments"],
                context=context,
                runtime_allowlist=frozenset(TOOL_REGISTRY),
                globally_enabled=case.get("globally_enabled", True),
            )
            effect = decision.effect
            reasons = decision.reason_codes
        else:
            release = output_guard.release(
                case["text"],
                classification=DataClassification(case["classification"]),
            )
            effect = release.effect
            reasons = release.reason_codes
        passed = effect == case["expected_effect"] and case["expected_reason"] in reasons
        if (
            case["kind"] == "tool"
            and case["expected_effect"] == "deny"
            and effect == "allow"
        ):
            unauthorized_side_effects += 1
        if (
            case["kind"] == "output"
            and case["expected_effect"] == "block"
            and effect == "released"
        ):
            secret_leakage_count += 1
        results.append(
            {
                "case_id": case["case_id"],
                "passed": passed,
                "effect": effect,
                "reason_codes": list(reasons),
            }
        )
    passed_count = sum(1 for result in results if result["passed"])
    return {
        "schema_version": "caseops.security-acceptance-report.v1",
        "dataset_version": dataset["schema_version"],
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "unauthorized_side_effects": unauthorized_side_effects,
        "secret_leakage_count": secret_leakage_count,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run_dataset()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"red-team cases: {report['passed']}/{report['total']} passed")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
