from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from caseops.collaboration.contracts import (
    CloudEvent,
    CollaborationResult,
)
from caseops.collaboration.service import CollaborationExecution, CollaborationService
from caseops.config import Settings
from caseops.context.contracts import (
    ContextInvestigationRequest,
    ContextInvestigationResult,
)
from caseops.context.service import ContextExecution, ContextInvestigationService
from caseops.errors import IdempotencyConflict, SystemRunNotFound
from caseops.infrastructure.models import (
    AuditEventRecord,
    DelegatedTaskRecord,
    OutboxEventRecord,
    RuntimeContextEdgeRecord,
    RuntimeContextNodeRecord,
    SystemRunRecord,
    SystemStepRecord,
    new_id,
)
from caseops.infrastructure.repositories import SqlAlchemyCaseRepository
from caseops.service import Principal

from .acceptance import SystemAcceptance
from .contracts import (
    ContextGraphEdge,
    ContextGraphNode,
    RuntimeContextGraph,
    SystemPlan,
    SystemPlanStep,
    SystemRunRequest,
    SystemRunResult,
    SystemRunStatus,
    SystemStepStatus,
)
from .graph import RuntimeContextGraphBuilder, digest


@dataclass(frozen=True, slots=True)
class SystemStepExecution:
    step_key: str
    owner: str
    status: str
    attempt_count: int
    depends_on: tuple[str, ...]
    result_ref: str | None
    result_digest: str | None
    error: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class SystemRunExecution:
    system_run_id: str
    idempotency_key: str
    status: str
    replayed: bool
    resumed: bool
    result: dict[str, object]
    steps: tuple[SystemStepExecution, ...]
    child_runs: dict[str, str]
    context_graph_uri: str
    created_at: datetime
    completed_at: datetime | None


def _request_hash(case_id: str, request: SystemRunRequest) -> str:
    canonical = json.dumps(
        {
            "schema_version": "caseops.system-run-request.v1",
            "case_id": case_id,
            **request.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SystemRunService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def execute(
        self,
        *,
        principal: Principal,
        case_id: str,
        request: SystemRunRequest,
        idempotency_key: str,
        request_id: str,
    ) -> SystemRunExecution:
        request_hash = _request_hash(case_id, request)
        resumed = False
        with self._session_factory() as session:
            existing = self._find_existing(session, principal.tenant_id, idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflict(idempotency_key)
                if existing.final_result is not None:
                    return self._execution(session, existing, replayed=True, resumed=False)
                system_run_id = existing.id
                resumed = True
            else:
                SqlAlchemyCaseRepository(session).get(principal.tenant_id, case_id)
                system_run_id = new_id()
                plan = self._plan()
                now = datetime.now(UTC)
                session.add(
                    SystemRunRecord(
                        id=system_run_id,
                        tenant_id=principal.tenant_id,
                        case_id=case_id,
                        actor_id=principal.actor_id,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        goal=request.goal,
                        question=request.question,
                        as_of=request.as_of,
                        status=SystemRunStatus.CREATED.value,
                        plan=plan.model_dump(mode="json"),
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add_all(
                    [
                        SystemStepRecord(
                            system_run_id=system_run_id,
                            tenant_id=principal.tenant_id,
                            step_key=step.key,
                            owner=step.owner,
                            goal=step.goal,
                            depends_on=list(step.depends_on),
                            acceptance_criteria=list(step.acceptance_criteria),
                            status=SystemStepStatus.PLANNED.value,
                        )
                        for step in plan.steps
                    ]
                )
                self._emit(
                    session=session,
                    tenant_id=principal.tenant_id,
                    run_id=system_run_id,
                    case_id=case_id,
                    event_type="dev.caseops.system.started.v1",
                    data={"plan": plan.model_dump(mode="json")},
                )
                session.commit()

        try:
            self._set_run_status(system_run_id, SystemRunStatus.RUNNING)
            context = self._execute_context(
                system_run_id=system_run_id,
                principal=principal,
                case_id=case_id,
                request=request,
                request_id=request_id,
            )
            collaboration = await self._execute_collaboration(
                system_run_id=system_run_id,
                principal=principal,
                case_id=case_id,
                request=request,
                request_id=request_id,
            )
            self._set_run_status(system_run_id, SystemRunStatus.CONSOLIDATING)
            result = SystemAcceptance().evaluate(
                context=ContextInvestigationResult.model_validate(context.result),
                collaboration=CollaborationResult.model_validate(collaboration.result),
            )
            self._complete(
                system_run_id=system_run_id,
                principal=principal,
                case_id=case_id,
                request_id=request_id,
                result=result,
            )
        except Exception as error:
            self._fail_active_step(system_run_id, error)
            raise

        with self._session_factory() as session:
            row = self._require_run(session, principal.tenant_id, system_run_id)
            return self._execution(
                session,
                row,
                replayed=False,
                resumed=resumed,
            )

    def get_context_graph(
        self,
        *,
        principal: Principal,
        system_run_id: str,
    ) -> RuntimeContextGraph:
        with self._session_factory() as session:
            self._require_run(session, principal.tenant_id, system_run_id)
            nodes = session.scalars(
                select(RuntimeContextNodeRecord)
                .where(
                    RuntimeContextNodeRecord.tenant_id == principal.tenant_id,
                    RuntimeContextNodeRecord.system_run_id == system_run_id,
                )
                .order_by(RuntimeContextNodeRecord.node_key)
            ).all()
            edges = session.scalars(
                select(RuntimeContextEdgeRecord)
                .where(
                    RuntimeContextEdgeRecord.tenant_id == principal.tenant_id,
                    RuntimeContextEdgeRecord.system_run_id == system_run_id,
                )
                .order_by(RuntimeContextEdgeRecord.edge_key)
            ).all()
            return RuntimeContextGraph(
                system_run_id=system_run_id,
                nodes=tuple(
                    ContextGraphNode(
                        node_key=row.node_key,
                        node_type=row.node_type,
                        label=row.label,
                        owner=row.owner,
                        status=row.status,
                        ref=row.ref,
                        payload_digest=row.payload_digest,
                        classification=row.classification,
                    )
                    for row in nodes
                ),
                edges=tuple(
                    ContextGraphEdge(
                        edge_key=row.edge_key,
                        from_node_key=row.from_node_key,
                        to_node_key=row.to_node_key,
                        relation_type=row.relation_type,
                    )
                    for row in edges
                ),
            )

    def _execute_context(
        self,
        *,
        system_run_id: str,
        principal: Principal,
        case_id: str,
        request: SystemRunRequest,
        request_id: str,
    ) -> ContextExecution:
        existing_ref = self._start_step(system_run_id, "context-evidence")
        with self._session_factory() as session:
            execution = ContextInvestigationService(session).execute(
                principal=principal,
                case_id=case_id,
                request=ContextInvestigationRequest(
                    question=request.question,
                    purpose="claim_investigation",
                    as_of=request.as_of,
                    evidence_token_budget=request.evidence_token_budget,
                    max_rounds=request.max_rounds,
                ),
                idempotency_key=f"{system_run_id}:context",
                request_id=request_id,
            )
        self._finish_step(
            system_run_id,
            "context-evidence",
            result_ref=f"caseops://context-runs/{execution.run_id}",
            result_payload=execution.result,
            child_field="context_run_id",
            child_run_id=execution.run_id,
        )
        _ = existing_ref
        return execution

    async def _execute_collaboration(
        self,
        *,
        system_run_id: str,
        principal: Principal,
        case_id: str,
        request: SystemRunRequest,
        request_id: str,
    ) -> CollaborationExecution:
        self._start_step(system_run_id, "specialist-collaboration")
        with self._session_factory() as session:
            execution = await CollaborationService(
                session=session,
                session_factory=self._session_factory,
                settings=self._settings,
            ).execute(
                principal=principal,
                case_id=case_id,
                goal=request.goal,
                join_policy=request.join_policy,
                idempotency_key=f"{system_run_id}:collaboration",
                request_id=request_id,
            )
        self._finish_step(
            system_run_id,
            "specialist-collaboration",
            result_ref=f"caseops://collaboration-runs/{execution.run_id}",
            result_payload=execution.result,
            child_field="collaboration_run_id",
            child_run_id=execution.run_id,
        )
        return execution

    def _complete(
        self,
        *,
        system_run_id: str,
        principal: Principal,
        case_id: str,
        request_id: str,
        result: SystemRunResult,
    ) -> None:
        self._start_step(system_run_id, "system-acceptance")
        now = datetime.now(UTC)
        with self._session_factory() as session:
            row = self._require_run(session, principal.tenant_id, system_run_id)
            if row.context_run_id is None or row.collaboration_run_id is None:
                raise RuntimeError("system acceptance requires both child runs")
            tasks = session.scalars(
                select(DelegatedTaskRecord)
                .where(DelegatedTaskRecord.run_id == row.collaboration_run_id)
                .order_by(DelegatedTaskRecord.specialist_id)
            ).all()
            context_row = row.context_run_id
            collaboration_row = row.collaboration_run_id
            plan = SystemPlan.model_validate(row.plan)

        # Build from already validated child payloads loaded through their own records.
        with self._session_factory() as session:
            from caseops.infrastructure.models import (
                CollaborationRunRecord,
                ContextRunRecord,
            )

            context_record = session.get(ContextRunRecord, context_row)
            collaboration_record = session.get(CollaborationRunRecord, collaboration_row)
            if (
                context_record is None
                or collaboration_record is None
                or collaboration_record.final_result is None
            ):
                raise RuntimeError("child run result is unavailable")
            context = ContextInvestigationResult.model_validate(
                {
                    "case_id": context_record.case_id,
                    "context_pack": context_record.context_pack,
                    "answer": context_record.answer,
                    "trace": context_record.trace,
                }
            )
            collaboration = CollaborationResult.model_validate(
                collaboration_record.final_result
            )
            graph = RuntimeContextGraphBuilder().build(
                system_run_id=system_run_id,
                goal=row.goal,
                plan=plan,
                context_run_id=context_row,
                collaboration_run_id=collaboration_row,
                delegated_tasks=tuple(
                    {
                        "task_id": task.id,
                        "specialist_id": task.specialist_id,
                        "status": task.status,
                        "attempt_count": task.attempt_count,
                        "result_digest": digest(task.result or task.error or {}),
                    }
                    for task in tasks
                ),
                context=context,
                collaboration=collaboration,
                result=result,
            )
            session.add_all(
                [
                    RuntimeContextNodeRecord(
                        system_run_id=system_run_id,
                        tenant_id=principal.tenant_id,
                        **node.model_dump(),
                    )
                    for node in graph.nodes
                ]
            )
            session.add_all(
                [
                    RuntimeContextEdgeRecord(
                        system_run_id=system_run_id,
                        tenant_id=principal.tenant_id,
                        **edge.model_dump(),
                    )
                    for edge in graph.edges
                ]
            )
            row = self._require_run(session, principal.tenant_id, system_run_id)
            row.final_result = result.model_dump(mode="json")
            row.status = (
                SystemRunStatus.NEEDS_HUMAN.value
                if result.outcome == "SYSTEM_ACCEPTED_WITH_HUMAN_REVIEW"
                else (
                    SystemRunStatus.COMPLETED.value
                    if result.outcome == "SYSTEM_ACCEPTED"
                    else SystemRunStatus.FAILED.value
                )
            )
            row.version += 1
            row.completed_at = now
            row.updated_at = now
            step = self._require_step(session, system_run_id, "system-acceptance")
            step.status = SystemStepStatus.SUCCEEDED.value
            step.result_ref = f"caseops://system-runs/{system_run_id}/acceptance"
            step.result_digest = digest(result.model_dump(mode="json"))
            step.completed_at = now
            session.add(
                AuditEventRecord(
                    tenant_id=principal.tenant_id,
                    actor_id=principal.actor_id,
                    action="system.run",
                    subject_type="case",
                    subject_id=case_id,
                    request_id=request_id,
                    outcome=result.outcome,
                    details={
                        "system_run_id": system_run_id,
                        "context_run_id": context_row,
                        "collaboration_run_id": collaboration_row,
                        "failed_checks": [
                            check.check_id
                            for check in result.checks
                            if check.status == "failed"
                        ],
                        "context_graph_nodes": len(graph.nodes),
                        "context_graph_edges": len(graph.edges),
                        "side_effect": result.side_effect,
                    },
                )
            )
            self._emit(
                session=session,
                tenant_id=principal.tenant_id,
                run_id=system_run_id,
                case_id=case_id,
                event_type="dev.caseops.system.completed.v1",
                data={
                    "outcome": result.outcome,
                    "status": row.status,
                    "context_graph_nodes": len(graph.nodes),
                    "context_graph_edges": len(graph.edges),
                },
            )
            session.commit()

    def _start_step(self, system_run_id: str, step_key: str) -> str | None:
        with self._session_factory() as session:
            step = self._require_step(session, system_run_id, step_key)
            if step.status == SystemStepStatus.SUCCEEDED.value:
                return step.result_ref
            completed = {
                row.step_key
                for row in session.scalars(
                    select(SystemStepRecord).where(
                        SystemStepRecord.system_run_id == system_run_id,
                        SystemStepRecord.status == SystemStepStatus.SUCCEEDED.value,
                    )
                )
            }
            if not set(step.depends_on).issubset(completed):
                raise RuntimeError(f"step dependencies are incomplete: {step_key}")
            step.status = SystemStepStatus.RUNNING.value
            step.attempt_count += 1
            step.error = None
            step.started_at = datetime.now(UTC)
            session.commit()
            return None

    def _finish_step(
        self,
        system_run_id: str,
        step_key: str,
        *,
        result_ref: str,
        result_payload: object,
        child_field: str,
        child_run_id: str,
    ) -> None:
        with self._session_factory() as session:
            step = self._require_step(session, system_run_id, step_key)
            step.status = SystemStepStatus.SUCCEEDED.value
            step.result_ref = result_ref
            step.result_digest = digest(result_payload)
            step.completed_at = datetime.now(UTC)
            row = session.get(SystemRunRecord, system_run_id)
            if row is None:
                raise RuntimeError("system run disappeared")
            setattr(row, child_field, child_run_id)
            row.updated_at = datetime.now(UTC)
            row.version += 1
            session.commit()

    def _set_run_status(
        self,
        system_run_id: str,
        status: SystemRunStatus,
    ) -> None:
        with self._session_factory() as session:
            row = session.get(SystemRunRecord, system_run_id)
            if row is None:
                raise RuntimeError("system run disappeared")
            row.status = status.value
            row.version += 1
            row.updated_at = datetime.now(UTC)
            session.commit()

    def _fail_active_step(self, system_run_id: str, error: Exception) -> None:
        with self._session_factory() as session:
            active = session.scalar(
                select(SystemStepRecord).where(
                    SystemStepRecord.system_run_id == system_run_id,
                    SystemStepRecord.status == SystemStepStatus.RUNNING.value,
                )
            )
            if active is not None:
                active.status = SystemStepStatus.FAILED.value
                active.error = {
                    "code": type(error).__name__,
                    "message": str(error)[:500],
                }
                active.completed_at = datetime.now(UTC)
            row = session.get(SystemRunRecord, system_run_id)
            if row is not None:
                row.status = SystemRunStatus.FAILED.value
                row.updated_at = datetime.now(UTC)
                row.version += 1
            session.commit()

    @staticmethod
    def _plan() -> SystemPlan:
        return SystemPlan(
            steps=(
                SystemPlanStep(
                    key="context-evidence",
                    owner="context-team",
                    goal="构建受时态、权限、来源与预算约束的 Context Pack。",
                    acceptance_criteria=(
                        "必要主张全部有证据",
                        "不可信指令被隔离",
                        "答案保持只读",
                    ),
                ),
                SystemPlanStep(
                    key="specialist-collaboration",
                    owner="collaboration-team",
                    goal="通过 A2A 委托规则、材料与风险专业节点并确定性 Join。",
                    acceptance_criteria=(
                        "必要专业节点成功",
                        "每个专业主张有证据",
                        "冲突不会被自然语言抹平",
                    ),
                ),
                SystemPlanStep(
                    key="system-acceptance",
                    owner="central-supervisor",
                    goal="执行跨团队一致性、证据绑定与副作用验收。",
                    depends_on=("context-evidence", "specialist-collaboration"),
                    acceptance_criteria=(
                        "规则版本一致",
                        "材料状态一致",
                        "风险门禁一致",
                        "全部结论可追溯",
                    ),
                ),
            ),
            capability_snapshot={
                "a2a": {
                    "protocol_version": "1.0",
                    "binding": "HTTP+JSON",
                    "skills": [
                        "caseops_coverage",
                        "caseops_document",
                        "caseops_risk",
                    ],
                    "streaming": False,
                    "push_notifications": False,
                },
                "mcp": {
                    "transport": "streamable-http",
                    "tool_execution": "synchronous-read-only",
                    "task_support": False,
                    "reason": (
                        "current tools are bounded synchronous reads; "
                        "experimental durable MCP Tasks are not required"
                    ),
                },
            },
        )

    @staticmethod
    def _find_existing(
        session: Session,
        tenant_id: str,
        idempotency_key: str,
    ) -> SystemRunRecord | None:
        return session.scalar(
            select(SystemRunRecord).where(
                SystemRunRecord.tenant_id == tenant_id,
                SystemRunRecord.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _require_step(
        session: Session,
        system_run_id: str,
        step_key: str,
    ) -> SystemStepRecord:
        step = session.scalar(
            select(SystemStepRecord).where(
                SystemStepRecord.system_run_id == system_run_id,
                SystemStepRecord.step_key == step_key,
            )
        )
        if step is None:
            raise RuntimeError(f"system step is unavailable: {step_key}")
        return step

    @staticmethod
    def _require_run(
        session: Session,
        tenant_id: str,
        system_run_id: str,
    ) -> SystemRunRecord:
        row = session.scalar(
            select(SystemRunRecord).where(
                SystemRunRecord.id == system_run_id,
                SystemRunRecord.tenant_id == tenant_id,
            )
        )
        if row is None:
            raise SystemRunNotFound(system_run_id)
        return row

    def _execution(
        self,
        session: Session,
        row: SystemRunRecord,
        *,
        replayed: bool,
        resumed: bool,
    ) -> SystemRunExecution:
        if row.final_result is None:
            raise RuntimeError("system run has no final result")
        steps = session.scalars(
            select(SystemStepRecord)
            .where(SystemStepRecord.system_run_id == row.id)
            .order_by(SystemStepRecord.step_key)
        ).all()
        return SystemRunExecution(
            system_run_id=row.id,
            idempotency_key=row.idempotency_key,
            status=row.status,
            replayed=replayed,
            resumed=resumed,
            result=row.final_result,
            steps=tuple(
                SystemStepExecution(
                    step_key=step.step_key,
                    owner=step.owner,
                    status=step.status,
                    attempt_count=step.attempt_count,
                    depends_on=tuple(step.depends_on),
                    result_ref=step.result_ref,
                    result_digest=step.result_digest,
                    error=step.error,
                )
                for step in steps
            ),
            child_runs={
                "context_run_id": row.context_run_id or "",
                "collaboration_run_id": row.collaboration_run_id or "",
            },
            context_graph_uri=f"/v1/system-runs/{row.id}/context-graph",
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    @staticmethod
    def _emit(
        *,
        session: Session,
        tenant_id: str,
        run_id: str,
        case_id: str,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        event = CloudEvent(
            id=new_id(),
            source="/caseops/system-runs",
            type=event_type,
            subject=f"case/{case_id}",
            time=datetime.now(UTC),
            dataschema="https://caseops.dev/schemas/system-run-event.v1.json",
            correlationid=run_id,
            tenantid=tenant_id,
            data=data,
        )
        session.add(
            OutboxEventRecord(
                tenant_id=tenant_id,
                topic=event.type,
                aggregate_type="system_run",
                aggregate_id=run_id,
                payload=event.model_dump(mode="json"),
            )
        )
