"""Deterministically compile user requirements into an executable protocol."""

import calendar
import hashlib
import json
import re
from datetime import date

from app.schemas.literature_research.protocol import (
    AmbiguityStatus,
    ConstraintOperator,
    ConstraintSeverity,
    DocumentScope,
    DocumentType,
    MissingValuePolicy,
    ProtocolAdviceProvenance,
    ProtocolCompileRequest,
    ProtocolCompileResponse,
    ProtocolConstraint,
    ProtocolIssue,
    QuantityPolicy,
    ResearchProtocol,
    SourcePolicy,
    TimeScope,
    TopicFacet,
    TopicModel,
)


def subtract_calendar_months(value: date, months: int) -> date:
    """Subtract whole calendar months and clamp to the destination month."""
    absolute_month = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def canonical_protocol_hash(protocol: ResearchProtocol) -> str:
    """Hash canonical JSON; this value locks all approved execution semantics."""
    payload = protocol.model_dump(mode="json", by_alias=True, exclude={"protocol_id"})
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _default_facet(topic: str, definition: str) -> TopicFacet:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", topic.lower()).strip("-")[:48] or "topic"
    description = definition or f"The paper's central problem or method directly addresses {topic}."
    return TopicFacet(
        facet_id=f"must-{slug}",
        name=topic[:120],
        description=description,
        minimum_score=0.65,
        weight=1.0,
    )


class ProtocolCompilerService:
    """Build and validate a protocol without invoking an LLM or external source."""

    _JOURNAL_METRIC_MARKERS = ("jif", "cas_partition", "cas_zone", "journal_partition")
    _CONFERENCE_METRIC_MARKERS = ("conference_rank", "conference_allowlist")

    def compile(
        self,
        request: ProtocolCompileRequest,
        *,
        advice_provenance: ProtocolAdviceProvenance | None = None,
        advice_ambiguities: list[str] | None = None,
    ) -> ProtocolCompileResponse:
        issues: list[ProtocolIssue] = []
        date_to = request.date_to or request.as_of_date
        date_from = request.date_from or subtract_calendar_months(
            request.as_of_date, request.rolling_months
        )

        constraints = self._base_constraints(request, date_from, date_to)
        constraints.extend(self._scope_metric_constraints(request, issues))
        self._validate_source_coverage(request, issues)
        self._validate_quality_branches(request, constraints, issues)
        for index, ambiguity in enumerate(advice_ambiguities or [], start=1):
            issues.append(
                ProtocolIssue(
                    code=f"AGENT_AMBIGUITY_{index}",
                    field="topic",
                    message=ambiguity,
                    blocking=True,
                )
            )

        must_have = request.must_have_facets or [
            _default_facet(request.topic, request.topic_definition)
        ]
        questions = request.research_questions or [f"{request.topic} 的核心方法和证据是什么?"]
        ambiguity = (
            AmbiguityStatus.NEEDS_CLARIFICATION
            if any(issue.blocking for issue in issues)
            else AmbiguityStatus.RESOLVED
        )
        protocol = ResearchProtocol(
            topic=request.topic,
            topic_definition=request.topic_definition,
            research_questions=questions,
            topic_model=TopicModel(
                must_have_facets=must_have,
                should_have_facets=request.should_have_facets,
                exclude_facets=request.exclude_facets,
                synonym_groups=request.synonym_groups,
            ),
            time_scope=TimeScope(
                date_from=date_from,
                date_to=date_to,
                timezone=request.timezone,
            ),
            document_scope=DocumentScope(
                allowed_types=request.allowed_types,
                allowed_languages=request.allowed_languages,
            ),
            source_policy=SourcePolicy(
                required_sources=request.required_sources,
                optional_sources=request.optional_sources,
                minimum_source_families=request.minimum_source_families,
                publisher_verification_required=request.publisher_verification_required,
            ),
            constraints=constraints,
            quantity_policy=QuantityPolicy(
                target_count=request.target_count,
                shortfall_action=request.shortfall_action,
            ),
            analysis_template=request.analysis_template,
            output_policy=request.output_policy,
            llm_budget=request.llm_budget,
            ambiguity_status=ambiguity,
            issues=issues,
            draft_advice_provenance=advice_provenance,
        )
        return ProtocolCompileResponse(
            protocol=protocol,
            protocol_hash=canonical_protocol_hash(protocol),
            executable=ambiguity == AmbiguityStatus.RESOLVED,
        )

    @staticmethod
    def _base_constraints(
        request: ProtocolCompileRequest, date_from: date, date_to: date
    ) -> list[ProtocolConstraint]:
        return [
            ProtocolConstraint(
                constraint_id="system-date-from",
                field="work.effective_publication_date",
                operator=ConstraintOperator.GTE,
                value=date_from.isoformat(),
                severity=ConstraintSeverity.HARD,
                verification_source="normalized_scholarly_metadata",
                missing_value_policy=MissingValuePolicy.FAIL,
            ),
            ProtocolConstraint(
                constraint_id="system-date-to",
                field="work.effective_publication_date",
                operator=ConstraintOperator.LTE,
                value=date_to.isoformat(),
                severity=ConstraintSeverity.HARD,
                verification_source="normalized_scholarly_metadata",
                missing_value_policy=MissingValuePolicy.FAIL,
            ),
            ProtocolConstraint(
                constraint_id="system-document-type",
                field="work.document_type",
                operator=ConstraintOperator.IN,
                value=[item.value for item in request.allowed_types],
                severity=ConstraintSeverity.HARD,
                verification_source="normalized_scholarly_metadata",
                missing_value_policy=MissingValuePolicy.FAIL,
            ),
        ]

    def _scope_metric_constraints(
        self, request: ProtocolCompileRequest, issues: list[ProtocolIssue]
    ) -> list[ProtocolConstraint]:
        scoped: list[ProtocolConstraint] = []
        for constraint in request.constraints:
            field = constraint.field.lower()
            is_journal_metric = any(marker in field for marker in self._JOURNAL_METRIC_MARKERS)
            applies_to = constraint.applies_to
            if is_journal_metric and applies_to is None:
                applies_to = [DocumentType.JOURNAL_ARTICLE]
            if is_journal_metric and applies_to and DocumentType.CONFERENCE_PAPER in applies_to:
                issues.append(
                    ProtocolIssue(
                        code="JOURNAL_METRIC_NOT_APPLICABLE_TO_CONFERENCE",
                        field=f"constraints.{constraint.constraint_id}.applies_to",
                        message="JIF/CAS journal metrics cannot be applied to conference papers.",
                    )
                )
            scoped.append(constraint.model_copy(update={"applies_to": applies_to}))
        return scoped

    @staticmethod
    def _validate_source_coverage(
        request: ProtocolCompileRequest, issues: list[ProtocolIssue]
    ) -> None:
        configured = set(request.required_sources) | set(request.optional_sources)
        if len(configured) < request.minimum_source_families:
            issues.append(
                ProtocolIssue(
                    code="INSUFFICIENT_SOURCE_FAMILIES",
                    field="minimum_source_families",
                    message=(
                        f"Protocol requires {request.minimum_source_families} source families "
                        f"but only {len(configured)} are configured."
                    ),
                )
            )

    def _validate_quality_branches(
        self,
        request: ProtocolCompileRequest,
        constraints: list[ProtocolConstraint],
        issues: list[ProtocolIssue],
    ) -> None:
        includes_journals = DocumentType.JOURNAL_ARTICLE in request.allowed_types
        includes_conferences = DocumentType.CONFERENCE_PAPER in request.allowed_types
        journal_metric_constraints = [
            item
            for item in constraints
            if any(marker in item.field.lower() for marker in self._JOURNAL_METRIC_MARKERS)
        ]
        conference_metric_constraints = [
            item
            for item in constraints
            if any(marker in item.field.lower() for marker in self._CONFERENCE_METRIC_MARKERS)
        ]
        if journal_metric_constraints and not includes_journals:
            issues.append(
                ProtocolIssue(
                    code="JOURNAL_METRIC_WITHOUT_JOURNALS",
                    field="constraints",
                    message="Journal metric constraints were supplied but journals are not allowed.",
                )
            )
        if (
            includes_conferences
            and journal_metric_constraints
            and not conference_metric_constraints
        ):
            issues.append(
                ProtocolIssue(
                    code="CONFERENCE_QUALITY_POLICY_MISSING",
                    field="constraints",
                    message=(
                        "Conference papers are allowed while journal metrics are required; provide "
                        "a separate conference rank or allowlist constraint."
                    ),
                )
            )
