from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import uuid4

import httpx

from .contracts import (
    AgentState,
    FinalAnswer,
    PlannerDecision,
    ToolCall,
    ToolDefinition,
)
from .tools import GET_CASE, GET_POLICY, LIST_DOCUMENTS, RESOLVE_ALIAS


class PlannerError(RuntimeError):
    pass


class Planner(Protocol):
    @property
    def kind(self) -> str:
        """Stable planner implementation identifier."""

    @property
    def model(self) -> str | None:
        """Model identifier, or None for a non-model conformance driver."""

    async def decide(
        self,
        state: AgentState,
        tools: tuple[ToolDefinition, ...],
    ) -> PlannerDecision:
        """Propose one tool call or a terminal answer."""


class ConformancePlanner:
    """Deterministic control-plane driver; deliberately not called an Agent."""

    @property
    def kind(self) -> str:
        return "conformance"

    @property
    def model(self) -> str | None:
        return None

    async def decide(
        self,
        state: AgentState,
        tools: tuple[ToolDefinition, ...],
    ) -> PlannerDecision:
        del tools
        by_name = {observation.tool_name: observation for observation in state.observations}
        if GET_CASE not in by_name:
            return _call(GET_CASE, {"case_id": state.case_id})
        if GET_POLICY not in by_name:
            return _call(GET_POLICY, {"case_id": state.case_id})
        if LIST_DOCUMENTS not in by_name:
            return _call(LIST_DOCUMENTS, {"case_id": state.case_id})

        documents_result = by_name[LIST_DOCUMENTS].result or {}
        documents = documents_result.get("documents", [])
        resolved_observations = [
            observation
            for observation in state.observations
            if observation.tool_name == RESOLVE_ALIAS
        ]
        resolved_ids = {
            str((observation.result or {}).get("document_id"))
            for observation in resolved_observations
        }
        if isinstance(documents, list):
            for document in documents:
                if not isinstance(document, dict):
                    continue
                document_id = str(document.get("document_id", ""))
                if document_id and document_id not in resolved_ids:
                    return _call(
                        RESOLVE_ALIAS,
                        {"case_id": state.case_id, "document_id": document_id},
                    )

        case_result = by_name[GET_CASE].result or {}
        policy_result = by_name[GET_POLICY].result or {}
        received = {str(code) for code in case_result.get("received_document_codes", [])}
        resolved = {
            str((observation.result or {}).get("canonical_code"))
            for observation in resolved_observations
            if (observation.result or {}).get("resolved") is True
        }
        required_documents = policy_result.get("required_documents", [])
        required = {
            str(document["code"])
            for document in required_documents
            if isinstance(document, dict) and "code" in document
        }
        missing = sorted(required - received - resolved)
        evidence_refs = sorted(
            {
                str(result["evidence_ref"])
                for result in (
                    observation.result or {} for observation in state.observations
                )
                if "evidence_ref" in result
            }
        )
        if missing:
            outcome = "MISSING_REQUIRED_DOCUMENTS"
            summary = "结构化材料与受治理的别名解析后，仍有必要材料缺失。"
        elif resolved:
            outcome = "DOCUMENTS_COMPLETE_AFTER_NORMALIZATION"
            summary = "未结构化材料经版本化别名规则归一后，案件材料已齐备。"
        else:
            outcome = "DOCUMENTS_COMPLETE"
            summary = "案件的结构化材料已覆盖当前规则要求。"
        return PlannerDecision(
            kind="final",
            final_answer=FinalAnswer(
                outcome=outcome,
                summary=summary,
                received_document_codes=sorted(received),
                resolved_document_codes=sorted(resolved),
                missing_document_codes=missing,
                evidence_refs=evidence_refs,
            ),
        )


def _call(name: str, arguments: dict[str, Any]) -> PlannerDecision:
    return PlannerDecision(
        kind="tool_call",
        tool_call=ToolCall(
            call_id=f"call_{uuid4().hex}",
            name=name,
            arguments=arguments,
        ),
    )


class OpenAIResponsesPlanner:
    """Responses API adapter. Runtime authorization remains outside the model."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @property
    def kind(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    async def decide(
        self,
        state: AgentState,
        tools: tuple[ToolDefinition, ...],
    ) -> PlannerDecision:
        body = {
            "model": self._model,
            "store": False,
            "parallel_tool_calls": False,
            "reasoning": {"effort": self._reasoning_effort},
            "instructions": (
                "You are the CaseOps investigation planner. Propose exactly one "
                "available read-only tool at a time. Never invent evidence, tenant "
                "identity, policy data, or tool results. When evidence is sufficient, "
                "return the final answer schema. If safe progress is impossible, "
                "return INSUFFICIENT_EVIDENCE."
            ),
            "input": json.dumps(
                {
                    "goal": state.goal,
                    "case_id": state.case_id,
                    "step_count": state.step_count,
                    "max_steps": state.max_steps,
                    "observations": [
                        observation.model_dump(mode="json")
                        for observation in state.observations
                    ],
                },
                ensure_ascii=False,
            ),
            "tools": [tool.as_openai_tool() for tool in tools],
            "tool_choice": "auto",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "caseops_final_answer",
                    "strict": True,
                    "schema": FinalAnswer.model_json_schema(),
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/responses",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise PlannerError(f"Responses API request failed: {error}") from error

        payload = response.json()
        for item in payload.get("output", []):
            if item.get("type") == "function_call":
                try:
                    arguments = json.loads(item["arguments"])
                    return PlannerDecision(
                        kind="tool_call",
                        tool_call=ToolCall(
                            call_id=item["call_id"],
                            name=item["name"],
                            arguments=arguments,
                        ),
                    )
                except (KeyError, TypeError, json.JSONDecodeError) as error:
                    raise PlannerError("invalid function_call payload") from error

        output_text = payload.get("output_text")
        if not isinstance(output_text, str):
            output_text = _extract_output_text(payload)
        try:
            final = FinalAnswer.model_validate_json(output_text)
        except (TypeError, ValueError) as error:
            raise PlannerError("model did not return a valid terminal answer") from error
        return PlannerDecision(kind="final", final_answer=final)


def _extract_output_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(str(content.get("text", "")))
    if not chunks:
        raise PlannerError("Responses API returned neither a tool call nor text")
    return "".join(chunks)
