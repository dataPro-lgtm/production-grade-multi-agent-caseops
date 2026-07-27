from __future__ import annotations

import hashlib
import json

from caseops.collaboration.contracts import CollaborationResult
from caseops.context.contracts import ContextInvestigationResult

from .contracts import (
    ContextGraphEdge,
    ContextGraphNode,
    RuntimeContextGraph,
    SystemPlan,
    SystemRunResult,
)


def digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RuntimeContextGraphBuilder:
    """Build a compact provenance index without retaining raw prompts or payloads."""

    def build(
        self,
        *,
        system_run_id: str,
        goal: str,
        plan: SystemPlan,
        context_run_id: str,
        collaboration_run_id: str,
        delegated_tasks: tuple[dict[str, object], ...],
        context: ContextInvestigationResult,
        collaboration: CollaborationResult,
        result: SystemRunResult,
    ) -> RuntimeContextGraph:
        nodes: list[ContextGraphNode] = []
        edges: list[ContextGraphEdge] = []

        def node(
            key: str,
            node_type: str,
            label: str,
            owner: str,
            status: str,
            ref: str | None,
            payload: object,
            classification: str = "metadata",
        ) -> None:
            nodes.append(
                ContextGraphNode.model_validate(
                    {
                        "node_key": key,
                        "node_type": node_type,
                        "label": label,
                        "owner": owner,
                        "status": status,
                        "ref": ref,
                        "payload_digest": digest(payload),
                        "classification": classification,
                    }
                )
            )

        def edge(
            source: str,
            target: str,
            relation: str,
            *,
            suffix: str = "",
        ) -> None:
            key = f"{source}:{relation}:{target}{suffix}"
            edges.append(
                ContextGraphEdge.model_validate(
                    {
                        "edge_key": key,
                        "from_node_key": source,
                        "to_node_key": target,
                        "relation_type": relation,
                    }
                )
            )

        node("goal", "goal", "系统级调查目标", "central-supervisor", "accepted", None, goal)
        node(
            "plan",
            "plan",
            "已验证的任务 DAG",
            "central-supervisor",
            "accepted",
            f"caseops://system-runs/{system_run_id}/plan",
            plan.model_dump(mode="json"),
        )
        edge("goal", "plan", "DECOMPOSED_INTO")

        for step in plan.steps:
            key = f"step:{step.key}"
            node(
                key,
                "step",
                step.goal,
                step.owner,
                "succeeded",
                f"caseops://system-runs/{system_run_id}/steps/{step.key}",
                step.model_dump(mode="json"),
            )
            edge("plan", key, "DECOMPOSED_INTO")
            for dependency in step.depends_on:
                edge(key, f"step:{dependency}", "DEPENDS_ON")

        pack_key = f"context-pack:{context.context_pack.pack_id}"
        node(
            pack_key,
            "context_pack",
            "受治理的 Context Pack",
            "context-team",
            context.answer.verdict.value,
            f"caseops://context-runs/{context_run_id}",
            context.context_pack.model_dump(mode="json"),
            "confidential",
        )
        edge("step:context-evidence", pack_key, "PRODUCED")

        evidence_nodes: dict[str, str] = {}

        def evidence_node(ref: str, payload: object, owner: str) -> str:
            existing = evidence_nodes.get(ref)
            if existing is not None:
                return existing
            key = f"evidence:{digest(ref)[:20]}"
            evidence_nodes[ref] = key
            node(
                key,
                "evidence",
                "受版本约束的证据引用",
                owner,
                "accepted",
                ref,
                payload,
                "confidential",
            )
            return key

        for item in context.context_pack.evidence:
            key = evidence_node(
                item.locator,
                {
                    "content_hash": item.content_hash,
                    "source_version": item.source_version,
                    "valid_from": item.valid_from,
                    "valid_to": item.valid_to,
                },
                "context-team",
            )
            edge(pack_key, key, "USED")

        for task in delegated_tasks:
            task_id = str(task["task_id"])
            specialist = str(task["specialist_id"])
            task_key = f"task:{task_id}"
            node(
                task_key,
                "delegated_task",
                f"{specialist} 专业委托",
                f"{specialist}-specialist",
                str(task["status"]),
                f"caseops://collaboration-runs/{collaboration_run_id}/tasks/{task_id}",
                task,
            )
            edge("step:specialist-collaboration", task_key, "DECOMPOSED_INTO")

        evidence_by_id = {
            item.evidence_id: item.locator for item in context.context_pack.evidence
        }
        for claim in result.claims:
            claim_key = f"claim:{claim.source}:{claim.claim_id}"
            node(
                claim_key,
                "claim",
                claim.statement,
                claim.source,
                "accepted",
                None,
                claim.model_dump(mode="json"),
                "internal",
            )
            producer = (
                pack_key
                if claim.source == "context-team"
                else "step:specialist-collaboration"
            )
            edge(producer, claim_key, "PRODUCED")
            for ref in claim.evidence_refs:
                evidence_key = evidence_node(ref, {"ref": ref}, claim.source)
                edge(claim_key, evidence_key, "SUPPORTED_BY")

        acceptance_key = "acceptance:system"
        node(
            acceptance_key,
            "acceptance",
            "跨团队确定性验收",
            "central-supervisor",
            "passed" if result.outcome != "SYSTEM_REJECTED" else "failed",
            f"caseops://system-runs/{system_run_id}/acceptance",
            [check.model_dump(mode="json") for check in result.checks],
        )
        edge("step:system-acceptance", acceptance_key, "PRODUCED")
        edge(pack_key, acceptance_key, "VALIDATED_BY", suffix=":context")
        edge(
            "step:specialist-collaboration",
            acceptance_key,
            "VALIDATED_BY",
            suffix=":collaboration",
        )
        for claim in result.claims:
            edge(
                f"claim:{claim.source}:{claim.claim_id}",
                acceptance_key,
                "VALIDATED_BY",
            )

        result_key = "result:system"
        node(
            result_key,
            "result",
            "系统级合龙结论",
            "central-supervisor",
            result.outcome,
            f"caseops://system-runs/{system_run_id}",
            result.model_dump(mode="json"),
            "internal",
        )
        edge(acceptance_key, result_key, "DERIVED_FROM")
        edge("step:system-acceptance", result_key, "PRODUCED")

        # This assertion catches accidental loss of claim provenance during changes.
        supported_claims = {
            edge.from_node_key for edge in edges if edge.relation_type == "SUPPORTED_BY"
        }
        expected_claims = {node.node_key for node in nodes if node.node_type == "claim"}
        if supported_claims != expected_claims:
            raise ValueError("every runtime claim must have evidence support")
        _ = evidence_by_id  # documents the context evidence-id to locator boundary.
        return RuntimeContextGraph(
            system_run_id=system_run_id,
            nodes=tuple(nodes),
            edges=tuple(edges),
        )
