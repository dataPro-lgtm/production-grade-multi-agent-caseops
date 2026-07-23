from __future__ import annotations

from caseops.context.contracts import (
    AnswerClaim,
    ContextAnswer,
    EvidenceItem,
    InvestigationVerdict,
)

REQUIRED_CLAIMS = frozenset(
    {
        "policy_version",
        "document_status",
        "claim_amount_signal",
        "policy_tenure_signal",
        "manual_review_rule",
    }
)


def supported_claims(evidence: tuple[EvidenceItem, ...]) -> frozenset[str]:
    return frozenset(
        claim
        for item in evidence
        for claim in item.supports_claims
        if claim in REQUIRED_CLAIMS
    )


def missing_claims(evidence: tuple[EvidenceItem, ...]) -> tuple[str, ...]:
    return tuple(sorted(REQUIRED_CLAIMS - supported_claims(evidence)))


class EvidenceBoundAnswerer:
    """Produce a deterministic answer whose every claim binds to evidence."""

    def answer(self, evidence: tuple[EvidenceItem, ...]) -> ContextAnswer:
        missing = missing_claims(evidence)
        if missing:
            return ContextAnswer(
                verdict=InvestigationVerdict.INSUFFICIENT_EVIDENCE,
                summary="当前 Context Pack 未覆盖全部必要主张，不能形成完整调查结论。",
                claims=(),
                unresolved_questions=missing,
                recommended_action="request_more_evidence",
            )

        by_claim: dict[str, list[EvidenceItem]] = {
            claim: [item for item in evidence if claim in item.supports_claims]
            for claim in REQUIRED_CLAIMS
        }
        policy = self._fact(by_claim["policy_version"], "policy_version")
        document_code = self._fact(
            by_claim["document_status"],
            "canonical_document_code",
        )
        review_evidence = [
            *by_claim["claim_amount_signal"],
            *by_claim["policy_tenure_signal"],
            *by_claim["manual_review_rule"],
        ]
        amount = self._integer_fact(review_evidence, "claim_amount")
        tenure = self._integer_fact(review_evidence, "policy_tenure_days")
        minimum_amount = self._integer_fact(
            review_evidence,
            "min_claim_amount",
        )
        maximum_tenure = self._integer_fact(
            review_evidence,
            "max_policy_tenure_days",
        )
        threshold = self._fact(
            review_evidence,
            "manual_review_threshold",
        )
        review_required = amount >= minimum_amount and tenure <= maximum_tenure
        claims = (
            AnswerClaim(
                claim_id="claim-policy-version",
                statement="本案适用的规则版本已经锁定。",
                value=str(policy),
                evidence_ids=tuple(item.evidence_id for item in by_claim["policy_version"]),
            ),
            AnswerClaim(
                claim_id="claim-document-status",
                statement="事故证明经受治理的别名规则归一后已满足材料要求。",
                value=f"satisfied:{document_code}",
                evidence_ids=tuple(
                    item.evidence_id for item in by_claim["document_status"]
                ),
            ),
            AnswerClaim(
                claim_id="claim-manual-review",
                statement=(
                    "高金额且保单期限较短触发人工复核规则。"
                    if review_required
                    else "当前风险信号未触发人工复核规则。"
                ),
                value=(
                    f"required={str(review_required).lower()}; "
                    f"claim_amount={amount}; policy_tenure_days={tenure}; "
                    f"threshold={threshold}"
                ),
                evidence_ids=tuple(item.evidence_id for item in review_evidence),
            ),
        )
        return ContextAnswer(
            verdict=InvestigationVerdict.COMPLETE,
            summary=(
                "C-102 的事故证明已满足 2026.1 规则要求；"
                + (
                    "高金额与短保单期限共同触发人工复核，"
                    if review_required
                    else "当前风险信号未触发人工复核，"
                )
                + "系统未执行任何外部动作。"
            ),
            claims=claims,
            unresolved_questions=(),
            recommended_action=(
                "route_to_human_reviewer"
                if review_required
                else "continue_read_only_review"
            ),
        )

    @staticmethod
    def _fact(
        evidence: list[EvidenceItem],
        key: str,
    ) -> object:
        values = {item.facts[key] for item in evidence if key in item.facts}
        if len(values) != 1:
            raise ValueError(f"evidence does not provide exactly one value for {key}")
        return next(iter(values))

    @classmethod
    def _integer_fact(
        cls,
        evidence: list[EvidenceItem],
        key: str,
    ) -> int:
        value = cls._fact(evidence, key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"evidence value for {key} must be an integer")
        return value
