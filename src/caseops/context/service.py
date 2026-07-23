from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from caseops.collaboration.contracts import CloudEvent
from caseops.context.answer import EvidenceBoundAnswerer, missing_claims
from caseops.context.builder import BuildOutcome, GovernedContextBuilder
from caseops.context.contracts import (
    ContextInvestigationRequest,
    ContextInvestigationResult,
    ContextPack,
    ContextTraceEvent,
    RetrievalPlan,
)
from caseops.context.planner import GovernedQueryPlanner
from caseops.context.retrieval import SqlAlchemyContextRetriever
from caseops.errors import IdempotencyConflict
from caseops.infrastructure.models import (
    AuditEventRecord,
    ContextRunRecord,
    OutboxEventRecord,
    new_id,
)
from caseops.infrastructure.repositories import SqlAlchemyCaseRepository
from caseops.service import Principal


@dataclass(frozen=True, slots=True)
class ContextExecution:
    run_id: str
    idempotency_key: str
    status: str
    replayed: bool
    result: dict[str, object]
    created_at: datetime
    completed_at: datetime


def _request_hash(
    case_id: str,
    request: ContextInvestigationRequest,
) -> str:
    canonical = json.dumps(
        {
            "schema_version": "caseops.context-investigation-request.v1",
            "case_id": case_id,
            **request.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ContextInvestigationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def execute(
        self,
        *,
        principal: Principal,
        case_id: str,
        request: ContextInvestigationRequest,
        idempotency_key: str,
        request_id: str,
    ) -> ContextExecution:
        request_hash = _request_hash(case_id, request)
        try:
            with self._session.begin():
                existing = self._find_existing(
                    principal.tenant_id,
                    idempotency_key,
                )
                if existing is not None:
                    return self._replay(existing, request_hash)

                SqlAlchemyCaseRepository(self._session).get(
                    principal.tenant_id,
                    case_id,
                )
                run_id = new_id()
                result, plan = self._investigate(
                    run_id=run_id,
                    tenant_id=principal.tenant_id,
                    case_id=case_id,
                    principal_scopes=principal.scopes,
                    request=request,
                )
                now = datetime.now(UTC)
                payload = result.model_dump(mode="json")
                row = ContextRunRecord(
                    id=run_id,
                    tenant_id=principal.tenant_id,
                    case_id=case_id,
                    actor_id=principal.actor_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    question=request.question,
                    purpose=request.purpose,
                    as_of=request.as_of,
                    status=result.answer.verdict.value,
                    retrieval_plan=plan.model_dump(mode="json"),
                    context_pack=result.context_pack.model_dump(mode="json"),
                    answer=result.answer.model_dump(mode="json"),
                    trace=[event.model_dump(mode="json") for event in result.trace],
                    created_at=now,
                    completed_at=now,
                )
                self._session.add(row)
                self._session.add(
                    AuditEventRecord(
                        tenant_id=principal.tenant_id,
                        actor_id=principal.actor_id,
                        action="context.investigate",
                        subject_type="case",
                        subject_id=case_id,
                        request_id=request_id,
                        outcome=result.answer.verdict.value,
                        details={
                            "run_id": run_id,
                            "pack_id": result.context_pack.pack_id,
                            "evidence_count": len(result.context_pack.evidence),
                            "retrieval_rounds": result.context_pack.retrieval_rounds,
                            "side_effect": result.answer.side_effect,
                        },
                    )
                )
                event = CloudEvent(
                    id=new_id(),
                    source="/caseops/context-investigations",
                    type="dev.caseops.context.completed.v1",
                    subject=f"case/{case_id}",
                    time=now,
                    dataschema=(
                        "https://caseops.dev/schemas/context-investigation-result.v1.json"
                    ),
                    correlationid=run_id,
                    tenantid=principal.tenant_id,
                    data={
                        "run_id": run_id,
                        "pack_id": result.context_pack.pack_id,
                        "verdict": result.answer.verdict.value,
                        "evidence_count": len(result.context_pack.evidence),
                        "stop_reason": result.context_pack.stop_reason,
                    },
                )
                self._session.add(
                    OutboxEventRecord(
                        tenant_id=principal.tenant_id,
                        topic=event.type,
                        aggregate_type="context_run",
                        aggregate_id=run_id,
                        payload=event.model_dump(mode="json"),
                    )
                )
                return ContextExecution(
                    run_id=run_id,
                    idempotency_key=idempotency_key,
                    status=result.answer.verdict.value,
                    replayed=False,
                    result=payload,
                    created_at=now,
                    completed_at=now,
                )
        except IntegrityError:
            self._session.rollback()
            existing = self._find_existing(
                principal.tenant_id,
                idempotency_key,
            )
            if existing is None:
                raise
            return self._replay(existing, request_hash)

    def _investigate(
        self,
        *,
        run_id: str,
        tenant_id: str,
        case_id: str,
        principal_scopes: frozenset[str],
        request: ContextInvestigationRequest,
    ) -> tuple[ContextInvestigationResult, RetrievalPlan]:
        planner = GovernedQueryPlanner()
        retriever = SqlAlchemyContextRetriever(self._session)
        builder = GovernedContextBuilder()
        plan = planner.plan(case_id=case_id, request=request)
        trace: list[ContextTraceEvent] = []
        previous_ids: frozenset[str] = frozenset()
        build: BuildOutcome | None = None
        stop_reason: Literal[
            "evidence_sufficient",
            "max_rounds_reached",
            "no_progress",
        ] = "max_rounds_reached"
        rounds = 0

        for round_number in range(1, plan.max_rounds + 1):
            rounds = round_number
            candidates, retrieval_trace = retriever.retrieve(
                tenant_id=tenant_id,
                plan=plan,
                round_number=round_number,
            )
            trace.extend(self._resequence(retrieval_trace, len(trace) + 1))
            build = builder.build(
                candidates=candidates,
                plan=plan,
                principal_scopes=principal_scopes,
                round_number=round_number,
                sequence_start=len(trace) + 1,
            )
            trace.extend(build.trace)
            missing = missing_claims(build.evidence)
            trace.append(
                ContextTraceEvent(
                    sequence=len(trace) + 1,
                    round=round_number,
                    stage="sufficiency",
                    candidate_id=None,
                    channel=None,
                    decision=("evidence_sufficient" if not missing else "evidence_gap"),
                    reason=(
                        "all required claim types are covered"
                        if not missing
                        else f"missing={','.join(missing)}"
                    ),
                )
            )
            if not missing:
                stop_reason = "evidence_sufficient"
                break

            selected_ids = frozenset(item.object_id for item in build.evidence)
            broadened = planner.broaden(plan)
            if selected_ids == previous_ids and broadened.channels == plan.channels:
                stop_reason = "no_progress"
                break
            previous_ids = selected_ids
            plan = broadened

        if build is None:
            raise RuntimeError("context builder did not execute")

        answer = EvidenceBoundAnswerer().answer(build.evidence)
        trace.append(
            ContextTraceEvent(
                sequence=len(trace) + 1,
                round=rounds,
                stage="answer",
                candidate_id=None,
                channel=None,
                decision=answer.verdict.value,
                reason=(
                    "every answer claim is bound to selected evidence"
                    if not answer.unresolved_questions
                    else "answer withheld because required evidence is missing"
                ),
            )
        )
        pack = ContextPack(
            pack_id=new_id(),
            run_id=run_id,
            purpose=request.purpose,
            as_of=request.as_of,
            retrieval_plan=plan,
            evidence=build.evidence,
            omissions=build.omissions,
            evidence_token_count=build.token_count,
            evidence_token_budget=request.evidence_token_budget,
            retrieval_rounds=rounds,
            stop_reason=stop_reason,
        )
        return (
            ContextInvestigationResult(
                case_id=case_id,
                context_pack=pack,
                answer=answer,
                trace=tuple(trace),
            ),
            plan,
        )

    @staticmethod
    def _resequence(
        events: list[ContextTraceEvent],
        start: int,
    ) -> list[ContextTraceEvent]:
        return [
            event.model_copy(update={"sequence": start + index})
            for index, event in enumerate(events)
        ]

    def _find_existing(
        self,
        tenant_id: str,
        idempotency_key: str,
    ) -> ContextRunRecord | None:
        return self._session.scalar(
            select(ContextRunRecord).where(
                ContextRunRecord.tenant_id == tenant_id,
                ContextRunRecord.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _replay(
        existing: ContextRunRecord,
        request_hash: str,
    ) -> ContextExecution:
        if existing.request_hash != request_hash:
            raise IdempotencyConflict(existing.idempotency_key)
        result = ContextInvestigationResult(
            case_id=existing.case_id,
            context_pack=existing.context_pack,
            answer=existing.answer,
            trace=tuple(existing.trace),
        )
        return ContextExecution(
            run_id=existing.id,
            idempotency_key=existing.idempotency_key,
            status=existing.status,
            replayed=True,
            result=result.model_dump(mode="json"),
            created_at=existing.created_at,
            completed_at=existing.completed_at,
        )
