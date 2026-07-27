from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from typing import cast

from .contracts import (
    ExpectedResult,
    GoldenCase,
    LayerName,
    LayerResult,
    QualityContract,
)


def _objects(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _fingerprint(payload: Mapping[str, object], graph: Mapping[str, object] | None) -> str:
    if "result" not in payload:
        semantic: object = {
            "status": payload.get("status"),
            "code": payload.get("code"),
        }
    else:
        result = cast(Mapping[str, object], payload["result"])
        checks = cast(list[Mapping[str, object]], result.get("checks", []))
        claims = cast(list[Mapping[str, object]], result.get("claims", []))
        semantic = {
            "status": payload.get("status"),
            "outcome": result.get("outcome"),
            "recommended_action": result.get("recommended_action"),
            "side_effect": result.get("side_effect"),
            "checks": sorted(
                (str(item.get("check_id")), str(item.get("status"))) for item in checks
            ),
            "claims": sorted(
                (
                    str(item.get("claim_id")),
                    str(item.get("value")),
                    tuple(sorted(map(str, _objects(item.get("evidence_refs"))))),
                )
                for item in claims
            ),
            "graph": _graph_shape(graph),
        }
    canonical = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _graph_shape(graph: Mapping[str, object] | None) -> object:
    if graph is None:
        return None
    nodes = cast(list[Mapping[str, object]], graph.get("nodes", []))
    edges = cast(list[Mapping[str, object]], graph.get("edges", []))
    return {
        "node_types": sorted(Counter(str(node.get("node_type")) for node in nodes).items()),
        "relations": sorted(
            Counter(str(edge.get("relation_type")) for edge in edges).items()
        ),
    }


class EvaluationEngine:
    """Deterministic evaluators first; model judges are advisory, never a hard gate."""

    def __init__(self, contract: QualityContract) -> None:
        self._contract = contract

    def evaluate(
        self,
        *,
        case: GoldenCase,
        http_status: int,
        payload: Mapping[str, object],
        graph: Mapping[str, object] | None,
        isolated_graph_status: int | None,
    ) -> tuple[tuple[LayerResult, ...], str, dict[str, int | float | str | bool]]:
        expected = case.expected
        if http_status >= 400:
            layers, diagnostics = self._evaluate_error(
                expected=expected,
                http_status=http_status,
                payload=payload,
            )
        else:
            layers, diagnostics = self._evaluate_success(
                expected=expected,
                http_status=http_status,
                payload=payload,
                graph=graph,
                isolated_graph_status=isolated_graph_status,
            )
        return layers, _fingerprint(payload, graph), diagnostics

    def _result(
        self,
        layer: LayerName,
        findings: list[str],
    ) -> LayerResult:
        score = 1.0 if not findings else 0.0
        return LayerResult(
            layer=layer,
            score=score,
            passed=score >= self._contract.thresholds.required_layer_score,
            findings=tuple(findings),
        )

    def _evaluate_error(
        self,
        *,
        expected: ExpectedResult,
        http_status: int,
        payload: Mapping[str, object],
    ) -> tuple[tuple[LayerResult, ...], dict[str, int | float | str | bool]]:
        contract_findings: list[str] = []
        if http_status != expected.http_status:
            contract_findings.append(
                f"HTTP status expected={expected.http_status} actual={http_status}"
            )
        if payload.get("code") != expected.error_code:
            contract_findings.append(
                f"error code expected={expected.error_code} actual={payload.get('code')}"
            )
        layers = (
            self._result("contract", contract_findings),
            self._result("outcome", []),
            self._result("path", []),
            self._result("evidence", []),
            self._result("security", []),
            self._result("efficiency", []),
        )
        return layers, {
            "expected_rejection": True,
            "error_code": str(payload.get("code", "")),
        }

    def _evaluate_success(
        self,
        *,
        expected: ExpectedResult,
        http_status: int,
        payload: Mapping[str, object],
        graph: Mapping[str, object] | None,
        isolated_graph_status: int | None,
    ) -> tuple[tuple[LayerResult, ...], dict[str, int | float | str | bool]]:
        result = cast(Mapping[str, object], payload.get("result", {}))
        steps = cast(list[Mapping[str, object]], payload.get("steps", []))
        checks = cast(list[Mapping[str, object]], result.get("checks", []))
        claims = cast(list[Mapping[str, object]], result.get("claims", []))
        evidence_refs = cast(list[object], result.get("evidence_refs", []))
        nodes = (
            cast(list[Mapping[str, object]], graph.get("nodes", []))
            if graph is not None
            else []
        )
        edges = (
            cast(list[Mapping[str, object]], graph.get("edges", []))
            if graph is not None
            else []
        )

        contract_findings: list[str] = []
        if http_status != expected.http_status:
            contract_findings.append(
                f"HTTP status expected={expected.http_status} actual={http_status}"
            )
        for field in ("system_run_id", "status", "result", "steps", "context_graph_uri"):
            if field not in payload:
                contract_findings.append(f"missing response field: {field}")

        outcome_findings: list[str] = []
        expectations = {
            "status": (payload.get("status"), expected.run_status),
            "outcome": (result.get("outcome"), expected.outcome),
            "recommended_action": (
                result.get("recommended_action"),
                expected.recommended_action,
            ),
            "side_effect": (result.get("side_effect"), expected.side_effect),
        }
        for name, (actual, wanted) in expectations.items():
            if actual != wanted:
                outcome_findings.append(f"{name} expected={wanted} actual={actual}")

        path_findings: list[str] = []
        expected_steps = {
            "context-evidence": (),
            "specialist-collaboration": (),
            "system-acceptance": (
                "context-evidence",
                "specialist-collaboration",
            ),
        }
        if len(steps) != len(expected_steps):
            path_findings.append(
                f"step count expected={len(expected_steps)} actual={len(steps)}"
            )
        by_key = {str(step.get("step_key")): step for step in steps}
        for key, dependencies in expected_steps.items():
            step = by_key.get(key)
            if step is None:
                path_findings.append(f"missing step: {key}")
                continue
            if step.get("status") != "succeeded":
                path_findings.append(f"step {key} did not succeed")
            if tuple(map(str, _objects(step.get("depends_on")))) != dependencies:
                path_findings.append(f"step {key} dependency contract drifted")
        if any(check.get("status") != "passed" for check in checks):
            path_findings.append("one or more system acceptance checks failed")

        evidence_findings: list[str] = []
        thresholds = self._contract.thresholds
        if len(claims) < thresholds.min_claims:
            evidence_findings.append(
                f"claims below floor={thresholds.min_claims} actual={len(claims)}"
            )
        if len(evidence_refs) < thresholds.min_evidence_refs:
            evidence_findings.append("system evidence references below release floor")
        if any(not claim.get("evidence_refs") for claim in claims):
            evidence_findings.append("one or more accepted claims lack evidence")
        claim_nodes = {
            str(node.get("node_key")) for node in nodes if node.get("node_type") == "claim"
        }
        supported_claims = {
            str(edge.get("from_node_key"))
            for edge in edges
            if edge.get("relation_type") == "SUPPORTED_BY"
        }
        if not claim_nodes or claim_nodes != supported_claims:
            evidence_findings.append("Runtime Context Graph claim support is incomplete")
        if len(nodes) < thresholds.min_graph_nodes:
            evidence_findings.append("Runtime Context Graph node coverage regressed")
        if len(edges) < thresholds.min_graph_edges:
            evidence_findings.append("Runtime Context Graph edge coverage regressed")

        security_findings: list[str] = []
        if result.get("side_effect") != "none":
            security_findings.append("system produced an unauthorized side effect")
        if isolated_graph_status != 404:
            security_findings.append(
                f"cross-tenant graph probe expected=404 actual={isolated_graph_status}"
            )

        efficiency_findings: list[str] = []
        if len(steps) > thresholds.max_steps:
            efficiency_findings.append("step budget exceeded")
        attempts = [_integer(step.get("attempt_count")) for step in steps]
        if any(value > thresholds.max_attempts_per_step for value in attempts):
            efficiency_findings.append("step attempt budget exceeded")

        layers = (
            self._result("contract", contract_findings),
            self._result("outcome", outcome_findings),
            self._result("path", path_findings),
            self._result("evidence", evidence_findings),
            self._result("security", security_findings),
            self._result("efficiency", efficiency_findings),
        )
        return layers, {
            "expected_rejection": False,
            "step_count": len(steps),
            "claim_count": len(claims),
            "evidence_ref_count": len(evidence_refs),
            "graph_node_count": len(nodes),
            "graph_edge_count": len(edges),
            "max_step_attempts": max(attempts, default=0),
            "cross_tenant_graph_status": isolated_graph_status or 0,
        }
