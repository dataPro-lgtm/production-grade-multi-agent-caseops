from __future__ import annotations

from collections import defaultdict

from .contracts import (
    Claim,
    CollaborationResult,
    JoinDecision,
    JoinPolicy,
    SpecialistId,
    SpecialistResult,
)


class EvidenceJoin:
    """Deterministic join gate; it does not ask a model to hide disagreement."""

    def evaluate(
        self,
        *,
        expected_tasks: dict[SpecialistId, str],
        results: list[SpecialistResult],
        policy: JoinPolicy,
    ) -> CollaborationResult:
        valid: list[SpecialistResult] = []
        failed: set[SpecialistId] = set()
        seen: set[SpecialistId] = set()

        for result in results:
            expected_task_id = expected_tasks.get(result.specialist_id)
            if expected_task_id != result.task_id or result.specialist_id in seen:
                failed.add(result.specialist_id)
                continue
            seen.add(result.specialist_id)
            if result.status == "failed":
                failed.add(result.specialist_id)
                continue
            if any(
                not self._evidence_allowed(ref, result.specialist_id)
                for claim in result.claims
                for ref in claim.evidence_refs
            ):
                failed.add(result.specialist_id)
                continue
            valid.append(result)

        accepted = {result.specialist_id for result in valid}
        missing_required = set(policy.required_specialists) - accepted
        conflicts = self._conflicts(valid)
        quorum_met = len(accepted) >= policy.minimum_successes
        join = JoinDecision(
            accepted_specialists=tuple(sorted(accepted)),
            failed_specialists=tuple(sorted(failed)),
            missing_required_specialists=tuple(sorted(missing_required)),
            conflicts=tuple(conflicts),
            quorum_met=quorum_met,
        )
        claims = tuple(claim for result in valid for claim in result.claims)
        evidence = tuple(
            dict.fromkeys(ref for claim in claims for ref in claim.evidence_refs)
        )

        if conflicts:
            return self._result(
                "CONFLICT_REQUIRES_HUMAN",
                "专业结论存在不可自动消解的冲突，系统保留异议并转交人工复核。",
                join,
                claims,
                evidence,
                "route_to_human_reviewer",
            )
        if missing_required or not quorum_met:
            return self._result(
                "INSUFFICIENT_EVIDENCE",
                "必要专业节点或最低成功数未满足，当前证据不足以形成完整结论。",
                join,
                claims,
                evidence,
                "request_missing_evidence",
            )
        if failed or len(accepted) < len(expected_tasks):
            return self._result(
                "PARTIAL_EVIDENCE",
                "核心证据已通过验收，但部分专业节点失败，结论以降级状态交付。",
                join,
                claims,
                evidence,
                "continue_read_only_review",
            )
        risk_review = any(
            claim.key == "risk_disposition" and claim.value == "manual_review_required"
            for claim in claims
        )
        return self._result(
            "COMPLETE_WITH_REVIEW_REQUIRED" if risk_review else "COMPLETE",
            (
                "三个专业节点均完成并通过证据验收；风险信号要求人工复核。"
                if risk_review
                else "三个专业节点均完成并通过证据验收。"
            ),
            join,
            claims,
            evidence,
            "route_to_human_reviewer" if risk_review else "continue_read_only_review",
        )

    @staticmethod
    def _evidence_allowed(ref: str, specialist: SpecialistId) -> bool:
        prefixes = {
            SpecialistId.COVERAGE: ("case://", "policy://"),
            SpecialistId.DOCUMENT: ("case://", "evidence://", "alias-rule://"),
            SpecialistId.RISK: ("risk-signal://", "risk-rule://"),
        }
        return ref.startswith(prefixes[specialist])

    @staticmethod
    def _conflicts(results: list[SpecialistResult]) -> list[str]:
        by_key: dict[str, set[str]] = defaultdict(set)
        for result in results:
            for claim in result.claims:
                by_key[claim.key].add(claim.value)
        return sorted(key for key, values in by_key.items() if len(values) > 1)

    @staticmethod
    def _result(
        outcome: str,
        summary: str,
        join: JoinDecision,
        claims: tuple[Claim, ...],
        evidence: tuple[str, ...],
        action: str,
    ) -> CollaborationResult:
        return CollaborationResult.model_validate(
            {
                "outcome": outcome,
                "summary": summary,
                "join": join,
                "claims": claims,
                "evidence_refs": evidence,
                "recommended_action": action,
            }
        )
