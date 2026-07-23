from __future__ import annotations

import json
import unittest

import httpx

from caseops.agent.contracts import AgentState
from caseops.agent.planner import OpenAIResponsesPlanner, PlannerError
from caseops.agent.tools import GET_CASE, TOOL_DEFINITIONS


def _state() -> AgentState:
    return AgentState(
        run_id="run-openai-adapter",
        tenant_id="tenant-demo",
        case_id="C-102",
        goal="判断案件材料是否满足其绑定规则，并给出可追溯结论。",
        max_steps=8,
        repeat_limit=2,
    )


class OpenAIPlannerAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_function_call_is_parsed_as_proposal(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(body["model"], "gpt-5.6-terra")
            self.assertFalse(body["parallel_tool_calls"])
            self.assertFalse(body["store"])
            self.assertNotIn("tenant_id", body["tools"][0]["parameters"]["properties"])
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_openai_001",
                            "name": GET_CASE,
                            "arguments": '{"case_id":"C-102"}',
                        }
                    ]
                },
            )

        planner = OpenAIResponsesPlanner(
            api_key="test-only",
            base_url="https://api.openai.test/v1",
            model="gpt-5.6-terra",
            reasoning_effort="low",
            transport=httpx.MockTransport(handler),
        )

        decision = await planner.decide(_state(), TOOL_DEFINITIONS)

        self.assertEqual(decision.kind, "tool_call")
        self.assertEqual(decision.tool_call.name, GET_CASE)
        self.assertEqual(decision.tool_call.arguments, {"case_id": "C-102"})

    async def test_structured_terminal_answer_is_parsed(self) -> None:
        final_answer = {
            "schema_version": "caseops.agent-result.v1",
            "outcome": "DOCUMENTS_COMPLETE",
            "summary": "结构化材料已经覆盖规则要求。",
            "received_document_codes": ["ACCIDENT_CERTIFICATE"],
            "resolved_document_codes": [],
            "missing_document_codes": [],
            "evidence_refs": ["case://C-102@7"],
        }

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        final_answer,
                                        ensure_ascii=False,
                                    ),
                                }
                            ],
                        }
                    ]
                },
            )

        planner = OpenAIResponsesPlanner(
            api_key="test-only",
            base_url="https://api.openai.test/v1",
            model="gpt-5.6-terra",
            reasoning_effort="low",
            transport=httpx.MockTransport(handler),
        )

        decision = await planner.decide(_state(), TOOL_DEFINITIONS)

        self.assertEqual(decision.kind, "final")
        self.assertEqual(decision.final_answer.outcome, "DOCUMENTS_COMPLETE")

    async def test_http_failure_is_classified_as_planner_error(self) -> None:
        planner = OpenAIResponsesPlanner(
            api_key="test-only",
            base_url="https://api.openai.test/v1",
            model="gpt-5.6-terra",
            reasoning_effort="low",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(503, text="unavailable")
            ),
        )

        with self.assertRaises(PlannerError):
            await planner.decide(_state(), TOOL_DEFINITIONS)


if __name__ == "__main__":
    unittest.main()
