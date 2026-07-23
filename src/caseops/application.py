from __future__ import annotations

from .domain import (
    Decision,
    DraftNotification,
    InvestigationRequest,
    InvestigationResult,
    RecommendedAction,
)
from .errors import DataContractError
from .policy import NotificationActionPolicy
from .ports import CaseRepository, PolicyRepository


class InvestigateCase:
    """Deterministic baseline use case used before introducing an Agent."""

    def __init__(
        self,
        cases: CaseRepository,
        policies: PolicyRepository,
        action_policy: NotificationActionPolicy | None = None,
    ) -> None:
        self._cases = cases
        self._policies = policies
        self._action_policy = action_policy or NotificationActionPolicy()

    def execute(self, request: InvestigationRequest) -> InvestigationResult:
        self._action_policy.require_allowed(request.notification_action)

        case, case_evidence = self._cases.get(
            request.tenant_id,
            request.case_id,
        )

        policy, policy_evidence = self._policies.get(
            request.tenant_id,
            case.policy_id,
            case.policy_version,
        )
        if not policy.is_effective_on(case.submitted_at.date()):
            raise DataContractError(
                source=policy_evidence.ref,
                reason="规则版本在案件提交日期未生效。",
            )

        received = set(case.received_document_codes)
        missing = tuple(
            document
            for document in policy.required_documents
            if document.code not in received
        )

        if missing:
            names = "、".join(document.name for document in missing)
            decision = Decision(
                code="MISSING_REQUIRED_DOCUMENTS",
                explanation=f"案件缺少规则要求的必要材料：{names}。",
            )
            draft = DraftNotification(
                subject=f"案件 {case.case_id} 补充材料提醒（待审核）",
                body=(
                    f"您好，案件 {case.case_id} 当前仍需补充：{names}。"
                    "本消息为系统生成草稿，须经人工审核后方可发送。"
                ),
            )
            action = RecommendedAction(
                type="draft_notification",
                execution_policy="human_approval_required",
                side_effect="none",
            )
        else:
            decision = Decision(
                code="DOCUMENTS_COMPLETE",
                explanation="案件已具备当前规则要求的全部材料。",
            )
            draft = None
            action = RecommendedAction(
                type="continue_review",
                execution_policy="deterministic_workflow",
                side_effect="none",
            )

        return InvestigationResult(
            schema_version="caseops.investigation.v1",
            case_id=case.case_id,
            tenant_id=case.tenant_id,
            case_status=case.status,
            decision=decision,
            missing_documents=missing,
            recommended_action=action,
            draft_notification=draft,
            evidence=(case_evidence, policy_evidence),
        )
