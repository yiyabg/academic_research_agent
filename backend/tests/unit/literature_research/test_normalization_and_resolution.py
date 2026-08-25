"""Metadata normalization, paper identity, and version-family tests."""

from datetime import UTC, datetime

from app.domain.literature_research.versioning import version_observation_groups
from app.schemas.literature_research.discovery import RawSourceRecord, ScholarlySourceName
from app.schemas.literature_research.protocol import DocumentType
from app.schemas.literature_research.work import DuplicateDecisionType, WorkVersionType
from app.services.literature_research.entity_resolution import (
    EntityResolutionService,
    duplicate_decision,
)
from app.services.literature_research.metadata_normalizer import (
    MetadataNormalizerService,
    normalize_doi,
    normalize_title,
)

NOW = datetime(2026, 8, 21, tzinfo=UTC)


def test_doi_title_abstract_and_effective_date_normalization() -> None:
    record = RawSourceRecord(
        source=ScholarlySourceName.CROSSREF,
        source_id="10.1000/ABC",
        retrieved_at=NOW,
        raw={
            "DOI": "https://doi.org/10.1000/ABC.",
            "title": ["<b>Auditable</b> {Research} Agents"],
            "abstract": "<jats:p>Evidence &amp; provenance.</jats:p>",
            "type": "journal-article",
            "published-online": {"date-parts": [[2026, 7, 31]]},
            "container-title": ["Journal of Agent Systems"],
            "author": [{"given": "Alice", "family": "Smith"}],
        },
    )
    paper = MetadataNormalizerService().normalize(record)

    assert normalize_doi("DOI:10.1000/ABC") == "10.1000/abc"
    assert normalize_title("<b>Audit</b> {Agent}") == "audit agent"
    assert paper.identifiers.doi == "10.1000/abc"
    assert paper.title_normalized == "auditable research agents"
    assert paper.abstract == "Evidence & provenance."
    assert paper.dates.effective_publication_date.isoformat() == "2026-07-31"
    assert paper.dates.effective_date_field == "published_online"


def test_crossref_and_openalex_same_doi_form_one_work_and_one_version() -> None:
    records = [
        RawSourceRecord(
            source=ScholarlySourceName.CROSSREF,
            source_id="10.1000/agent",
            retrieved_at=NOW,
            raw={
                "DOI": "10.1000/agent",
                "title": ["Auditable Research Agents"],
                "type": "journal-article",
                "issued": {"date-parts": [[2026, 7, 1]]},
                "author": [{"family": "Smith"}],
            },
        ),
        RawSourceRecord(
            source=ScholarlySourceName.OPENALEX,
            source_id="https://openalex.org/W1",
            retrieved_at=NOW,
            raw={
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.1000/agent",
                "display_name": "Auditable Research Agents",
                "type": "article",
                "publication_date": "2026-07-01",
                "authorships": [{"author": {"display_name": "Alice Smith"}}],
            },
        ),
    ]
    papers = [MetadataNormalizerService().normalize(record) for record in records]
    clusters = EntityResolutionService().resolve(papers)

    assert len(clusters) == 1
    assert len(clusters[0].versions) == 2
    assert len(version_observation_groups(clusters[0].versions)) == 1
    assert clusters[0].decisions[0].reason == "same_doi"


def test_unrecognized_source_types_remain_unknown_instead_of_becoming_journal_articles() -> None:
    normalizer = MetadataNormalizerService()
    crossref = normalizer.normalize(
        RawSourceRecord(
            source=ScholarlySourceName.CROSSREF,
            source_id="10.1000/dataset",
            retrieved_at=NOW,
            raw={"DOI": "10.1000/dataset", "title": ["Dataset"], "type": "dataset"},
        )
    )
    openalex = normalizer.normalize(
        RawSourceRecord(
            source=ScholarlySourceName.OPENALEX,
            source_id="https://openalex.org/W-dataset",
            retrieved_at=NOW,
            raw={"id": "https://openalex.org/W-dataset", "title": "Dataset", "type": "dataset"},
        )
    )

    assert crossref.document_type == DocumentType.UNKNOWN
    assert openalex.document_type == DocumentType.UNKNOWN
    assert crossref.version_type == WorkVersionType.UNKNOWN
    assert openalex.version_type == WorkVersionType.UNKNOWN


def test_preprint_and_version_of_record_share_work_but_remain_versions() -> None:
    normalizer = MetadataNormalizerService()
    preprint = normalizer.normalize(
        RawSourceRecord(
            source=ScholarlySourceName.ARXIV,
            source_id="https://arxiv.org/abs/2606.00001v2",
            retrieved_at=NOW,
            raw={
                "id": "https://arxiv.org/abs/2606.00001v2",
                "title": "Auditable Research Agents",
                "summary": "Preprint",
                "published": "2026-06-01T00:00:00Z",
                "authors": ["Alice Smith"],
                "links": [],
            },
        )
    )
    journal = normalizer.normalize(
        RawSourceRecord(
            source=ScholarlySourceName.CROSSREF,
            source_id="10.1000/published",
            retrieved_at=NOW,
            raw={
                "DOI": "10.1000/published",
                "title": ["Auditable Research Agents"],
                "type": "journal-article",
                "issued": {"date-parts": [[2026, 7, 1]]},
                "author": [{"given": "Alice", "family": "Smith"}],
            },
        )
    )

    decision = duplicate_decision(preprint, journal)
    cluster = EntityResolutionService().resolve([preprint, journal])[0]
    assert decision.decision == DuplicateDecisionType.MERGE
    assert len(version_observation_groups(cluster.versions)) == 2
    assert cluster.preferred.version_type == WorkVersionType.VERSION_OF_RECORD
