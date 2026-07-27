from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from caseops.config import Settings
from caseops.errors import (
    IdempotencyConflict,
    SystemRunNotFound,
    SystemRunNotTerminal,
)
from caseops.infrastructure.models import (
    AuditEventRecord,
    DelegatedTaskRecord,
    OperationalAssessmentRecord,
    OperationalCostEventRecord,
    RuntimeContextEdgeRecord,
    RuntimeContextNodeRecord,
    SecurityDecisionRecord,
    SystemRunRecord,
    SystemStepRecord,
    new_id,
)
from caseops.service import Principal

from .contracts import (
    OperationalAssessment,
    OperationalControl,
    OperationalCostSummary,
    OperationalEvidenceInventory,
    OperationalFailure,
    OperationalImpact,
    OperationalTimelineEvent,
    OperationalUnitMeasure,
    OperationalVersions,
)

TERMINAL_STATUSES = frozenset({"completed", "needs_human", "failed"})
STEP_ORDER = {
    "context-evidence": 0,
    "specialist-collaboration": 1,
    "system-acceptance": 2,
}


@dataclass(frozen=True, slots=True)
class OperationalAssessmentExecution:
    assessment_id: str
    idempotency_key: str
    replayed: bool
    report_digest: str
    created_at: datetime
    report: dict[str, object]


def _request_hash(system_run_id: str) -> str:
    canonical = json.dumps(
        {
            "schema_version": "caseops.operational-assessment.request.v1",
            "system_run_id": system_run_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class OperationsService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def assess(
        self,
        *,
        principal: Principal,
        system_run_id: str,
        idempotency_key: str,
        request_id: str,
    ) -> OperationalAssessmentExecution:
        request_hash = _request_hash(system_run_id)
        try:
            with self._session.begin():
                existing = self._find_existing(
                    principal.tenant_id,
                    idempotency_key,
                )
                if existing is not None:
                    return self._replay(existing, request_hash)

                run = self._require_run(principal.tenant_id, system_run_id)
                if run.status not in TERMINAL_STATUSES:
                    raise SystemRunNotTerminal(system_run_id, run.status)

                assessment_id = new_id()
                report, measures = self._build_report(
                    assessment_id=assessment_id,
                    run=run,
                )
                report_payload = report.model_dump(mode="json")
                report_digest = _digest(report_payload)
                record = OperationalAssessmentRecord(
                    id=assessment_id,
                    tenant_id=principal.tenant_id,
                    system_run_id=system_run_id,
                    actor_id=principal.actor_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    status=report.status,
                    severity=report.severity,
                    report=report_payload,
                    report_digest=report_digest,
                )
                self._session.add(record)
                self._session.flush()
                self._session.add_all(
                    [
                        OperationalCostEventRecord(
                            tenant_id=principal.tenant_id,
                            system_run_id=system_run_id,
                            assessment_id=assessment_id,
                            dimension="goal-attributed",
                            resource_type=measure.resource_type,
                            quantity=measure.quantity,
                            unit=measure.unit,
                            attribution=measure.attribution,
                            monetary_cost_microunits=None,
                        )
                        for measure in measures
                    ]
                )
                self._session.add(
                    AuditEventRecord(
                        tenant_id=principal.tenant_id,
                        actor_id=principal.actor_id,
                        action="system.operations.assess",
                        subject_type="system_run",
                        subject_id=system_run_id,
                        request_id=request_id,
                        outcome=report.status,
                        details={
                            "assessment_id": assessment_id,
                            "severity": report.severity,
                            "first_failure_code": (
                                report.first_failure.error_code
                                if report.first_failure
                                else None
                            ),
                            "report_digest": report_digest,
                            "side_effect": "none",
                        },
                    )
                )
                return OperationalAssessmentExecution(
                    assessment_id=assessment_id,
                    idempotency_key=idempotency_key,
                    replayed=False,
                    report_digest=report_digest,
                    created_at=record.created_at,
                    report=report_payload,
                )
        except IntegrityError:
            self._session.rollback()
            existing = self._find_existing(principal.tenant_id, idempotency_key)
            if existing is None:
                raise
            return self._replay(existing, request_hash)

    def _build_report(
        self,
        *,
        assessment_id: str,
        run: SystemRunRecord,
    ) -> tuple[OperationalAssessment, tuple[OperationalUnitMeasure, ...]]:
        steps = sorted(
            self._session.scalars(
                select(SystemStepRecord).where(
                    SystemStepRecord.system_run_id == run.id,
                    SystemStepRecord.tenant_id == run.tenant_id,
                )
            ).all(),
            key=lambda step: STEP_ORDER.get(step.step_key, 99),
        )
        tasks = (
            self._session.scalars(
                select(DelegatedTaskRecord).where(
                    DelegatedTaskRecord.run_id == run.collaboration_run_id,
                    DelegatedTaskRecord.tenant_id == run.tenant_id,
                )
            ).all()
            if run.collaboration_run_id
            else []
        )
        task_ids = [task.id for task in tasks]
        security_count = (
            self._session.scalar(
                select(func.count(SecurityDecisionRecord.id)).where(
                    SecurityDecisionRecord.tenant_id == run.tenant_id,
                    SecurityDecisionRecord.task_id.in_(task_ids),
                )
            )
            or 0
            if task_ids
            else 0
        )
        node_count = (
            self._session.scalar(
                select(func.count(RuntimeContextNodeRecord.id)).where(
                    RuntimeContextNodeRecord.system_run_id == run.id,
                    RuntimeContextNodeRecord.tenant_id == run.tenant_id,
                )
            )
            or 0
        )
        edge_count = (
            self._session.scalar(
                select(func.count(RuntimeContextEdgeRecord.id)).where(
                    RuntimeContextEdgeRecord.system_run_id == run.id,
                    RuntimeContextEdgeRecord.tenant_id == run.tenant_id,
                )
            )
            or 0
        )
        incident = run.status == "failed"
        completed_steps = sum(step.status == "succeeded" for step in steps)
        failed_steps = sum(step.status == "failed" for step in steps)
        failed_tasks = sum(
            task.status in {"failed", "timed_out", "rejected"} for task in tasks
        )
        first_failure = self._first_failure(run, steps, tasks) if incident else None
        timeline = self._timeline(run, steps)
        successful_goals = 0 if incident else 1
        execution_attribution = "wasted" if incident else "productive"
        measures = (
            self._measure(
                "system_step_attempt",
                sum(step.attempt_count for step in steps),
                "attempt",
                execution_attribution,
                successful_goals,
            ),
            self._measure(
                "context_run",
                int(run.context_run_id is not None),
                "run",
                execution_attribution,
                successful_goals,
            ),
            self._measure(
                "delegated_task_attempt",
                sum(task.attempt_count for task in tasks),
                "attempt",
                execution_attribution,
                successful_goals,
            ),
            self._measure(
                "security_decision",
                security_count,
                "decision",
                "protective",
                successful_goals,
            ),
        )
        report = OperationalAssessment(
            assessment_id=assessment_id,
            system_run_id=run.id,
            status="incident" if incident else "healthy",
            severity="SEV-2" if incident else "none",
            impact=OperationalImpact(
                terminal_status=run.status,
                goal_succeeded=not incident,
                completed_step_count=completed_steps,
                failed_step_count=failed_steps,
                failed_delegated_task_count=failed_tasks,
            ),
            first_failure=first_failure,
            timeline=timeline,
            evidence=OperationalEvidenceInventory(
                system_run_ref=f"caseops://system-runs/{run.id}",
                step_refs=tuple(
                    f"caseops://system-runs/{run.id}/steps/{step.step_key}"
                    for step in steps
                ),
                context_run_ref=(
                    f"caseops://context-runs/{run.context_run_id}"
                    if run.context_run_id
                    else None
                ),
                collaboration_run_ref=(
                    f"caseops://collaboration-runs/{run.collaboration_run_id}"
                    if run.collaboration_run_id
                    else None
                ),
                context_graph_ref=(
                    f"/v1/system-runs/{run.id}/context-graph" if node_count else None
                ),
                context_graph_node_count=node_count,
                context_graph_edge_count=edge_count,
                delegated_task_count=len(tasks),
                security_decision_count=security_count,
            ),
            cost=OperationalCostSummary(
                successful_goal_count=successful_goals,
                measures=measures,
                limitation=(
                    "当前确定性模式未调用计费模型，也未配置供应商价格表；"
                    "因此只报告可核验的资源数量，不推测货币成本。"
                ),
            ),
            recommended_controls=self._controls(first_failure),
            versions=OperationalVersions(
                release=f"v{self._settings.service_version}",
                database_revision=self._settings.expected_database_revision,
                tool_guard_policy=self._settings.tool_guard_policy_version,
            ),
        )
        return report, measures

    @staticmethod
    def _measure(
        resource_type: str,
        quantity: int,
        unit: str,
        attribution: str,
        successful_goals: int,
    ) -> OperationalUnitMeasure:
        return OperationalUnitMeasure.model_validate(
            {
                "resource_type": resource_type,
                "quantity": quantity,
                "unit": unit,
                "attribution": attribution,
                "per_successful_goal": (
                    float(quantity) / successful_goals if successful_goals else None
                ),
                "monetary_cost_microunits": None,
            }
        )

    @staticmethod
    def _first_failure(
        run: SystemRunRecord,
        steps: list[SystemStepRecord],
        tasks: Sequence[DelegatedTaskRecord],
    ) -> OperationalFailure:
        failed_tasks = [
            task for task in tasks if task.status in {"failed", "timed_out", "rejected"}
        ]
        if failed_tasks:
            task = min(
                failed_tasks,
                key=lambda item: item.completed_at or item.updated_at,
            )
            error = task.error or {}
            return OperationalFailure(
                layer="collaboration",
                component=f"{task.specialist_id}-specialist",
                step_key="specialist-collaboration",
                error_code=str(error.get("code", "DELEGATED_TASK_FAILED")),
                occurred_at=task.completed_at or task.updated_at,
                evidence_refs=(
                    f"caseops://collaboration-runs/{task.run_id}/tasks/{task.id}",
                    f"caseops://system-runs/{run.id}/steps/specialist-collaboration",
                ),
            )
        failed = next((step for step in steps if step.status == "failed"), None)
        if failed is None:
            return OperationalFailure(
                layer="orchestration",
                component="central-supervisor",
                step_key="unknown",
                error_code="UNKNOWN_TERMINAL_FAILURE",
                occurred_at=run.updated_at,
                evidence_refs=(f"caseops://system-runs/{run.id}",),
            )
        layer = {
            "context-evidence": "context",
            "specialist-collaboration": "collaboration",
            "system-acceptance": "system_acceptance",
        }.get(failed.step_key, "orchestration")
        error = failed.error or {}
        return OperationalFailure.model_validate(
            {
                "layer": layer,
                "component": failed.owner,
                "step_key": failed.step_key,
                "error_code": str(error.get("code", "UNCLASSIFIED_FAILURE")),
                "occurred_at": failed.completed_at or run.updated_at,
                "evidence_refs": (
                    f"caseops://system-runs/{run.id}/steps/{failed.step_key}",
                    f"caseops://system-runs/{run.id}",
                ),
            }
        )

    @staticmethod
    def _timeline(
        run: SystemRunRecord,
        steps: list[SystemStepRecord],
    ) -> tuple[OperationalTimelineEvent, ...]:
        events = [
            OperationalTimelineEvent(
                occurred_at=run.created_at,
                phase="created",
                fact="system run accepted for governed execution",
                evidence_ref=f"caseops://system-runs/{run.id}",
            )
        ]
        for step in steps:
            ref = f"caseops://system-runs/{run.id}/steps/{step.step_key}"
            if step.started_at:
                events.append(
                    OperationalTimelineEvent(
                        occurred_at=step.started_at,
                        phase="started",
                        fact=f"{step.step_key} started",
                        evidence_ref=ref,
                    )
                )
            if step.completed_at:
                events.append(
                    OperationalTimelineEvent(
                        occurred_at=step.completed_at,
                        phase="failed" if step.status == "failed" else "completed",
                        fact=f"{step.step_key} {step.status}",
                        evidence_ref=ref,
                    )
                )
        events.append(
            OperationalTimelineEvent(
                occurred_at=run.completed_at or run.updated_at,
                phase="terminal",
                fact=f"system run reached terminal status {run.status}",
                evidence_ref=f"caseops://system-runs/{run.id}",
            )
        )
        return tuple(sorted(events, key=lambda event: event.occurred_at))

    @staticmethod
    def _controls(
        failure: OperationalFailure | None,
    ) -> tuple[OperationalControl, ...]:
        if failure is None:
            return (
                OperationalControl(
                    action="none",
                    trigger="no operational incident detected",
                    safe_state="continue read-only operation under existing SLO",
                ),
            )
        if failure.layer == "collaboration":
            return (
                OperationalControl(
                    action="wait_for_dependency_readiness",
                    trigger=failure.error_code,
                    safe_state=(
                        "keep the run failed and preserve completed context evidence"
                    ),
                ),
                OperationalControl(
                    action="route_to_human",
                    trigger="goal cannot converge while A2A is unavailable",
                    safe_state="no external side effect",
                ),
                OperationalControl(
                    action="retry_from_failed_step",
                    trigger="A2A readiness restored",
                    safe_state="reuse only version-compatible durable evidence",
                ),
            )
        return (
            OperationalControl(
                action="repair_failed_step",
                trigger=failure.error_code,
                safe_state="retain terminal failure and evidence until repair is verified",
            ),
        )

    def _require_run(
        self,
        tenant_id: str,
        system_run_id: str,
    ) -> SystemRunRecord:
        row = self._session.scalar(
            select(SystemRunRecord).where(
                SystemRunRecord.id == system_run_id,
                SystemRunRecord.tenant_id == tenant_id,
            )
        )
        if row is None:
            raise SystemRunNotFound(system_run_id)
        return row

    def _find_existing(
        self,
        tenant_id: str,
        idempotency_key: str,
    ) -> OperationalAssessmentRecord | None:
        return self._session.scalar(
            select(OperationalAssessmentRecord).where(
                OperationalAssessmentRecord.tenant_id == tenant_id,
                OperationalAssessmentRecord.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _replay(
        existing: OperationalAssessmentRecord,
        request_hash: str,
    ) -> OperationalAssessmentExecution:
        if existing.request_hash != request_hash:
            raise IdempotencyConflict(existing.idempotency_key)
        return OperationalAssessmentExecution(
            assessment_id=existing.id,
            idempotency_key=existing.idempotency_key,
            replayed=True,
            report_digest=existing.report_digest,
            created_at=existing.created_at,
            report=existing.report,
        )
