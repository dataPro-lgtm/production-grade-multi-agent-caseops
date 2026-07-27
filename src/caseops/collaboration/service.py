from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from caseops.config import Settings
from caseops.errors import IdempotencyConflict
from caseops.infrastructure.models import (
    AuditEventRecord,
    CollaborationRunRecord,
    DelegatedTaskRecord,
    OutboxEventRecord,
    new_id,
)
from caseops.infrastructure.repositories import SqlAlchemyCaseRepository
from caseops.platform.runtime_envelope import current_deadline
from caseops.service import Principal

from .contracts import (
    CloudEvent,
    CollaborationResult,
    CollaborationStatus,
    DelegationTask,
    JoinPolicy,
    SpecialistId,
    SpecialistResult,
    TaskStatus,
)
from .join import EvidenceJoin
from .specialists import (
    DelegationRejected,
    DirectSpecialistGateway,
    SpecialistGateway,
)


@dataclass(frozen=True, slots=True)
class TaskExecution:
    task_id: str
    specialist_id: str
    status: str
    attempt_count: int
    result: dict[str, object] | None
    error: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class CollaborationExecution:
    run_id: str
    idempotency_key: str
    status: str
    replayed: bool
    result: dict[str, object]
    tasks: tuple[TaskExecution, ...]
    created_at: datetime
    completed_at: datetime | None


def _request_hash(case_id: str, goal: str, policy: JoinPolicy) -> str:
    canonical = json.dumps(
        {
            "schema_version": "caseops.collaboration-request.v1",
            "case_id": case_id,
            "goal": goal,
            "join_policy": policy.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class CollaborationService:
    def __init__(
        self,
        *,
        session: Session,
        session_factory: sessionmaker[Session],
        settings: Settings,
        gateway: SpecialistGateway | None = None,
    ) -> None:
        self._session = session
        self._session_factory = session_factory
        self._settings = settings
        self._gateway = gateway

    async def execute(
        self,
        *,
        principal: Principal,
        case_id: str,
        goal: str,
        join_policy: JoinPolicy,
        idempotency_key: str,
        request_id: str,
    ) -> CollaborationExecution:
        request_hash = _request_hash(case_id, goal, join_policy)
        existing = self._find_existing(principal.tenant_id, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflict(idempotency_key)
            if existing.final_result is None:
                raise RuntimeError("incomplete collaboration run requires recovery")
            return self._execution(existing, replayed=True)

        SqlAlchemyCaseRepository(self._session).get(principal.tenant_id, case_id)
        run_id = new_id()
        tasks = self._plan(run_id, case_id, goal)
        row = CollaborationRunRecord(
            id=run_id,
            tenant_id=principal.tenant_id,
            case_id=case_id,
            actor_id=principal.actor_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            goal=goal,
            status=CollaborationStatus.CREATED.value,
            join_policy=join_policy.model_dump(mode="json"),
            expected_specialists=[task.specialist_id.value for task in tasks],
        )
        self._session.add(row)
        self._session.add_all(
            [
                DelegatedTaskRecord(
                    id=task.task_id,
                    run_id=run_id,
                    tenant_id=principal.tenant_id,
                    case_id=case_id,
                    specialist_id=task.specialist_id.value,
                    status=TaskStatus.PLANNED.value,
                    goal=task.goal,
                    acceptance_criteria=list(task.acceptance_criteria),
                    allowed_evidence_kinds=list(task.allowed_evidence_kinds),
                    required_scopes=list(task.required_scopes),
                    deadline_at=task.deadline_at,
                )
                for task in tasks
            ]
        )
        self._emit(
            tenant_id=principal.tenant_id,
            run_id=run_id,
            case_id=case_id,
            event_type="dev.caseops.collaboration.started.v1",
            data={"expected_specialists": row.expected_specialists},
        )
        self._session.commit()

        row.status = CollaborationStatus.DISPATCHING.value
        row.version += 1
        for task in tasks:
            task_row = self._require_task(task.task_id)
            task_row.status = TaskStatus.SUBMITTED.value
            task_row.version += 1
            task_row.attempt_count = 1
        self._session.commit()

        gateway = self._gateway or self._build_gateway()
        outcomes = await asyncio.gather(
            *[
                self._dispatch(
                    gateway=gateway,
                    task=task,
                    principal=Principal(
                        tenant_id=principal.tenant_id,
                        actor_id=f"{principal.actor_id}:delegate:{task.specialist_id}",
                        scopes=frozenset(task.required_scopes),
                    ),
                )
                for task in tasks
            ]
        )

        results: list[SpecialistResult] = []
        for task, outcome in zip(tasks, outcomes, strict=True):
            task_row = self._require_task(task.task_id)
            task_row.version += 1
            task_row.completed_at = datetime.now(UTC)
            if isinstance(outcome, SpecialistResult):
                task_row.result = outcome.model_dump(mode="json")
                task_row.status = (
                    TaskStatus.SUCCEEDED.value
                    if outcome.status != "failed"
                    else TaskStatus.FAILED.value
                )
                results.append(outcome)
                event_data: dict[str, object] = {
                    "task_id": task.task_id,
                    "specialist_id": task.specialist_id.value,
                    "status": task_row.status,
                }
            else:
                status, code, message = outcome
                task_row.status = status.value
                task_row.error = {"code": code, "message": message}
                results.append(
                    SpecialistResult(
                        task_id=task.task_id,
                        specialist_id=task.specialist_id,
                        status="failed",
                        summary=message,
                        error_code=code,
                    )
                )
                event_data = {
                    "task_id": task.task_id,
                    "specialist_id": task.specialist_id.value,
                    "status": task_row.status,
                    "error_code": code,
                }
            self._emit(
                tenant_id=principal.tenant_id,
                run_id=run_id,
                case_id=case_id,
                event_type="dev.caseops.delegation.completed.v1",
                data=event_data,
            )
        self._session.commit()

        row.status = CollaborationStatus.JOINING.value
        row.version += 1
        self._session.commit()
        result = EvidenceJoin().evaluate(
            expected_tasks={task.specialist_id: task.task_id for task in tasks},
            results=results,
            policy=join_policy,
        )
        row.status = self._terminal_status(result)
        row.final_result = result.model_dump(mode="json")
        row.completed_at = datetime.now(UTC)
        row.updated_at = row.completed_at
        row.version += 1
        self._session.add(
            AuditEventRecord(
                tenant_id=principal.tenant_id,
                actor_id=principal.actor_id,
                action="collaboration.run",
                subject_type="case",
                subject_id=case_id,
                request_id=request_id,
                outcome=result.outcome,
                details={
                    "run_id": run_id,
                    "accepted_specialists": [
                        item.value for item in result.join.accepted_specialists
                    ],
                    "conflicts": list(result.join.conflicts),
                    "side_effect": result.side_effect,
                },
            )
        )
        self._emit(
            tenant_id=principal.tenant_id,
            run_id=run_id,
            case_id=case_id,
            event_type="dev.caseops.collaboration.completed.v1",
            data={
                "outcome": result.outcome,
                "status": row.status,
                "recommended_action": result.recommended_action,
            },
        )
        self._session.commit()
        return self._execution(row, replayed=False)

    async def _dispatch(
        self,
        *,
        gateway: SpecialistGateway,
        task: DelegationTask,
        principal: Principal,
    ) -> SpecialistResult | tuple[TaskStatus, str, str]:
        timeout_seconds = min(
            self._settings.collaboration_task_timeout_seconds,
            max(0.0, (task.deadline_at - datetime.now(UTC)).total_seconds()),
        )
        if timeout_seconds <= 0:
            return (
                TaskStatus.TIMED_OUT,
                "REQUEST_DEADLINE_EXCEEDED",
                "request deadline expired before specialist dispatch",
            )
        try:
            return await asyncio.wait_for(
                gateway.execute(task=task, principal=principal),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return (
                TaskStatus.TIMED_OUT,
                "DELEGATION_TIMEOUT",
                "specialist exceeded the bounded delegation deadline",
            )
        except DelegationRejected as error:
            return TaskStatus.REJECTED, "DELEGATION_REJECTED", str(error)
        except Exception as error:
            return TaskStatus.FAILED, "SPECIALIST_FAILURE", str(error)

    def _build_gateway(self) -> SpecialistGateway:
        if self._settings.collaboration_transport == "direct":
            return DirectSpecialistGateway(self._session_factory)
        from .a2a_client import A2ASpecialistGateway

        return A2ASpecialistGateway(self._settings)

    @staticmethod
    def _plan(
        run_id: str,
        case_id: str,
        goal: str,
    ) -> tuple[DelegationTask, ...]:
        deadline = datetime.now(UTC) + timedelta(seconds=30)
        request_deadline = current_deadline()
        if request_deadline is not None:
            deadline = min(deadline, request_deadline)
        definitions = (
            (
                SpecialistId.COVERAGE,
                "核对案件绑定规则版本与必要材料集合。",
                ("返回规则版本", "返回必要材料集合", "每项结论包含证据引用"),
                ("case_snapshot", "policy_rule"),
                ("case:read", "policy:read"),
            ),
            (
                SpecialistId.DOCUMENT,
                "核对来源材料并执行受治理的材料名称归一。",
                ("返回材料完整性", "保留来源材料引用", "禁止写入案件"),
                ("case_snapshot", "source_document", "alias_rule"),
                ("case:read", "document:read", "document:resolve"),
            ),
            (
                SpecialistId.RISK,
                "根据结构化风险信号判断是否需要人工复核。",
                ("返回风险处置", "引用风险信号与规则", "禁止自动处置"),
                ("risk_signal", "risk_rule"),
                ("risk:read",),
            ),
        )
        return tuple(
            DelegationTask(
                task_id=new_id(),
                parent_run_id=run_id,
                case_id=case_id,
                specialist_id=specialist,
                goal=f"{goal} 子目标：{sub_goal}",
                acceptance_criteria=criteria,
                allowed_evidence_kinds=evidence,
                required_scopes=scopes,
                deadline_at=deadline,
            )
            for specialist, sub_goal, criteria, evidence, scopes in definitions
        )

    def _emit(
        self,
        *,
        tenant_id: str,
        run_id: str,
        case_id: str,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        event = CloudEvent(
            id=str(uuid4()),
            source="/caseops/collaboration",
            type=event_type,
            subject=f"case/{case_id}",
            time=datetime.now(UTC),
            dataschema="https://caseops.dev/schemas/collaboration-event-v1.json",
            correlationid=run_id,
            tenantid=tenant_id,
            data=data,
        )
        self._session.add(
            OutboxEventRecord(
                tenant_id=tenant_id,
                topic=event_type,
                aggregate_type="collaboration_run",
                aggregate_id=run_id,
                payload=event.model_dump(mode="json"),
            )
        )

    def _find_existing(
        self,
        tenant_id: str,
        idempotency_key: str,
    ) -> CollaborationRunRecord | None:
        return self._session.scalar(
            select(CollaborationRunRecord).where(
                CollaborationRunRecord.tenant_id == tenant_id,
                CollaborationRunRecord.idempotency_key == idempotency_key,
            )
        )

    def _require_task(self, task_id: str) -> DelegatedTaskRecord:
        row = self._session.get(DelegatedTaskRecord, task_id)
        if row is None:
            raise RuntimeError(f"delegated task disappeared: {task_id}")
        return row

    def _execution(
        self,
        row: CollaborationRunRecord,
        *,
        replayed: bool,
    ) -> CollaborationExecution:
        if row.final_result is None:
            raise RuntimeError("collaboration result is not terminal")
        task_rows = self._session.scalars(
            select(DelegatedTaskRecord)
            .where(
                DelegatedTaskRecord.run_id == row.id,
                DelegatedTaskRecord.tenant_id == row.tenant_id,
            )
            .order_by(DelegatedTaskRecord.specialist_id)
        ).all()
        return CollaborationExecution(
            run_id=row.id,
            idempotency_key=row.idempotency_key,
            status=row.status,
            replayed=replayed,
            result=row.final_result,
            tasks=tuple(
                TaskExecution(
                    task_id=task.id,
                    specialist_id=task.specialist_id,
                    status=task.status,
                    attempt_count=task.attempt_count,
                    result=task.result,
                    error=task.error,
                )
                for task in task_rows
            ),
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    def _terminal_status(result: CollaborationResult) -> str:
        if result.outcome == "CONFLICT_REQUIRES_HUMAN":
            return CollaborationStatus.NEEDS_HUMAN.value
        if result.outcome in {"PARTIAL_EVIDENCE", "INSUFFICIENT_EVIDENCE"}:
            return CollaborationStatus.PARTIAL.value
        return CollaborationStatus.COMPLETED.value
