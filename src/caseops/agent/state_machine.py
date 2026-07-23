from __future__ import annotations

from dataclasses import dataclass

from .contracts import AgentState, RunStatus


class IllegalTransition(ValueError):
    pass


LEGAL_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.PLANNING}),
    RunStatus.PLANNING: frozenset(
        {
            RunStatus.TOOL_PROPOSED,
            RunStatus.COMPLETED,
            RunStatus.NEEDS_HUMAN,
            RunStatus.STOPPED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.TOOL_PROPOSED: frozenset(
        {
            RunStatus.TOOL_AUTHORIZED,
            RunStatus.NEEDS_HUMAN,
            RunStatus.FAILED,
            RunStatus.STOPPED,
        }
    ),
    RunStatus.TOOL_AUTHORIZED: frozenset({RunStatus.TOOL_RUNNING, RunStatus.FAILED}),
    RunStatus.TOOL_RUNNING: frozenset(
        {RunStatus.OBSERVING, RunStatus.FAILED, RunStatus.NEEDS_HUMAN}
    ),
    RunStatus.OBSERVING: frozenset(
        {RunStatus.PLANNING, RunStatus.FAILED, RunStatus.STOPPED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.NEEDS_HUMAN: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.STOPPED: frozenset(),
}


@dataclass(slots=True)
class StateMachine:
    state: AgentState

    def transition(self, target: RunStatus) -> AgentState:
        if target not in LEGAL_TRANSITIONS[self.state.status]:
            raise IllegalTransition(
                f"illegal Agent transition: {self.state.status} -> {target}"
            )
        self.state.status = target
        self.state.sequence += 1
        return self.state
