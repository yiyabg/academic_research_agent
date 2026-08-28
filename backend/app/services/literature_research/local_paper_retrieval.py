"""Core chunk retrieval module for local paper search.

Provides reusable hybrid retrieval (Dense + BM25 + RRF + rerank) without
paper selection or MMR. Used by both discovery search and analysis evidence.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.local_paper_library import (
    LocalPaperChunk,
    LocalPaperSection,
)

if TYPE_CHECKING:
    from app.services.literature_research.local_paper_reranker import LocalPaperReranker
    from app.services.literature_research.local_paper_vector_index import (
        LocalPaperVectorIndex,
    )


logger = logging.getLogger(__name__)
_BM25_TOKEN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.I)


def _bm25_tokens(text: str) -> list[str]:
    """Tokenise English terms and CJK unigrams/bigrams consistently for BM25."""
    raw = _BM25_TOKEN.findall(text.casefold())
    cjk = "".join(token for token in raw if len(token) == 1 and "\u4e00" <= token <= "\u9fff")
    return raw + [cjk[index : index + 2] for index in range(max(0, len(cjk) - 1))]


@dataclass(frozen=True)
class _BM25Corpus:
    chunk_ids: tuple[UUID, ...]
    model: BM25Okapi


class _BM25CorpusCache:
    """A small process cache; the caller's active-version scope is its key."""

    _entries: OrderedDict[str, _BM25Corpus] = OrderedDict()
    max_entries = 16

    @classmethod
    def get_or_create(
        cls, *, key: str, chunk_ids: tuple[UUID, ...], token_rows: list[list[str]]
    ) -> _BM25Corpus | None:
        cached = cls._entries.get(key)
        if cached is not None:
            cls._entries.move_to_end(key)
            return cached
        if not token_rows:
            return None
        corpus = _BM25Corpus(chunk_ids=chunk_ids, model=BM25Okapi(token_rows))
        cls._entries[key] = corpus
        cls._entries.move_to_end(key)
        while len(cls._entries) > cls.max_entries:
            cls._entries.popitem(last=False)
        return corpus


def _bm25_scope_key(paper_ids: Iterable[UUID], document_version_ids: Iterable[UUID]) -> str:
    """Stable cache key which changes with the authoritative document version."""
    raw = "|".join(
        [
            ",".join(sorted(str(value) for value in paper_ids)),
            ",".join(sorted(str(value) for value in document_version_ids)),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _rrf_fuse(
    dense: list[tuple[UUID, float, list[float] | None]],
    bm25: list[tuple[UUID, float]],
    *,
    rrf_k: int,
) -> dict[UUID, tuple[float, float | None, float | None, list[float] | None]]:
    """Fuse ranked dense/BM25 lists without comparing raw score scales."""
    fused: dict[UUID, list[object]] = {}
    for rank, (chunk_id, score, vector) in enumerate(
        sorted(dense, key=lambda item: item[1], reverse=True), 1
    ):
        values = fused.setdefault(chunk_id, [0.0, None, None, None])
        values[0] = float(values[0]) + 1.0 / (rrf_k + rank)
        values[1], values[3] = score, vector
    for rank, (chunk_id, score) in enumerate(
        sorted(bm25, key=lambda item: item[1], reverse=True), 1
    ):
        values = fused.setdefault(chunk_id, [0.0, None, None, None])
        values[0] = float(values[0]) + 1.0 / (rrf_k + rank)
        values[2] = score
    return {
        chunk_id: (float(values[0]), values[1], values[2], values[3])
        for chunk_id, values in fused.items()
    }


def _bm25_rank(
    *,
    rows: Iterable[tuple[UUID, str | None]],
    query: str,
    scope_key: str,
    limit: int,
) -> list[tuple[UUID, float]]:
    """Score the complete lexical scope, then return its top-k positive hits."""
    populated_rows = [(chunk_id, terms) for chunk_id, terms in rows if terms]
    corpus = _BM25CorpusCache.get_or_create(
        key=scope_key,
        chunk_ids=tuple(chunk_id for chunk_id, _ in populated_rows),
        token_rows=[_bm25_tokens(str(terms)) for _, terms in populated_rows],
    )
    query_tokens = _bm25_tokens(query)
    if corpus is None or not query_tokens:
        return []
    return sorted(
        (
            (chunk_id, float(score))
            for chunk_id, score in zip(
                corpus.chunk_ids, corpus.model.get_scores(query_tokens), strict=True
            )
            if score > 0
        ),
        key=lambda item: item[1],
        reverse=True,
    )[:limit]


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

        # Step 2: genuine scoped BM25 over *all* PostgreSQL lexical rows.  The
        # limit is applied after Okapi scoring, never by slicing database rows.
        lexical_rows = (
            await self.db.execute(
                select(LocalPaperChunk.id, LocalPaperChunk.lexical_terms).where(
                    LocalPaperChunk.paper_id.in_(paper_ids),
                    LocalPaperChunk.document_version_id.in_(document_version_ids),
                )
            )
        ).all()
        ranked_bm25 = _bm25_rank(
            rows=[(row.id, row.lexical_terms) for row in lexical_rows],
            query=query,
            scope_key=_bm25_scope_key(paper_ids, document_version_ids),
            limit=bm25_limit,
        )
        bm25_scored = dict(ranked_bm25)

        # Step 3: RRF fusion
        fused = _rrf_fuse(
            [(chunk_id, score, None) for chunk_id, score in dense_scored.items()],
            ranked_bm25,
            rrf_k=rrf_k,
        )
        rrf_scored = {chunk_id: score[0] for chunk_id, score in fused.items()}

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
            f"{section.heading}\n\n{chunk.content}" for chunk, section, _ in candidates
        ]
        rerank_scores = await self.reranker.score(query=query, documents=reranker_texts)

        if len(rerank_scores) != len(candidates):
            raise RuntimeError("BGE reranker returned a score count that does not match candidates")

        # Step 7: Build final results
        results: list[RetrievedChunk] = []
        for (chunk, section, rrf_score), rerank_score in zip(
            candidates, rerank_scores, strict=True
        ):
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
