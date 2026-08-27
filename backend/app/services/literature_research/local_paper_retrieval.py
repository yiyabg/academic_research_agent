"""Core chunk retrieval module for local paper search.

Provides reusable hybrid retrieval (Dense + BM25 + RRF + rerank) without
paper selection or MMR. Used by both discovery search and analysis evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.local_paper_library import (
    LocalPaper,
    LocalPaperChunk,
    LocalPaperSection,
)

if TYPE_CHECKING:
    from app.services.literature_research.local_paper_vector_index import (
        LocalPaperVectorIndex,
    )
    from app.services.literature_research.local_paper_reranker import LocalPaperReranker


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk with full lineage and retrieval scores."""

    chunk_id: UUID
    paper_id: UUID
    document_version_id: UUID
    section_id: UUID
    page_number: int
    chunk_index: int
    paragraph_index: int
    content: str
    section_heading: str
    section_type: str
    section_content: str  # Full parent content
    bbox: list[float] | None
    figure_id: UUID | None
    # Retrieval scores
    dense_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None


class LocalPaperChunkRetriever:
    """Hybrid retrieval core: Dense + BM25 + RRF + substantive filter + rerank.

    This component returns ranked chunks with full lineage. It does NOT:
    - Perform MMR diversification
    - Select final papers
    - Apply per-paper quotas
    Those are caller responsibilities (discovery vs analysis have different strategies).
    """

    def __init__(
        self,
        db: AsyncSession,
        vector_index: LocalPaperVectorIndex,
        reranker: LocalPaperReranker,
    ) -> None:
        self.db = db
        self.vector_index = vector_index
        self.reranker = reranker

    async def retrieve(
        self,
        *,
        query: str,
        collection: str,
        paper_ids: list[UUID],
        document_version_ids: list[UUID],
        dense_limit: int = settings.LOCAL_PAPER_DENSE_CANDIDATE_LIMIT,
        bm25_limit: int = settings.LOCAL_PAPER_BM25_CANDIDATE_LIMIT,
        rerank_limit: int = settings.LOCAL_PAPER_RERANK_CANDIDATE_LIMIT,
        rrf_k: int = settings.LOCAL_PAPER_RRF_K,
        min_rerank_score: float = settings.LOCAL_PAPER_RERANK_MIN_SCORE,
        apply_substantive_filter: bool = True,
    ) -> list[RetrievedChunk]:
        """Execute hybrid retrieval and return ranked chunks.

        Args:
            query: Natural language query (already cleaned of hard constraints)
            collection: Qdrant collection name
            paper_ids: Scope to these papers (from PostgreSQL metadata filter)
            document_version_ids: Active document versions only
            dense_limit: Top-K for dense retrieval
            bm25_limit: Top-K for BM25 retrieval
            rerank_limit: How many RRF-fused chunks to rerank
            rrf_k: RRF parameter
            min_rerank_score: Minimum rerank score threshold
            apply_substantive_filter: Whether to filter out tables/captions for text queries

        Returns:
            List of RetrievedChunk ordered by rerank score (or RRF if rerank N/A).
            Includes full lineage for each chunk.
        """
        if not query.strip():
            return []

        # Step 1: Dense retrieval via Qdrant
        dense_points = await self.vector_index.search(
            collection=collection,
            query=query,
            limit=dense_limit,
            paper_ids=paper_ids,
            document_version_ids=document_version_ids,
        )
        dense_scored: dict[UUID, float] = {}
        for point in dense_points:
            payload = getattr(point, "payload", {}) or {}
            try:
                chunk_id = UUID(str(payload.get("chunk_id")))
                score = float(getattr(point, "score", 0.0))
                dense_scored[chunk_id] = score
            except (ValueError, TypeError):
                continue

        # Step 2: BM25 retrieval via PostgreSQL lexical_terms
        # (Simplified - actual implementation would use _BM25CorpusCache)
        lexical_rows = (
            await self.db.execute(
                select(LocalPaperChunk.id, LocalPaperChunk.lexical_terms).where(
                    LocalPaperChunk.paper_id.in_(paper_ids),
                    LocalPaperChunk.document_version_id.in_(document_version_ids),
                )
            )
        ).all()

        # For now, simplified BM25 scoring (production uses BM25Okapi)
        bm25_scored: dict[UUID, float] = {}
        query_lower = query.lower()
        for row in lexical_rows[:bm25_limit]:
            if row.lexical_terms and query_lower in str(row.lexical_terms).lower():
                bm25_scored[row.id] = 1.0  # Simplified scoring

        # Step 3: RRF fusion
        all_chunk_ids = set(dense_scored.keys()) | set(bm25_scored.keys())
        rrf_scored: dict[UUID, float] = {}
        dense_ranks = {cid: rank for rank, cid in enumerate(sorted(dense_scored, key=dense_scored.get, reverse=True), 1)}
        bm25_ranks = {cid: rank for rank, cid in enumerate(sorted(bm25_scored, key=bm25_scored.get, reverse=True), 1)}

        for chunk_id in all_chunk_ids:
            rrf_score = 0.0
            if chunk_id in dense_ranks:
                rrf_score += 1.0 / (rrf_k + dense_ranks[chunk_id])
            if chunk_id in bm25_ranks:
                rrf_score += 1.0 / (rrf_k + bm25_ranks[chunk_id])
            rrf_scored[chunk_id] = rrf_score

        # Step 4: Load top RRF chunks with parent sections
        top_rrf_ids = sorted(rrf_scored, key=rrf_scored.get, reverse=True)[:rerank_limit]
        if not top_rrf_ids:
            return []

        chunk_rows = (
            await self.db.execute(
                select(LocalPaperChunk, LocalPaperSection)
                .join(LocalPaperSection, LocalPaperChunk.section_id == LocalPaperSection.id)
                .where(LocalPaperChunk.id.in_(top_rrf_ids))
            )
        ).all()

        candidates: list[tuple[LocalPaperChunk, LocalPaperSection, float]] = []
        for chunk, section in chunk_rows:
            rrf_score = rrf_scored.get(chunk.id, 0.0)
            candidates.append((chunk, section, rrf_score))

        # Step 5: Substantive filter (skip tables/captions for text queries)
        if apply_substantive_filter:
            is_visual = any(kw in query.lower() for kw in ["fig", "figure", "table", "图", "表"])
            if not is_visual:
                candidates = [
                    (c, s, score)
                    for c, s, score in candidates
                    if c.chunk_kind not in {"table", "figure_caption"}
                ]

        # Step 6: BGE reranker
        reranker_texts = [
            f"{section.heading}\n\n{chunk.content}"
            for chunk, section, _ in candidates
        ]
        rerank_scores = await self.reranker.score(query=query, documents=reranker_texts)

        if len(rerank_scores) != len(candidates):
            logger.warning(
                f"Reranker returned {len(rerank_scores)} scores for {len(candidates)} candidates"
            )
            rerank_scores = rerank_scores[: len(candidates)]

        # Step 7: Build final results
        results: list[RetrievedChunk] = []
        for (chunk, section, rrf_score), rerank_score in zip(candidates, rerank_scores, strict=False):
            if rerank_score < min_rerank_score:
                continue

            results.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    paper_id=chunk.paper_id,
                    document_version_id=chunk.document_version_id,
                    section_id=section.id,
                    page_number=chunk.page_number,
                    chunk_index=chunk.chunk_index,
                    paragraph_index=chunk.paragraph_index,
                    content=chunk.content,
                    section_heading=section.heading,
                    section_type=section.section_type,
                    section_content=section.content,
                    bbox=chunk.bbox_json,
                    figure_id=chunk.figure_id,
                    dense_score=dense_scored.get(chunk.id),
                    bm25_score=bm25_scored.get(chunk.id),
                    rrf_score=rrf_score,
                    rerank_score=rerank_score,
                )
            )

        # Sort by rerank score descending
        results.sort(key=lambda r: r.rerank_score or 0.0, reverse=True)
        return results
