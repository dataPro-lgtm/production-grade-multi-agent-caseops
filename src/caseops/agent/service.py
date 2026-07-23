from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from caseops.config import Settings
from caseops.errors import IdempotencyConflict
from caseops.infrastructure.models import (
    AgentCheckpointRecord,
    AgentRunRecord,
    AuditEventRecord,
    OutboxEventRecord,
    ToolExecutionRecord,
    new_id,
)
from caseops.infrastructure.repositories import SqlAlchemyCaseRepository
from caseops.service import Principal

from .contracts import AgentState, RunStatus, ToolCall, ToolDefinition
from .planner import ConformancePlanner, OpenAIResponsesPlanner, Planner
from .policy import ToolPolicy
from .runtime import AgentRuntime
from .tools import (
    TOOL_REGISTRY,
    DatabaseToolExecutor,
    ToolExecutor,
)


@dataclass(frozen=True, slots=True)
class AgentExecution:
    run_id: str
    idempotency_key: str
    status: str
    replayed: bool
    resumed: bool
    step_count: int
    result: dict[str, object] | None
    stop_reason: str | None
    created_at: datetime
    completed_at: datetime | None


def _request_hash(case_id: str, goal: str) -> str:
    canonical = json.dumps(
        {
            "schema_version": "caseops.agent-run.request.v1",
            "case_id": case_id,
            "goal": goal,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SqlRunRecorder:
    def __init__(self, session: Session, run_id: str, tenant_id: str) -> None:
        self._session = session
        self.run_id = run_id
        self._tenant_id = tenant_id

    def checkpoint(self, state: AgentState) -> None:
        row = self._require_run()
        payload = state.model_dump(mode="json")
        row.status = state.status.value
        row.step_count = state.step_count
        row.state = payload
        row.final_result = (
            state.final_answer.model_dump(mode="json")
            if state.final_answer is not None
            else None
        )
        row.stop_reason = state.stop_reason
        row.updated_at = datetime.now(UTC)
        if state.terminal:
            row.completed_at = datetime.now(UTC)
        self._session.add(
            AgentCheckpointRecord(
                run_id=self.run_id,
                tenant_id=self._tenant_id,
                sequence=state.sequence,
                state=payload,
            )
        )
        self._session.commit()

    def record_tool(
        self,
        *,
        call: ToolCall,
        definition: ToolDefinition | None,
        fingerprint: str,
        status: str,
        attempt_count: int,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        row = self._session.scalar(
            select(ToolExecutionRecord).where(
                ToolExecutionRecord.run_id == self.run_id,
                ToolExecutionRecord.tool_call_id == call.call_id,
            )
        )
        if row is None:
            row = ToolExecutionRecord(
                run_id=self.run_id,
                tenant_id=self._tenant_id,
                tool_call_id=call.call_id,
                tool_name=call.name,
                tool_version=definition.version if definition else "unknown",
                arguments=call.arguments,
                arguments_hash=call.arguments_hash(),
                action_fingerprint=fingerprint,
                status=status,
                attempt_count=attempt_count,
            )
            self._session.add(row)
        else:
            row.arguments = call.arguments
            row.arguments_hash = call.arguments_hash()
            row.status = status
            row.attempt_count = attempt_count
        row.result = result
        row.error = error
        if status in {"succeeded", "failed", "denied"}:
            row.completed_at = datetime.now(UTC)
        self._session.commit()

    def _require_run(self) -> AgentRunRecord:
        row = self._session.get(AgentRunRecord, self.run_id)
        if row is None:
            raise RuntimeError(f"agent run disappeared: {self.run_id}")
        return row


class AgentRunService:
    def __init__(
        self,
        *,
        session: Session,
        session_factory: sessionmaker[Session],
        settings: Settings,
        executor: ToolExecutor | None = None,
        planner: Planner | None = None,
    ) -> None:
        self._session = session
        self._session_factory = session_factory
        self._settings = settings
        self._executor = executor
        self._planner = planner

    async def execute(
        self,
        *,
        principal: Principal,
        case_id: str,
        goal: str,
        idempotency_key: str,
        request_id: str,
    ) -> AgentExecution:
        request_hash = _request_hash(case_id, goal)
        row = self._session.scalar(
            select(AgentRunRecord).where(
                AgentRunRecord.tenant_id == principal.tenant_id,
                AgentRunRecord.idempotency_key == idempotency_key,
            )
        )
        if row is not None:
            if row.request_hash != request_hash:
                raise IdempotencyConflict(idempotency_key)
            if row.status in {
                RunStatus.COMPLETED.value,
                RunStatus.NEEDS_HUMAN.value,
                RunStatus.FAILED.value,
                RunStatus.STOPPED.value,
            }:
                return self._execution(row, replayed=True, resumed=False)
            state = AgentState.model_validate(row.state)
            planner = self._planner or self._build_planner()
            recorder = SqlRunRecorder(
                self._session,
                row.id,
                principal.tenant_id,
            )
            runtime = AgentRuntime(
                planner=planner,
                executor=self._executor or self._build_executor(),
                policy=ToolPolicy(allowed_tools=frozenset(TOOL_REGISTRY)),
                recorder=recorder,
                principal=principal,
                timeout_ceiling_seconds=self._settings.agent_tool_timeout_seconds,
            )
            await runtime.run(state)
            resumed_row = self._session.get(AgentRunRecord, row.id)
            if resumed_row is None:
                raise RuntimeError("agent run missing after recovery")
            self._persist_terminal_events(resumed_row, principal, request_id)
            return self._execution(resumed_row, replayed=False, resumed=True)

        # Authorization and existence are checked before an Agent run is allocated.
        # The model never decides which tenant or case the request may access.
        SqlAlchemyCaseRepository(self._session).get(principal.tenant_id, case_id)

        planner = self._planner or self._build_planner()
        run_id = new_id()
        state = AgentState(
            run_id=run_id,
            tenant_id=principal.tenant_id,
            case_id=case_id,
            goal=goal,
            max_steps=self._settings.agent_max_steps,
            repeat_limit=self._settings.agent_repeat_limit,
        )
        row = AgentRunRecord(
            id=run_id,
            tenant_id=principal.tenant_id,
            case_id=case_id,
            actor_id=principal.actor_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            goal=goal,
            planner_kind=planner.kind,
            model=planner.model,
            status=RunStatus.CREATED.value,
            max_steps=state.max_steps,
            state=state.model_dump(mode="json"),
        )
        self._session.add(row)
        self._session.commit()

        recorder = SqlRunRecorder(self._session, run_id, principal.tenant_id)
        recorder.checkpoint(state)
        executor = self._executor or self._build_executor()
        runtime = AgentRuntime(
            planner=planner,
            executor=executor,
            policy=ToolPolicy(allowed_tools=frozenset(TOOL_REGISTRY)),
            recorder=recorder,
            principal=principal,
            timeout_ceiling_seconds=self._settings.agent_tool_timeout_seconds,
        )
        await runtime.run(state)

        row = self._session.get(AgentRunRecord, run_id)
        if row is None:
            raise RuntimeError("agent run missing after execution")
        self._persist_terminal_events(row, principal, request_id)
        return self._execution(row, replayed=False, resumed=False)

    def _build_planner(self) -> Planner:
        if self._settings.agent_planner == "conformance":
            return ConformancePlanner()
        api_key = self._settings.openai_api_key
        if not api_key:
            raise RuntimeError("OpenAI API key is required by planner configuration")
        return OpenAIResponsesPlanner(
            api_key=api_key,
            base_url=self._settings.openai_base_url,
            model=self._settings.openai_model,
            reasoning_effort=self._settings.openai_reasoning_effort,
        )

    def _build_executor(self) -> ToolExecutor:
        if self._settings.agent_tool_transport == "direct":
            return DatabaseToolExecutor(self._session_factory)
        from .mcp_client import MCPToolExecutor

        return MCPToolExecutor(settings=self._settings)

    def _persist_terminal_events(
        self,
        row: AgentRunRecord,
        principal: Principal,
        request_id: str,
    ) -> None:
        self._session.add_all(
            [
                AuditEventRecord(
                    tenant_id=principal.tenant_id,
                    actor_id=principal.actor_id,
                    action="agent.run",
                    subject_type="case",
                    subject_id=row.case_id,
                    request_id=request_id,
                    outcome=row.status,
                    details={
                        "run_id": row.id,
                        "planner_kind": row.planner_kind,
                        "model": row.model,
                        "step_count": row.step_count,
                        "stop_reason": row.stop_reason,
                    },
                ),
                OutboxEventRecord(
                    tenant_id=principal.tenant_id,
                    topic="case.agent-run.completed.v1",
                    aggregate_type="case",
                    aggregate_id=row.case_id,
                    payload={
                        "run_id": row.id,
                        "status": row.status,
                        "outcome": (
                            row.final_result.get("outcome")
                            if row.final_result is not None
                            else None
                        ),
                    },
                ),
            ]
        )
        self._session.commit()

    @staticmethod
    def _execution(
        row: AgentRunRecord,
        *,
        replayed: bool,
        resumed: bool,
    ) -> AgentExecution:
        return AgentExecution(
            run_id=row.id,
            idempotency_key=row.idempotency_key,
            status=row.status,
            replayed=replayed,
            resumed=resumed,
            step_count=row.step_count,
            result=row.final_result,
            stop_reason=row.stop_reason,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )
