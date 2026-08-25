"""Deterministic, quota-bounded DOI-seeded discovery planner."""

from app.core.config import settings
from app.schemas.literature_research.discovery import (
    QueryPlan,
    ScholarlySourceName,
    SourceQuery,
)
from app.schemas.literature_research.protocol import ResearchProtocol

MAX_SOURCE_QUERY_LENGTH = 1000


def _query_phrase(value: str) -> str:
    normalized = " ".join(value.replace('"', " ").split())
    return f'"{normalized}"'


def build_crossref_topic_query(protocol: ResearchProtocol) -> str:
    """Build one bounded discovery query from approved positive topic semantics.

    Crossref has no documented negative bibliographic-query contract, so
    exclusion facets stay in the later relevance gate rather than being sent as
    misleading positive terms. The single HTTP search quota remains unchanged.
    """
    values = [protocol.topic]
    values.extend(item.name for item in protocol.topic_model.must_have_facets)
    for group in protocol.topic_model.synonym_groups:
        values.extend([group.concept, *group.terms])
    values.extend(item.name for item in protocol.topic_model.should_have_facets)
    values.extend(item.description for item in protocol.topic_model.must_have_facets)
    values.extend(item.description for item in protocol.topic_model.should_have_facets)
    if protocol.topic_definition:
        values.append(protocol.topic_definition)

    phrases: list[str] = []
    seen: set[str] = set()
    for value in values:
        phrase = _query_phrase(value)
        identity = phrase.casefold()
        if identity in seen or phrase == '""':
            continue
        candidate = " ".join([*phrases, phrase])
        if len(candidate) > MAX_SOURCE_QUERY_LENGTH:
            continue
        seen.add(identity)
        phrases.append(phrase)
    return " ".join(phrases)


class QueryPlannerService:
    def plan(self, protocol: ResearchProtocol) -> QueryPlan:
        positive_facet_ids = [
            facet.facet_id
            for facet in (
                protocol.topic_model.must_have_facets
                + protocol.topic_model.should_have_facets
            )
        ]
        limit = settings.RESEARCH_DISCOVERY_DOI_CANDIDATE_LIMIT
        queries = [
            SourceQuery(
                query_id="q-001-crossref-doi-seeds",
                family="topic_doi_seed",
                source=ScholarlySourceName.CROSSREF,
                query_text=build_crossref_topic_query(protocol),
                date_from=protocol.time_scope.date_from,
                date_to=protocol.time_scope.date_to,
                publication_types=protocol.document_scope.allowed_types,
                filters={
                    "must_facet_ids": [
                        item.facet_id for item in protocol.topic_model.must_have_facets
                    ],
                    "should_facet_ids": [
                        item.facet_id for item in protocol.topic_model.should_have_facets
                    ],
                    "exclude_facet_ids": [
                        item.facet_id for item in protocol.topic_model.exclude_facets
                    ],
                    "synonym_concepts": [
                        item.concept for item in protocol.topic_model.synonym_groups
                    ],
                },
                facet_coverage=positive_facet_ids,
                origin="quota_bounded_doi_pipeline",
                result_limit=limit,
            )
        ]
        return QueryPlan(
            queries=queries,
            strategy="doi_seeded",
            candidate_limit=limit,
            max_pages_per_query=1,
        )
