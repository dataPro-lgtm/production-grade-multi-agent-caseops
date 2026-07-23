from __future__ import annotations

import asyncio
from typing import Any, Protocol

from pydantic import ValidationError

from caseops.service import Principal

from .contracts import (
    AgentState,
    RunStatus,
    ToolCall,
    ToolDefinition,
    ToolObservation,
    ToolRisk,
)
from .planner import Planner, PlannerError
from .policy import ToolAuthorizationDenied, ToolPolicy
from .state_machine import StateMachine
from .tools import (
    TOOL_DEFINITIONS,
    TOOL_REGISTRY,
    ToolExecutionError,
    ToolExecutor,
    validate_arguments,
)


class RunRecorder(Protocol):
    def checkpoint(self, state: AgentState) -> None:
        """Durably store the current control state."""

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
        """Upsert one tool execution ledger entry."""


class AgentRuntime:
    def __init__(
        self,
        *,
        planner: Planner,
        executor: ToolExecutor,
        policy: ToolPolicy,
        recorder: RunRecorder,
        principal: Principal,
        timeout_ceiling_seconds: float,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._policy = policy
        self._recorder = recorder
        self._principal = principal
        self._timeout_ceiling_seconds = timeout_ceiling_seconds

    async def run(self, state: AgentState) -> AgentState:
        machine = StateMachine(state)
        if state.status is RunStatus.CREATED or state.status is RunStatus.OBSERVING:
            self._transition(machine, RunStatus.PLANNING)
        elif state.status in {
            RunStatus.TOOL_PROPOSED,
            RunStatus.TOOL_AUTHORIZED,
            RunStatus.TOOL_RUNNING,
        }:
            definition = (
                TOOL_REGISTRY.get(state.pending_call.name)
                if state.pending_call is not None
                else None
            )
            if definition is None or definition.risk is not ToolRisk.READ_ONLY:
                state.stop_reason = "UNSAFE_RESUME_POINT"
                self._force_terminal(machine, RunStatus.NEEDS_HUMAN)
                return state
            # A read may be repeated safely. We discard the incomplete proposal and
            # ask the planner again from the last durable observations.
            state.pending_call = None
            state.recovery_count += 1
            state.status = RunStatus.PLANNING
            state.sequence += 1
            self._recorder.checkpoint(state)
        elif state.status is not RunStatus.PLANNING:
            state.stop_reason = "UNSAFE_RESUME_POINT"
            self._force_terminal(machine, RunStatus.NEEDS_HUMAN)
            return state

        while not state.terminal:
            if state.step_count >= state.max_steps:
                state.stop_reason = "STEP_BUDGET_EXHAUSTED"
                self._transition(machine, RunStatus.STOPPED)
                break
            try:
                decision = await self._planner.decide(state, TOOL_DEFINITIONS)
            except PlannerError as error:
                state.stop_reason = f"PLANNER_ERROR:{error}"
                self._transition(machine, RunStatus.FAILED)
                break

            if decision.kind == "final":
                state.final_answer = decision.final_answer
                self._transition(machine, RunStatus.COMPLETED)
                break
            if decision.kind == "needs_human":
                state.stop_reason = decision.reason
                self._transition(machine, RunStatus.NEEDS_HUMAN)
                break

            call = decision.tool_call
            if call is None:  # protected by contract validation
                state.stop_reason = "PLANNER_CONTRACT_VIOLATION"
                self._transition(machine, RunStatus.FAILED)
                break
            state.pending_call = call
            state.step_count += 1
            self._transition(machine, RunStatus.TOOL_PROPOSED)

            definition = TOOL_REGISTRY.get(call.name)
            fingerprint = call.fingerprint()
            self._recorder.record_tool(
                call=call,
                definition=definition,
                fingerprint=fingerprint,
                status="proposed",
                attempt_count=0,
            )
            if definition is None:
                state.stop_reason = "UNKNOWN_TOOL"
                self._transition(machine, RunStatus.NEEDS_HUMAN)
                break
            try:
                call.arguments = validate_arguments(call.name, call.arguments)
                self._policy.authorize(
                    principal=self._principal,
                    definition=definition,
                    call=call,
                    case_id=state.case_id,
                )
            except (ValidationError, ToolExecutionError) as error:
                self._deny(
                    machine,
                    call,
                    definition,
                    fingerprint,
                    "INVALID_ARGUMENTS",
                    error,
                )
                break
            except ToolAuthorizationDenied as error:
                self._deny(machine, call, definition, fingerprint, error.code, error)
                break

            count = state.fingerprint_counts.get(fingerprint, 0) + 1
            state.fingerprint_counts[fingerprint] = count
            if count > state.repeat_limit:
                state.stop_reason = "REPEATED_ACTION_FINGERPRINT"
                self._recorder.record_tool(
                    call=call,
                    definition=definition,
                    fingerprint=fingerprint,
                    status="denied",
                    attempt_count=0,
                    error={
                        "code": "REPEATED_ACTION_FINGERPRINT",
                        "message": "same action exceeded repeat limit",
                    },
                )
                self._transition(machine, RunStatus.STOPPED)
                break

            self._recorder.record_tool(
                call=call,
                definition=definition,
                fingerprint=fingerprint,
                status="authorized",
                attempt_count=0,
            )
            self._transition(machine, RunStatus.TOOL_AUTHORIZED)
            self._transition(machine, RunStatus.TOOL_RUNNING)
            observation = await self._execute(call, definition, fingerprint)
            state.observations.append(observation)
            state.pending_call = None
            self._transition(machine, RunStatus.OBSERVING)
            if not observation.ok:
                state.stop_reason = observation.error_code
                self._transition(machine, RunStatus.FAILED)
                break
            self._transition(machine, RunStatus.PLANNING)
        return state

    async def _execute(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        fingerprint: str,
    ) -> ToolObservation:
        timeout = min(definition.timeout_seconds, self._timeout_ceiling_seconds)
        last_error: ToolExecutionError | None = None
        for attempt in range(1, definition.max_attempts + 1):
            self._recorder.record_tool(
                call=call,
                definition=definition,
                fingerprint=fingerprint,
                status="running",
                attempt_count=attempt,
            )
            try:
                result = await asyncio.wait_for(
                    self._executor.execute(
                        principal=self._principal,
                        run_id=self._recorder_run_id,
                        tool_name=call.name,
                        arguments=call.arguments,
                    ),
                    timeout=timeout,
                )
            except TimeoutError:
                last_error = ToolExecutionError(
                    "TOOL_TIMEOUT",
                    f"tool exceeded {timeout:g}s deadline",
                    transient=True,
                )
            except ToolExecutionError as error:
                last_error = error
            except Exception as error:
                last_error = ToolExecutionError(
                    "TOOL_UNEXPECTED_ERROR",
                    str(error),
                    transient=False,
                )
            else:
                self._recorder.record_tool(
                    call=call,
                    definition=definition,
                    fingerprint=fingerprint,
                    status="succeeded",
                    attempt_count=attempt,
                    result=result,
                )
                return ToolObservation(
                    call_id=call.call_id,
                    tool_name=call.name,
                    fingerprint=fingerprint,
                    ok=True,
                    result=result,
                    attempt_count=attempt,
                )
            if not last_error.transient:
                break

        if last_error is None:
            raise RuntimeError("tool execution ended without result or classified error")
        self._recorder.record_tool(
            call=call,
            definition=definition,
            fingerprint=fingerprint,
            status="failed",
            attempt_count=attempt,
            error={"code": last_error.code, "message": str(last_error)},
        )
        return ToolObservation(
            call_id=call.call_id,
            tool_name=call.name,
            fingerprint=fingerprint,
            ok=False,
            error_code=last_error.code,
            error_message=str(last_error),
            attempt_count=attempt,
        )

    @property
    def _recorder_run_id(self) -> str:
        run_id = getattr(self._recorder, "run_id", None)
        if not isinstance(run_id, str):
            raise RuntimeError("run recorder must expose run_id")
        return run_id

    def _deny(
        self,
        machine: StateMachine,
        call: ToolCall,
        definition: ToolDefinition,
        fingerprint: str,
        code: str,
        error: Exception,
    ) -> None:
        machine.state.stop_reason = code
        self._recorder.record_tool(
            call=call,
            definition=definition,
            fingerprint=fingerprint,
            status="denied",
            attempt_count=0,
            error={"code": code, "message": str(error)},
        )
        self._transition(machine, RunStatus.NEEDS_HUMAN)

    def _transition(self, machine: StateMachine, target: RunStatus) -> None:
        self._recorder.checkpoint(machine.transition(target))

    def _force_terminal(self, machine: StateMachine, target: RunStatus) -> None:
        machine.state.status = target
        machine.state.sequence += 1
        self._recorder.checkpoint(machine.state)
