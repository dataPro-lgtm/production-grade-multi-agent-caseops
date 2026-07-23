from __future__ import annotations

import unittest
from typing import Any

from caseops.agent.contracts import (
    AgentState,
    FinalAnswer,
    PlannerDecision,
    RunStatus,
    ToolCall,
    ToolDefinition,
)
from caseops.agent.policy import ToolPolicy
from caseops.agent.runtime import AgentRuntime
from caseops.agent.state_machine import IllegalTransition, StateMachine
from caseops.agent.tools import GET_CASE, ToolExecutionError, ToolExecutor
from caseops.service import Principal


class MemoryRecorder:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.checkpoints: list[dict[str, Any]] = []
        self.tool_events: list[dict[str, Any]] = []

    def checkpoint(self, state: AgentState) -> None:
        self.checkpoints.append(state.model_dump(mode="json"))

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
        self.tool_events.append(
            {
                "call_id": call.call_id,
                "tool": call.name,
                "definition": definition.name if definition else None,
                "fingerprint": fingerprint,
                "status": status,
                "attempt_count": attempt_count,
                "result": result,
                "error": error,
            }
        )


class RepeatingPlanner:
    @property
    def kind(self) -> str:
        return "test-repeat"

    @property
    def model(self) -> str | None:
        return None

    async def decide(
        self,
        state: AgentState,
        tools: tuple[ToolDefinition, ...],
    ) -> PlannerDecision:
        del tools
        return PlannerDecision(
            kind="tool_call",
            tool_call=ToolCall(
                call_id=f"call_repeat_{state.step_count:02d}",
                name=GET_CASE,
                arguments={"case_id": state.case_id},
            ),
        )


class CountingExecutor(ToolExecutor):
    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        *,
        principal: Principal,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        del principal, run_id, tool_name, arguments
        self.calls += 1
        return {"case_id": "C-102", "evidence_ref": "case://C-102@7"}


class OneToolThenFinalPlanner:
    @property
    def kind(self) -> str:
        return "test-one-tool"

    @property
    def model(self) -> str | None:
        return None

    async def decide(
        self,
        state: AgentState,
        tools: tuple[ToolDefinition, ...],
    ) -> PlannerDecision:
        del tools
        if not state.observations:
            return PlannerDecision(
                kind="tool_call",
                tool_call=ToolCall(
                    call_id="call_retry_once",
                    name=GET_CASE,
                    arguments={"case_id": state.case_id},
                ),
            )
        return PlannerDecision(
            kind="final",
            final_answer=FinalAnswer(
                outcome="DOCUMENTS_COMPLETE",
                summary="retry succeeded and evidence is sufficient",
                received_document_codes=[],
                resolved_document_codes=[],
                missing_document_codes=[],
                evidence_refs=["case://C-102@7"],
            ),
        )


class TransientOnceExecutor(CountingExecutor):
    async def execute(
        self,
        *,
        principal: Principal,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            raise ToolExecutionError(
                "TEMPORARY_UPSTREAM_FAILURE",
                "retryable test failure",
                transient=True,
            )
        del principal, run_id, tool_name, arguments
        return {"case_id": "C-102", "evidence_ref": "case://C-102@7"}


class AgentRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def test_illegal_state_transition_is_rejected(self) -> None:
        state = AgentState(
            run_id="run-1",
            tenant_id="tenant-demo",
            case_id="C-102",
            goal="test controlled state transitions",
            max_steps=4,
            repeat_limit=1,
        )

        with self.assertRaises(IllegalTransition):
            StateMachine(state).transition(RunStatus.COMPLETED)

    async def test_repeated_action_fingerprint_stops_the_loop(self) -> None:
        state = AgentState(
            run_id="run-repeat",
            tenant_id="tenant-demo",
            case_id="C-102",
            goal="test repeated action fingerprint",
            max_steps=5,
            repeat_limit=1,
        )
        recorder = MemoryRecorder(state.run_id)
        executor = CountingExecutor()
        runtime = AgentRuntime(
            planner=RepeatingPlanner(),
            executor=executor,
            policy=ToolPolicy(allowed_tools=frozenset({GET_CASE})),
            recorder=recorder,
            principal=Principal("tenant-demo", "test"),
            timeout_ceiling_seconds=1,
        )

        result = await runtime.run(state)

        self.assertEqual(result.status, RunStatus.STOPPED)
        self.assertEqual(result.stop_reason, "REPEATED_ACTION_FINGERPRINT")
        self.assertEqual(executor.calls, 1)
        self.assertEqual(recorder.tool_events[-1]["status"], "denied")

    async def test_model_cannot_change_case_boundary_in_tool_arguments(self) -> None:
        class BoundaryEscapePlanner(RepeatingPlanner):
            async def decide(
                self,
                state: AgentState,
                tools: tuple[ToolDefinition, ...],
            ) -> PlannerDecision:
                del state, tools
                return PlannerDecision(
                    kind="tool_call",
                    tool_call=ToolCall(
                        call_id="call_boundary_escape",
                        name=GET_CASE,
                        arguments={"case_id": "C-999"},
                    ),
                )

        state = AgentState(
            run_id="run-boundary",
            tenant_id="tenant-demo",
            case_id="C-102",
            goal="test resource boundary authorization",
            max_steps=3,
            repeat_limit=1,
        )
        recorder = MemoryRecorder(state.run_id)
        executor = CountingExecutor()
        runtime = AgentRuntime(
            planner=BoundaryEscapePlanner(),
            executor=executor,
            policy=ToolPolicy(allowed_tools=frozenset({GET_CASE})),
            recorder=recorder,
            principal=Principal("tenant-demo", "test"),
            timeout_ceiling_seconds=1,
        )

        result = await runtime.run(state)

        self.assertEqual(result.status, RunStatus.NEEDS_HUMAN)
        self.assertEqual(result.stop_reason, "TOOL_RESOURCE_MISMATCH")
        self.assertEqual(executor.calls, 0)

    async def test_transient_read_failure_retries_within_tool_budget(self) -> None:
        state = AgentState(
            run_id="run-retry",
            tenant_id="tenant-demo",
            case_id="C-102",
            goal="test bounded transient retry",
            max_steps=3,
            repeat_limit=1,
        )
        recorder = MemoryRecorder(state.run_id)
        executor = TransientOnceExecutor()
        runtime = AgentRuntime(
            planner=OneToolThenFinalPlanner(),
            executor=executor,
            policy=ToolPolicy(allowed_tools=frozenset({GET_CASE})),
            recorder=recorder,
            principal=Principal("tenant-demo", "test"),
            timeout_ceiling_seconds=1,
        )

        result = await runtime.run(state)

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.observations[0].attempt_count, 2)
        self.assertEqual(executor.calls, 2)

    async def test_step_budget_stops_before_an_unbounded_second_call(self) -> None:
        state = AgentState(
            run_id="run-budget",
            tenant_id="tenant-demo",
            case_id="C-102",
            goal="test hard step budget",
            max_steps=1,
            repeat_limit=2,
        )
        recorder = MemoryRecorder(state.run_id)
        executor = CountingExecutor()
        runtime = AgentRuntime(
            planner=RepeatingPlanner(),
            executor=executor,
            policy=ToolPolicy(allowed_tools=frozenset({GET_CASE})),
            recorder=recorder,
            principal=Principal("tenant-demo", "test"),
            timeout_ceiling_seconds=1,
        )

        result = await runtime.run(state)

        self.assertEqual(result.status, RunStatus.STOPPED)
        self.assertEqual(result.stop_reason, "STEP_BUDGET_EXHAUSTED")
        self.assertEqual(executor.calls, 1)

    async def test_read_only_tool_run_recovers_from_running_checkpoint(self) -> None:
        state = AgentState(
            run_id="run-recovery",
            tenant_id="tenant-demo",
            case_id="C-102",
            goal="test recovery from a read-only tool crash window",
            status=RunStatus.TOOL_RUNNING,
            sequence=3,
            step_count=1,
            max_steps=4,
            repeat_limit=1,
            pending_call=ToolCall(
                call_id="call_before_crash",
                name=GET_CASE,
                arguments={"case_id": "C-102"},
            ),
        )
        recorder = MemoryRecorder(state.run_id)
        executor = CountingExecutor()
        runtime = AgentRuntime(
            planner=OneToolThenFinalPlanner(),
            executor=executor,
            policy=ToolPolicy(allowed_tools=frozenset({GET_CASE})),
            recorder=recorder,
            principal=Principal("tenant-demo", "test"),
            timeout_ceiling_seconds=1,
        )

        result = await runtime.run(state)

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.recovery_count, 1)
        self.assertIsNone(result.pending_call)
        self.assertEqual(executor.calls, 1)


if __name__ == "__main__":
    unittest.main()
