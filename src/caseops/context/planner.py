from __future__ import annotations

from caseops.context.contracts import (
    ContextInvestigationRequest,
    RetrievalChannel,
    RetrievalPlan,
)

RELATION_SIGNALS = ("为什么", "关联", "关系", "规则", "复核", "依据")


class GovernedQueryPlanner:
    """Build an allowlisted retrieval plan instead of arbitrary SQL or Cypher."""

    def plan(
        self,
        *,
        case_id: str,
        request: ContextInvestigationRequest,
    ) -> RetrievalPlan:
        channels = [
            RetrievalChannel.STRUCTURED,
            RetrievalChannel.FULL_TEXT,
        ]
        graph_paths: tuple[str, ...] = ()
        if any(signal in request.question for signal in RELATION_SIGNALS):
            channels.append(RetrievalChannel.GRAPH)
            graph_paths = (
                "case-policy-required-document",
                "case-document-normalization",
                "case-risk-review-rule",
            )

        terms: list[str] = [case_id]
        mappings = (
            (("材料", "事故证明", "认定书"), ("事故证明", "道路交通事故认定书")),
            (("规则", "保单"), ("规则版本", "必要材料")),
            (("风险", "复核", "金额"), ("人工复核", "理赔金额", "保单期限")),
        )
        for signals, additions in mappings:
            if any(signal in request.question for signal in signals):
                terms.extend(additions)

        return RetrievalPlan(
            question=request.question,
            case_id=case_id,
            purpose=request.purpose,
            as_of=request.as_of,
            channels=tuple(channels),
            query_terms=tuple(dict.fromkeys(terms)),
            seed_entities=(f"case:{case_id}",),
            graph_path_templates=graph_paths,
            max_hops=2,
            candidate_limit=40,
            evidence_token_budget=request.evidence_token_budget,
            max_rounds=request.max_rounds,
        )

    def broaden(self, plan: RetrievalPlan) -> RetrievalPlan:
        channels = list(plan.channels)
        graph_paths = plan.graph_path_templates
        if RetrievalChannel.GRAPH not in channels:
            channels.append(RetrievalChannel.GRAPH)
            graph_paths = (
                "case-policy-required-document",
                "case-document-normalization",
                "case-risk-review-rule",
            )
        return plan.model_copy(
            update={
                "channels": tuple(channels),
                "graph_path_templates": graph_paths,
            }
        )
