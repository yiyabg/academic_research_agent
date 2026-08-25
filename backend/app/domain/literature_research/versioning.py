"""Publication-version identity policies independent of storage and transport."""

from app.schemas.literature_research.work import NormalizedPaper


def _year(paper: NormalizedPaper) -> int:
    effective = paper.dates.effective_publication_date
    return effective.year if effective else 0


def version_observation_groups(
    papers: list[NormalizedPaper],
) -> list[list[NormalizedPaper]]:
    """Group source observations that describe the same concrete publication version."""
    groups: dict[str, list[NormalizedPaper]] = {}
    for paper in papers:
        if paper.identifiers.doi:
            key = f"doi:{paper.identifiers.doi}"
        elif paper.identifiers.arxiv_id:
            key = f"arxiv:{paper.identifiers.arxiv_id}"
        else:
            key = f"fallback:{paper.version_type.value}:{paper.title_normalized}:{_year(paper)}"
        groups.setdefault(key, []).append(paper)
    return list(groups.values())


def choose_version_representative(papers: list[NormalizedPaper]) -> NormalizedPaper:
    """Choose the richest source observation without erasing the others."""
    source_priority = {"crossref": 3, "openalex": 2, "arxiv": 1}
    return max(
        papers,
        key=lambda paper: (
            int(paper.abstract is not None),
            len(paper.authors),
            int(paper.venue is not None),
            source_priority.get(paper.source.value, 0),
        ),
    )
