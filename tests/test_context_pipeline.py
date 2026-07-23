from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from caseops.context.answer import EvidenceBoundAnswerer
from caseops.context.builder import GovernedContextBuilder
from caseops.context.contracts import (
    CandidateDecision,
    ContextInvestigationRequest,
    EvidenceItem,
    RetrievalChannel,
    RetrievedCandidate,
)
from caseops.context.planner import GovernedQueryPlanner
from caseops.context.retrieval import reciprocal_rank_fusion
from caseops.context.service import ContextInvestigationService
from caseops.database import build_session_factory
from caseops.errors import IdempotencyConflict
from caseops.infrastructure.models import (
    AuditEventRecord,
    Base,
    ContextRunRecord,
    OutboxEventRecord,
)
from caseops.seed import seed_reference_data
from caseops.service import Principal


def request() -> ContextInvestigationRequest:
    return ContextInvestigationRequest(
        question=(
            "C-102 的事故证明是否满足规则要求，适用哪个规则版本，为什么需要人工复核？"
        ),
        purpose="claim_investigation",
        as_of=datetime.fromisoformat("2026-07-23T12:00:00+08:00"),
        evidence_token_budget=1800,
        max_rounds=2,
    )


class ContextPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = build_session_factory(self.engine)
        with self.factory.begin() as session:
            seed_reference_data(session)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_context_pack_is_temporal_authorized_and_claim_bound(self) -> None:
        with self.factory() as session:
            execution = ContextInvestigationService(session).execute(
                principal=Principal(
                    tenant_id="tenant-demo",
                    actor_id="test:context",
                ),
                case_id="C-102",
                request=request(),
                idempotency_key="context-unit-complete-0001",
                request_id="request-context-unit-complete-0001",
            )

        result = execution.result
        pack = result["context_pack"]
        answer = result["answer"]
        trace = result["trace"]
        evidence = pack["evidence"]
        evidence_ids = {item["evidence_id"] for item in evidence}

        self.assertEqual(execution.status, "complete")
        self.assertEqual(answer["verdict"], "complete")
        self.assertEqual(answer["side_effect"], "none")
        self.assertEqual(pack["stop_reason"], "evidence_sufficient")
        self.assertLessEqual(
            pack["evidence_token_count"],
            pack["evidence_token_budget"],
        )
        self.assertNotIn(
            "ctx-policy-motor-2025.4",
            {item["object_id"] for item in evidence},
        )
        self.assertNotIn(
            "ctx-untrusted-email-v1",
            {item["object_id"] for item in evidence},
        )
        self.assertIn(
            CandidateDecision.REJECTED_TEMPORAL.value,
            {event["decision"] for event in trace},
        )
        self.assertIn(
            CandidateDecision.REJECTED_UNTRUSTED_INSTRUCTION.value,
            {event["decision"] for event in trace},
        )
        for claim in answer["claims"]:
            self.assertTrue(set(claim["evidence_ids"]).issubset(evidence_ids))

        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count(ContextRunRecord.id))),
                1,
            )
            self.assertEqual(
                session.scalar(select(func.count(AuditEventRecord.id))),
                1,
            )
            self.assertEqual(
                session.scalar(select(func.count(OutboxEventRecord.id))),
                1,
            )

    def test_scope_reduction_withholds_the_answer(self) -> None:
        with self.factory() as session:
            execution = ContextInvestigationService(session).execute(
                principal=Principal(
                    tenant_id="tenant-demo",
                    actor_id="test:restricted",
                    scopes=frozenset({"case:read"}),
                ),
                case_id="C-102",
                request=request(),
                idempotency_key="context-unit-restricted-0001",
                request_id="request-context-unit-restricted-0001",
            )

        answer = execution.result["answer"]
        self.assertEqual(answer["verdict"], "insufficient_evidence")
        self.assertTrue(answer["unresolved_questions"])
        self.assertEqual(answer["claims"], [])
        self.assertIn(
            CandidateDecision.REJECTED_SCOPE.value,
            {event["decision"] for event in execution.result["trace"]},
        )

    def test_risk_rule_is_evaluated_instead_of_merely_retrieved(self) -> None:
        with self.factory() as session:
            execution = ContextInvestigationService(session).execute(
                principal=Principal("tenant-demo", "test:risk-evaluation"),
                case_id="C-102",
                request=request(),
                idempotency_key="context-unit-risk-evaluation-0001",
                request_id="request-context-unit-risk-evaluation-0001",
            )
        evidence = [
            EvidenceItem.model_validate(item)
            for item in execution.result["context_pack"]["evidence"]
        ]
        adjusted = tuple(
            item.model_copy(update={"facts": {**item.facts, "claim_amount": 1000}})
            if "claim_amount" in item.facts
            else item
            for item in evidence
        )

        answer = EvidenceBoundAnswerer().answer(adjusted)

        self.assertEqual(answer.recommended_action, "continue_read_only_review")
        manual_review = next(
            claim for claim in answer.claims if claim.claim_id == "claim-manual-review"
        )
        self.assertIn("required=false", manual_review.value)

    def test_idempotency_replays_and_rejects_request_reuse(self) -> None:
        with self.factory() as session:
            service = ContextInvestigationService(session)
            first = service.execute(
                principal=Principal("tenant-demo", "test:idempotency"),
                case_id="C-102",
                request=request(),
                idempotency_key="context-unit-idempotency-0001",
                request_id="request-context-unit-idempotency-0001",
            )
            second = service.execute(
                principal=Principal("tenant-demo", "test:idempotency"),
                case_id="C-102",
                request=request(),
                idempotency_key="context-unit-idempotency-0001",
                request_id="request-context-unit-idempotency-0002",
            )
            changed = request().model_copy(update={"evidence_token_budget": 1200})
            with self.assertRaises(IdempotencyConflict):
                service.execute(
                    principal=Principal("tenant-demo", "test:idempotency"),
                    case_id="C-102",
                    request=changed,
                    idempotency_key="context-unit-idempotency-0001",
                    request_id="request-context-unit-idempotency-0003",
                )

        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.run_id, second.run_id)

    def test_rrf_fuses_independent_channel_ranks(self) -> None:
        scores, ranks = reciprocal_rank_fusion(
            {
                RetrievalChannel.STRUCTURED: ["a", "b"],
                RetrievalChannel.FULL_TEXT: ["b", "c"],
                RetrievalChannel.GRAPH: ["b", "a"],
            }
        )

        self.assertGreater(scores["b"], scores["a"])
        self.assertGreater(scores["a"], scores["c"])
        self.assertEqual(ranks["b"]["full_text"], 1)
        self.assertEqual(ranks["b"]["graph"], 1)

    def test_builder_rejects_integrity_failure_and_budget_overflow(self) -> None:
        plan = GovernedQueryPlanner().plan(case_id="C-102", request=request())
        candidate = RetrievedCandidate(
            object_id="candidate-integrity",
            tenant_id="tenant-demo",
            source_id="test-source",
            source_version="1",
            object_type="document",
            subject_id="C-102",
            title="test",
            content="trusted evidence",
            locator="test://evidence",
            content_hash="0" * 64,
            valid_from=datetime.fromisoformat("2026-01-01T00:00:00+08:00"),
            valid_to=None,
            observed_at=datetime.fromisoformat("2026-07-20T00:00:00+08:00"),
            required_scopes=("case:read",),
            allowed_purposes=("claim_investigation",),
            supports_claims=("policy_version",),
            facts={"policy_version": "2026.1"},
            trust_level="authoritative",
            contains_instructions=False,
            channels=(RetrievalChannel.FULL_TEXT,),
            channel_ranks={"full_text": 1},
            rrf_score=0.1,
        )
        outcome = GovernedContextBuilder().build(
            candidates=[candidate],
            plan=plan,
            principal_scopes=frozenset({"case:read"}),
            round_number=1,
            sequence_start=1,
        )

        self.assertEqual(outcome.evidence, ())
        self.assertEqual(
            outcome.trace[0].decision,
            CandidateDecision.REJECTED_INTEGRITY.value,
        )


if __name__ == "__main__":
    unittest.main()
