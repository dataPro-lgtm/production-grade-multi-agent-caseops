from __future__ import annotations

import unittest
from collections.abc import Mapping

from caseops.evaluation.contracts import GoldenCase
from caseops.evaluation.dataset import (
    load_baseline,
    load_dataset,
    load_quality_contract,
)
from caseops.evaluation.evaluators import EvaluationEngine
from caseops.evaluation.runner import run_evaluation


def _success_payload(http_status: int = 201) -> dict[str, object]:
    _ = http_status
    claims = [
        {
            "claim_id": f"claim-{index}",
            "value": f"value-{index}",
            "evidence_refs": [f"evidence://{index}"],
        }
        for index in range(6)
    ]
    return {
        "system_run_id": "sys-eval-stable",
        "status": "needs_human",
        "replayed": False,
        "result": {
            "outcome": "SYSTEM_ACCEPTED_WITH_HUMAN_REVIEW",
            "recommended_action": "route_to_human_reviewer",
            "side_effect": "none",
            "checks": [
                {"check_id": f"check-{index}", "status": "passed"} for index in range(7)
            ],
            "claims": claims,
            "evidence_refs": [f"evidence://{index}" for index in range(7)],
        },
        "steps": [
            {
                "step_key": "context-evidence",
                "status": "succeeded",
                "depends_on": [],
                "attempt_count": 1,
            },
            {
                "step_key": "specialist-collaboration",
                "status": "succeeded",
                "depends_on": [],
                "attempt_count": 1,
            },
            {
                "step_key": "system-acceptance",
                "status": "succeeded",
                "depends_on": [
                    "context-evidence",
                    "specialist-collaboration",
                ],
                "attempt_count": 1,
            },
        ],
        "context_graph_uri": "/v1/system-runs/sys-eval-stable/context-graph",
    }


def _graph(*, omit_last_support: bool = False) -> dict[str, object]:
    nodes = [{"node_key": f"claim:{index}", "node_type": "claim"} for index in range(6)]
    nodes.extend(
        {"node_key": f"node:{index}", "node_type": "evidence"} for index in range(14)
    )
    supported_count = 5 if omit_last_support else 6
    edges = [
        {
            "from_node_key": f"claim:{index}",
            "to_node_key": f"node:{index}",
            "relation_type": "SUPPORTED_BY",
        }
        for index in range(supported_count)
    ]
    edges.extend(
        {
            "from_node_key": "node:0",
            "to_node_key": "node:1",
            "relation_type": "DERIVED_FROM",
        }
        for _ in range(40 - len(edges))
    )
    return {"nodes": nodes, "edges": edges}


class EvaluationEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = load_dataset()
        self.contract = load_quality_contract()
        self.engine = EvaluationEngine(self.contract)

    def test_versioned_assets_are_internally_consistent(self) -> None:
        baseline = load_baseline()

        self.assertEqual(baseline.dataset_version, self.dataset.version)
        self.assertEqual(
            {item.case_id for item in baseline.cases},
            {item.case_id for item in self.dataset.cases},
        )
        self.assertEqual(sum(case.repetitions for case in self.dataset.cases), 13)

    def test_all_deterministic_layers_pass_for_grounded_run(self) -> None:
        case = self.dataset.cases[0]

        layers, fingerprint, diagnostics = self.engine.evaluate(
            case=case,
            http_status=201,
            payload=_success_payload(),
            graph=_graph(),
            isolated_graph_status=404,
        )

        self.assertTrue(all(layer.passed for layer in layers))
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(diagnostics["claim_count"], 6)
        self.assertEqual(diagnostics["cross_tenant_graph_status"], 404)

    def test_missing_supported_by_edge_blocks_evidence_gate(self) -> None:
        case = self.dataset.cases[0]

        layers, _, _ = self.engine.evaluate(
            case=case,
            http_status=201,
            payload=_success_payload(),
            graph=_graph(omit_last_support=True),
            isolated_graph_status=404,
        )

        evidence = next(layer for layer in layers if layer.layer == "evidence")
        self.assertFalse(evidence.passed)
        self.assertIn("claim support", evidence.findings[0])

    def test_expected_integrity_rejection_is_a_successful_eval_outcome(self) -> None:
        case = self.dataset.cases[-1]

        layers, _, diagnostics = self.engine.evaluate(
            case=case,
            http_status=409,
            payload={"status": 409, "code": "IDEMPOTENCY_KEY_REUSED"},
            graph=None,
            isolated_graph_status=None,
        )

        self.assertTrue(all(layer.passed for layer in layers))
        self.assertTrue(diagnostics["expected_rejection"])


class _FakeLiveTarget:
    async def execute(
        self,
        *,
        case: GoldenCase,
        idempotency_key: str,
    ) -> tuple[int, Mapping[str, object], Mapping[str, object] | None, int | None]:
        self.last_idempotency_key = idempotency_key
        if case.mode == "idempotency_conflict":
            return (
                409,
                {"status": 409, "code": "IDEMPOTENCY_KEY_REUSED"},
                None,
                None,
            )
        status = 200 if case.mode == "idempotent_replay" else 201
        return status, _success_payload(status), _graph(), 404


class EvaluationRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_golden_dataset_produces_paired_release_decision(self) -> None:
        target = _FakeLiveTarget()

        report = await run_evaluation(
            target=target,  # type: ignore[arg-type]
            candidate_release="v0.8.0-test",
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.summary["case_count"], 5)
        self.assertEqual(report.summary["trial_count"], 13)
        self.assertEqual(report.summary["release_decision"], "pass")
        self.assertTrue(all(case.consistency == 1 for case in report.cases))
        self.assertTrue(target.last_idempotency_key.startswith(report.eval_run_id))
