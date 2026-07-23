from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from caseops.context.contracts import (
    ContextTraceEvent,
    RetrievalChannel,
    RetrievalPlan,
    RetrievedCandidate,
)
from caseops.infrastructure.models import (
    KnowledgeEntityRecord,
    KnowledgeObjectRecord,
    KnowledgeRelationRecord,
    KnowledgeSourceRecord,
)

RRF_K = 60


def reciprocal_rank_fusion(
    ranked_ids: dict[RetrievalChannel, list[str]],
) -> tuple[dict[str, float], dict[str, dict[str, int]]]:
    scores: dict[str, float] = defaultdict(float)
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    for channel, object_ids in ranked_ids.items():
        for rank, object_id in enumerate(object_ids, start=1):
            scores[object_id] += 1.0 / (RRF_K + rank)
            ranks[object_id][channel.value] = rank
    return dict(scores), dict(ranks)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyContextRetriever:
    """Run allowlisted retrieval channels and fuse their independent rankings."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def retrieve(
        self,
        *,
        tenant_id: str,
        plan: RetrievalPlan,
        round_number: int,
    ) -> tuple[list[RetrievedCandidate], list[ContextTraceEvent]]:
        ranked: dict[RetrievalChannel, list[str]] = {}
        if RetrievalChannel.STRUCTURED in plan.channels:
            ranked[RetrievalChannel.STRUCTURED] = self._structured(
                tenant_id,
                plan.case_id,
                plan.candidate_limit,
            )
        if RetrievalChannel.FULL_TEXT in plan.channels:
            ranked[RetrievalChannel.FULL_TEXT] = self._full_text(
                tenant_id,
                plan.query_terms,
                plan.candidate_limit,
            )
        if RetrievalChannel.GRAPH in plan.channels:
            ranked[RetrievalChannel.GRAPH] = self._graph(
                tenant_id,
                plan,
            )

        scores, ranks = reciprocal_rank_fusion(ranked)
        rows = self._load_objects(tenant_id, list(scores))
        candidates = [
            self._candidate(row, source_id, scores[row.object_id], ranks[row.object_id])
            for row, source_id in rows
        ]
        candidates.sort(key=lambda item: (-item.rrf_score, item.object_id))

        trace: list[ContextTraceEvent] = []
        for candidate in candidates:
            for channel in candidate.channels:
                trace.append(
                    ContextTraceEvent(
                        sequence=len(trace) + 1,
                        round=round_number,
                        stage="retrieve",
                        candidate_id=candidate.object_id,
                        channel=channel,
                        decision="candidate_retrieved",
                        reason=(
                            f"rank={candidate.channel_ranks[channel.value]}; "
                            f"rrf={candidate.rrf_score:.8f}"
                        ),
                    )
                )
        return candidates, trace

    def _structured(self, tenant_id: str, case_id: str, limit: int) -> list[str]:
        fallback_statement = (
            select(KnowledgeObjectRecord.object_id)
            .join(
                KnowledgeSourceRecord,
                KnowledgeObjectRecord.source_record_id == KnowledgeSourceRecord.id,
            )
            .where(
                KnowledgeObjectRecord.tenant_id == tenant_id,
                KnowledgeObjectRecord.subject_id == case_id,
                KnowledgeSourceRecord.active.is_(True),
            )
            .order_by(KnowledgeObjectRecord.object_id)
            .limit(limit)
        )
        return list(self._session.scalars(fallback_statement))

    def _full_text(
        self,
        tenant_id: str,
        terms: tuple[str, ...],
        limit: int,
    ) -> list[str]:
        if (
            self._session.bind is not None
            and self._session.bind.dialect.name == "postgresql"
        ):
            web_query = " OR ".join(f'"{term}"' for term in terms)
            statement = text(
                """
                SELECT ko.object_id
                FROM knowledge_objects AS ko
                JOIN knowledge_sources AS ks ON ks.id = ko.source_record_id
                WHERE ko.tenant_id = :tenant_id
                  AND ks.active = true
                  AND to_tsvector(
                        'simple',
                        coalesce(ko.title, '') || ' ' || coalesce(ko.content, '')
                      ) @@ websearch_to_tsquery('simple', :query)
                ORDER BY ts_rank_cd(
                           to_tsvector(
                             'simple',
                             coalesce(ko.title, '') || ' ' || coalesce(ko.content, '')
                           ),
                           websearch_to_tsquery('simple', :query)
                         ) DESC,
                         ko.object_id
                LIMIT :limit
                """
            )
            result = self._session.execute(
                statement,
                {
                    "tenant_id": tenant_id,
                    "query": web_query,
                    "limit": limit,
                },
            ).scalars()
            object_ids = cast(list[str], list(result))
            if object_ids:
                return object_ids

        normalized_terms = tuple(term.casefold() for term in terms)
        filters = [
            or_(
                func.lower(KnowledgeObjectRecord.title).contains(term),
                func.lower(KnowledgeObjectRecord.content).contains(term),
            )
            for term in normalized_terms
        ]
        fallback_statement = (
            select(KnowledgeObjectRecord.object_id)
            .join(
                KnowledgeSourceRecord,
                KnowledgeObjectRecord.source_record_id == KnowledgeSourceRecord.id,
            )
            .where(
                KnowledgeObjectRecord.tenant_id == tenant_id,
                KnowledgeSourceRecord.active.is_(True),
                or_(*filters),
            )
            .order_by(KnowledgeObjectRecord.object_id)
            .limit(limit)
        )
        return list(self._session.scalars(fallback_statement))

    def _graph(self, tenant_id: str, plan: RetrievalPlan) -> list[str]:
        seed_ids = list(
            self._session.scalars(
                select(KnowledgeEntityRecord.id).where(
                    KnowledgeEntityRecord.tenant_id == tenant_id,
                    KnowledgeEntityRecord.entity_key.in_(plan.seed_entities),
                )
            )
        )
        if not seed_ids:
            return []

        current = set(seed_ids)
        visited = set(seed_ids)
        evidence_record_ids: list[str] = []
        as_of = plan.as_of
        for _ in range(plan.max_hops):
            if not current:
                break
            relations = list(
                self._session.scalars(
                    select(KnowledgeRelationRecord)
                    .where(
                        KnowledgeRelationRecord.tenant_id == tenant_id,
                        KnowledgeRelationRecord.from_entity_id.in_(current),
                        KnowledgeRelationRecord.path_template.in_(
                            plan.graph_path_templates
                        ),
                        KnowledgeRelationRecord.valid_from <= as_of,
                        or_(
                            KnowledgeRelationRecord.valid_to.is_(None),
                            KnowledgeRelationRecord.valid_to > as_of,
                        ),
                    )
                    .order_by(KnowledgeRelationRecord.relation_id)
                )
            )
            next_nodes: set[str] = set()
            for relation in relations:
                if relation.evidence_object_record_id not in evidence_record_ids:
                    evidence_record_ids.append(relation.evidence_object_record_id)
                if relation.to_entity_id not in visited:
                    visited.add(relation.to_entity_id)
                    next_nodes.add(relation.to_entity_id)
            current = next_nodes

        if not evidence_record_ids:
            return []
        return list(
            self._session.scalars(
                select(KnowledgeObjectRecord.object_id)
                .where(KnowledgeObjectRecord.id.in_(evidence_record_ids))
                .order_by(KnowledgeObjectRecord.object_id)
            )
        )

    def _load_objects(
        self,
        tenant_id: str,
        object_ids: list[str],
    ) -> list[tuple[KnowledgeObjectRecord, str]]:
        if not object_ids:
            return []
        result = self._session.execute(
            select(KnowledgeObjectRecord, KnowledgeSourceRecord.source_id)
            .join(
                KnowledgeSourceRecord,
                KnowledgeObjectRecord.source_record_id == KnowledgeSourceRecord.id,
            )
            .where(
                KnowledgeObjectRecord.tenant_id == tenant_id,
                KnowledgeObjectRecord.object_id.in_(object_ids),
                KnowledgeSourceRecord.active.is_(True),
            )
        )
        return [(row[0], row[1]) for row in result.all()]

    @staticmethod
    def _candidate(
        row: KnowledgeObjectRecord,
        source_id: str,
        score: float,
        ranks: dict[str, int],
    ) -> RetrievedCandidate:
        ordered_channels = tuple(
            sorted(
                (RetrievalChannel(value) for value in ranks),
                key=lambda channel: ranks[channel.value],
            )
        )
        return RetrievedCandidate(
            object_id=row.object_id,
            tenant_id=row.tenant_id,
            source_id=source_id,
            source_version=row.source_version,
            object_type=row.object_type,
            subject_id=row.subject_id,
            title=row.title,
            content=row.content,
            locator=row.locator,
            content_hash=row.content_hash,
            valid_from=_aware(row.valid_from),
            valid_to=_aware(row.valid_to) if row.valid_to else None,
            observed_at=_aware(row.observed_at),
            required_scopes=tuple(row.required_scopes),
            allowed_purposes=tuple(row.allowed_purposes),
            supports_claims=tuple(row.supports_claims),
            facts=dict(row.facts),
            trust_level=cast(
                Literal["authoritative", "operational", "untrusted"],
                row.trust_level,
            ),
            contains_instructions=row.contains_instructions,
            channels=ordered_channels,
            channel_ranks=ranks,
            rrf_score=score,
        )
