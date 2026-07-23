from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from caseops.agent.tools import (
    GET_CASE,
    GET_POLICY,
    LIST_DOCUMENTS,
    LIST_RISK_SIGNALS,
    RESOLVE_ALIAS,
    DatabaseToolExecutor,
    ToolExecutor,
)
from caseops.service import Principal

from .contracts import Claim, DelegationTask, SpecialistId, SpecialistResult


class SpecialistGateway(Protocol):
    async def execute(
        self,
        *,
        task: DelegationTask,
        principal: Principal,
    ) -> SpecialistResult:
        """Execute one bounded delegation through a local or remote Agent."""


class DelegationRejected(RuntimeError):
    pass


class DirectSpecialistGateway:
    """Process-local adapter used by tests and the developer profile."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        executor: ToolExecutor | None = None,
    ) -> None:
        if executor is None and session_factory is None:
            raise ValueError("specialist gateway requires a tool executor")
        if executor is None:
            if session_factory is None:
                raise ValueError("session factory is required for direct tools")
            executor = DatabaseToolExecutor(session_factory)
        self._executor = executor

    async def execute(
        self,
        *,
        task: DelegationTask,
        principal: Principal,
    ) -> SpecialistResult:
        self._authorize(task, principal)
        if task.specialist_id is SpecialistId.COVERAGE:
            return await self._coverage(task, principal)
        if task.specialist_id is SpecialistId.DOCUMENT:
            return await self._document(task, principal)
        if task.specialist_id is SpecialistId.RISK:
            return await self._risk(task, principal)
        raise DelegationRejected(f"unknown specialist: {task.specialist_id}")

    @staticmethod
    def _authorize(task: DelegationTask, principal: Principal) -> None:
        if task.deadline_at <= datetime.now(UTC):
            raise DelegationRejected("delegation deadline has expired")
        required = frozenset(task.required_scopes)
        if not required.issubset(principal.scopes):
            raise DelegationRejected("delegation requested scopes outside principal")

    async def _coverage(
        self,
        task: DelegationTask,
        principal: Principal,
    ) -> SpecialistResult:
        case = await self._executor.execute(
            principal=principal,
            run_id=task.task_id,
            tool_name=GET_CASE,
            arguments={"case_id": task.case_id},
        )
        policy = await self._executor.execute(
            principal=principal,
            run_id=task.task_id,
            tool_name=GET_POLICY,
            arguments={"case_id": task.case_id},
        )
        required = ",".join(
            sorted(str(item["code"]) for item in policy["required_documents"])
        )
        return SpecialistResult(
            task_id=task.task_id,
            specialist_id=task.specialist_id,
            status="succeeded",
            summary="已锁定案件绑定的规则版本与材料要求。",
            claims=(
                Claim(
                    key="required_document_set",
                    value=required,
                    confidence=1.0,
                    evidence_refs=(
                        str(case["evidence_ref"]),
                        str(policy["evidence_ref"]),
                    ),
                ),
            ),
            artifacts=(f"caseops://collaboration/{task.parent_run_id}/coverage",),
        )

    async def _document(
        self,
        task: DelegationTask,
        principal: Principal,
    ) -> SpecialistResult:
        case = await self._executor.execute(
            principal=principal,
            run_id=task.task_id,
            tool_name=GET_CASE,
            arguments={"case_id": task.case_id},
        )
        listed = await self._executor.execute(
            principal=principal,
            run_id=task.task_id,
            tool_name=LIST_DOCUMENTS,
            arguments={"case_id": task.case_id},
        )
        resolved_codes: list[str] = []
        evidence_refs = [str(case["evidence_ref"])]
        for document in listed["documents"]:
            resolved = await self._executor.execute(
                principal=principal,
                run_id=task.task_id,
                tool_name=RESOLVE_ALIAS,
                arguments={
                    "case_id": task.case_id,
                    "document_id": str(document["document_id"]),
                },
            )
            evidence_refs.append(str(resolved["evidence_ref"]))
            if resolved.get("resolved"):
                resolved_codes.append(str(resolved["canonical_code"]))
        received = {str(code) for code in case["received_document_codes"]}
        received.update(resolved_codes)
        value = "complete" if "ACCIDENT_CERTIFICATE" in received else "incomplete"
        return SpecialistResult(
            task_id=task.task_id,
            specialist_id=task.specialist_id,
            status="succeeded",
            summary="已完成来源材料读取与受治理的别名归一。",
            claims=(
                Claim(
                    key="document_completeness",
                    value=value,
                    confidence=1.0,
                    evidence_refs=tuple(evidence_refs),
                ),
            ),
            artifacts=(f"caseops://collaboration/{task.parent_run_id}/documents",),
        )

    async def _risk(
        self,
        task: DelegationTask,
        principal: Principal,
    ) -> SpecialistResult:
        response = await self._executor.execute(
            principal=principal,
            run_id=task.task_id,
            tool_name=LIST_RISK_SIGNALS,
            arguments={"case_id": task.case_id},
        )
        signals = response["signals"]
        if not signals:
            return SpecialistResult(
                task_id=task.task_id,
                specialist_id=task.specialist_id,
                status="partial",
                summary="没有取得风险信号，不能自动给出风险处置结论。",
                missing_evidence=("case risk signals",),
            )
        values = {str(signal["signal_code"]): signal["signal_value"] for signal in signals}
        amount_signal = values.get("CLAIM_AMOUNT")
        tenure_signal = values.get("POLICY_TENURE_DAYS")
        amount = (
            int(amount_signal.get("amount", 0)) if isinstance(amount_signal, dict) else 0
        )
        tenure = (
            int(tenure_signal.get("days", 99999))
            if isinstance(tenure_signal, dict)
            else 99999
        )
        review_required = amount >= 100000 and tenure < 30
        evidence_refs = tuple(str(signal["source_ref"]) for signal in signals) + (
            "risk-rule://rapid-high-value-claim@2026.1",
        )
        return SpecialistResult(
            task_id=task.task_id,
            specialist_id=task.specialist_id,
            status="succeeded",
            summary=(
                "高金额且保单生效时间较短，触发人工风险复核门禁。"
                if review_required
                else "当前结构化信号未触发人工风险复核门禁。"
            ),
            claims=(
                Claim(
                    key="risk_disposition",
                    value=(
                        "manual_review_required" if review_required else "standard_review"
                    ),
                    confidence=1.0,
                    evidence_refs=evidence_refs,
                ),
            ),
            artifacts=(f"caseops://collaboration/{task.parent_run_id}/risk",),
        )
