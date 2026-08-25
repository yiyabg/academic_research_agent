"""Deterministic paper deduplication and version-family resolution."""

import hashlib

from app.schemas.literature_research.work import (
    DuplicateDecision,
    DuplicateDecisionType,
    NormalizedPaper,
    ResolvedWorkCluster,
    WorkVersionType,
)


def _author_tokens(paper: NormalizedPaper) -> set[str]:
    return {
        (author.family_name or author.name.split()[-1]).lower()
        for author in paper.authors
        if author.name
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _year(paper: NormalizedPaper) -> int | None:
    effective = paper.dates.effective_publication_date
    return effective.year if effective else None


def duplicate_decision(left: NormalizedPaper, right: NormalizedPaper) -> DuplicateDecision:
    left_doi, right_doi = left.identifiers.doi, right.identifiers.doi
    if left_doi and right_doi and left_doi == right_doi:
        return DuplicateDecision(
            left_source_id=left.source_id,
            right_source_id=right.source_id,
            decision=DuplicateDecisionType.MERGE,
            confidence=1.0,
            reason="same_doi",
        )
    left_arxiv, right_arxiv = left.identifiers.arxiv_id, right.identifiers.arxiv_id
    if left_arxiv and right_arxiv and left_arxiv == right_arxiv:
        return DuplicateDecision(
            left_source_id=left.source_id,
            right_source_id=right.source_id,
            decision=DuplicateDecisionType.MERGE,
            confidence=1.0,
            reason="same_arxiv",
        )
    left_authors, right_authors = _author_tokens(left), _author_tokens(right)
    left_year, right_year = _year(left), _year(right)
    year_compatible = (
        left_year is not None and right_year is not None and abs(left_year - right_year) <= 1
    )
    if (
        left.title_normalized == right.title_normalized
        and bool(left_authors & right_authors)
        and year_compatible
    ):
        return DuplicateDecision(
            left_source_id=left.source_id,
            right_source_id=right.source_id,
            decision=DuplicateDecisionType.MERGE,
            confidence=0.99,
            reason="same_title_author",
        )
    title_score = _jaccard(set(left.title_normalized.split()), set(right.title_normalized.split()))
    author_score = _jaccard(left_authors, right_authors)
    if title_score >= 0.92 and author_score >= 0.70:
        return DuplicateDecision(
            left_source_id=left.source_id,
            right_source_id=right.source_id,
            decision=DuplicateDecisionType.MERGE,
            confidence=0.96,
            reason="fuzzy_title_author",
        )
    if title_score >= 0.85 and author_score >= 0.50:
        return DuplicateDecision(
            left_source_id=left.source_id,
            right_source_id=right.source_id,
            decision=DuplicateDecisionType.REVIEW,
            confidence=0.80,
            reason="borderline_title_author",
        )
    return DuplicateDecision(
        left_source_id=left.source_id,
        right_source_id=right.source_id,
        decision=DuplicateDecisionType.KEEP_SEPARATE,
        confidence=0.99,
        reason="insufficient_similarity",
    )


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


_VERSION_PRIORITY = {
    WorkVersionType.VERSION_OF_RECORD: 4,
    WorkVersionType.CONFERENCE_VERSION: 3,
    WorkVersionType.ACCEPTED_MANUSCRIPT: 2,
    WorkVersionType.PREPRINT: 1,
    WorkVersionType.UNKNOWN: 0,
}


class EntityResolutionService:
    def resolve(self, papers: list[NormalizedPaper]) -> list[ResolvedWorkCluster]:
        union = _UnionFind(len(papers))
        decisions: list[tuple[int, int, DuplicateDecision]] = []
        for left_index, left in enumerate(papers):
            for right_index in range(left_index + 1, len(papers)):
                decision = duplicate_decision(left, papers[right_index])
                if decision.decision != DuplicateDecisionType.KEEP_SEPARATE:
                    decisions.append((left_index, right_index, decision))
                if decision.decision == DuplicateDecisionType.MERGE:
                    union.union(left_index, right_index)

        grouped: dict[int, list[int]] = {}
        for index in range(len(papers)):
            grouped.setdefault(union.find(index), []).append(index)
        clusters = []
        for indexes in grouped.values():
            versions = [papers[index] for index in indexes]
            preferred = max(
                versions,
                key=lambda paper: (
                    _VERSION_PRIORITY[paper.version_type],
                    paper.dates.effective_publication_date or paper.retrieved_at.date(),
                ),
            )
            member_ids = {paper.source_id for paper in versions}
            cluster_decisions = [
                decision
                for _, _, decision in decisions
                if decision.left_source_id in member_ids and decision.right_source_id in member_ids
            ]
            seed = (
                preferred.identifiers.doi
                or preferred.identifiers.arxiv_id
                or preferred.title_normalized
            )
            cluster_key = f"W_{hashlib.sha256(seed.encode()).hexdigest()[:16].upper()}"
            clusters.append(
                ResolvedWorkCluster(
                    cluster_key=cluster_key,
                    preferred=preferred,
                    versions=versions,
                    decisions=cluster_decisions,
                )
            )
        return clusters
