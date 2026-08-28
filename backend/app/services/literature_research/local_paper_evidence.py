"""Analysis evidence retrieval for selected papers.

Provides independent per-paper evidence with token budgets and context construction.
"""
# ruff: noqa: RUF001 - Chinese sentence punctuation is part of the context boundary grammar.

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.local_paper_library import LocalPaper

try:
    import tiktoken
except ImportError:  # pragma: no cover - uv lock installs tiktoken in production
    tiktoken = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from app.services.literature_research.local_paper_retrieval import (
        LocalPaperChunkRetriever,
        RetrievedChunk,
    )


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisEvidence:
    """Evidence for one paper with context around child chunks."""

    paper_id: UUID
    document_version_id: UUID
    section_id: UUID
    chunk_id: UUID
    section_type: str
    section_heading: str
    page_number: int
    page_end: int | None
    child_text: str
    context_text: str
    rerank_score: float
    retrieval_pass: int  # 1 = main, 2 =補充


@dataclass(frozen=True)
class PaperEvidenceResult:
    """Evidence retrieval result for one paper."""

    paper_id: UUID
    paper_title: str
    paper_citekey: str
    document_version_id: UUID | None
    evidence: list[AnalysisEvidence]
    insufficient_evidence: bool
    queries_used: list[str]


class LocalPaperEvidenceRetriever:
    """Retrieve evidence within selected papers for deep analysis.

    Key differences from discovery search:
    - Each paper gets independent retrieval (no cross-paper MMR)
    - Token budget allocation (not fixed chunk count)
    - Context construction around child position in parent
    - Bounded補充 retrieval if first pass insufficient
    """

    def __init__(
        self,
        db: AsyncSession,
        chunk_retriever: LocalPaperChunkRetriever,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.db = db
        self.chunk_retriever = chunk_retriever
        self.token_counter = token_counter or self._default_token_count

    @staticmethod
    def _default_token_count(text: str) -> int:
        if tiktoken is not None:
            return len(tiktoken.get_encoding("cl100k_base").encode(text))
        return max(1, len(text) // 4)

    async def retrieve_for_papers(
        self,
        *,
        paper_ids: list[UUID],
        question: str,
        query_context: str | None = None,
        collection: str,
        max_evidence_per_paper: int = settings.LOCAL_PAPER_ANALYSIS_MAX_EVIDENCE_PER_PAPER,
        target_tokens_per_paper: int = settings.LOCAL_PAPER_ANALYSIS_EVIDENCE_TOKEN_BUDGET,
    ) -> list[PaperEvidenceResult]:
        """Retrieve evidence for each paper independently.

        Args:
            paper_ids: Papers to analyze (in user-selected order)
            question: Main analysis question (primary retrieval query)
            query_context: Optional context (e.g., original search topic)
            collection: Qdrant collection name
            max_evidence_per_paper: Maximum child chunks per paper
            target_tokens_per_paper: Target total evidence tokens per paper

        Returns:
            List of PaperEvidenceResult, one per paper, preserving input order.
            Papers with insufficient evidence marked but still included.
        """
        results: list[PaperEvidenceResult] = []

        # Load paper metadata
        papers = (
            (
                await self.db.execute(
                    select(LocalPaper).where(
                        LocalPaper.id.in_(paper_ids),
                        LocalPaper.status == "INDEXED",
                        LocalPaper.active_document_version_id.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )

        paper_map = {p.id: p for p in papers}

        for paper_id in paper_ids:
            paper = paper_map.get(paper_id)
            if not paper or not paper.active_document_version_id:
                results.append(
                    PaperEvidenceResult(
                        paper_id=paper_id,
                        paper_title=paper.title if paper else "Unknown",
                        paper_citekey=paper.citekey if paper else "",
                        document_version_id=paper.active_document_version_id if paper else None,
                        evidence=[],
                        insufficient_evidence=True,
                        queries_used=[question],
                    )
                )
                continue

            # Main retrieval pass with question
            main_chunks = await self.chunk_retriever.retrieve(
                query=question,
                collection=collection,
                paper_ids=[paper_id],
                document_version_ids=[paper.active_document_version_id],
                rerank_limit=20,  # More candidates for diversity selection
            )

            # Select diverse evidence within token budget
            selected_evidence = self._select_diverse_evidence(
                chunks=main_chunks,
                max_chunks=max_evidence_per_paper,
                target_tokens=target_tokens_per_paper,
            )

            queries_used = [question]
            insufficient = (
                len(selected_evidence) < settings.LOCAL_PAPER_ANALYSIS_MIN_EVIDENCE_PER_PAPER
            )

            # Bounded補充 retrieval if insufficient
            if insufficient and query_context:
                # One additional pass with combined context
                combined_query = f"{query_context}\n{question}"
                queries_used.append(combined_query)

                補充_chunks = await self.chunk_retriever.retrieve(
                    query=combined_query,
                    collection=collection,
                    paper_ids=[paper_id],
                    document_version_ids=[paper.active_document_version_id],
                    rerank_limit=20,
                )

                # A supplementary query is one bounded recall pass.  Merge it
                # with the first pass before ranking/budgeting so good first
                # pass evidence cannot vanish and duplicate children collapse.
                merged: dict[UUID, RetrievedChunk] = {
                    chunk.chunk_id: chunk for chunk in main_chunks
                }
                for chunk in 補充_chunks:
                    merged.setdefault(chunk.chunk_id, chunk)
                selected_evidence = self._select_diverse_evidence(
                    chunks=sorted(
                        merged.values(), key=lambda chunk: chunk.rerank_score or 0.0, reverse=True
                    ),
                    max_chunks=max_evidence_per_paper,
                    target_tokens=target_tokens_per_paper,
                )

                insufficient = (
                    len(selected_evidence) < settings.LOCAL_PAPER_ANALYSIS_MIN_EVIDENCE_PER_PAPER
                )

            # Build context around each child
            main_ids = {chunk.chunk_id for chunk in main_chunks}
            # Context is allocated across the selected evidence set, not with
            # an unrelated character cap on each parent section.
            per_evidence_budget = max(1, target_tokens_per_paper // max(1, len(selected_evidence)))
            evidence_with_context = [
                self._build_evidence_with_context(
                    chunk,
                    retrieval_pass=1 if chunk.chunk_id in main_ids else 2,
                    context_token_budget=per_evidence_budget,
                )
                for chunk in selected_evidence
            ]

            results.append(
                PaperEvidenceResult(
                    paper_id=paper_id,
                    paper_title=paper.title,
                    paper_citekey=paper.citekey,
                    document_version_id=paper.active_document_version_id,
                    evidence=evidence_with_context,
                    insufficient_evidence=insufficient,
                    queries_used=queries_used,
                )
            )

        return results

    def _select_diverse_evidence(
        self,
        *,
        chunks: list[RetrievedChunk],
        max_chunks: int,
        target_tokens: int,
    ) -> list[RetrievedChunk]:
        """Select diverse chunks within token budget.

        Diversity criteria:
        - Vary section_id (cover different sections)
        - Avoid near-duplicate content
        - Respect token budget

        Args:
            chunks: Candidates ordered by rerank score
            max_chunks: Hard limit on chunk count
            target_tokens: Soft limit on total tokens

        Returns:
            Selected chunks (may be fewer than max_chunks if budget exhausted)
        """
        if not chunks:
            return []

        selected: list[RetrievedChunk] = []
        seen_sections: set[UUID] = set()
        seen_content_hashes: set[str] = set()
        total_tokens = 0

        for chunk in chunks:
            # Stop if hard limit reached
            if len(selected) >= max_chunks:
                break

            chunk_tokens = self.token_counter(chunk.content)

            # Stop if adding this would significantly exceed budget
            if total_tokens > 0 and total_tokens + chunk_tokens > target_tokens * 1.2:
                break

            # Check content deduplication
            content_hash = hashlib.md5(chunk.content.encode()).hexdigest()
            if content_hash in seen_content_hashes:
                continue

            # Prefer chunks from unseen sections (soft diversity)
            # But don't skip high-relevance chunks just because section repeats
            section_penalty = 0.8 if chunk.section_id in seen_sections else 1.0
            effective_score = (chunk.rerank_score or 0.0) * section_penalty

            # Accept if high relevance or different section
            if effective_score > 0.5 or chunk.section_id not in seen_sections:
                selected.append(chunk)
                seen_sections.add(chunk.section_id)
                seen_content_hashes.add(content_hash)
                total_tokens += chunk_tokens

        return selected

    def _build_evidence_with_context(
        self,
        chunk: RetrievedChunk,
        retrieval_pass: int,
        context_token_budget: int,
    ) -> AnalysisEvidence:
        """Build evidence with context around child position.

        Context construction rules:
        1. Always include full child_text
        2. Locate child in parent section content
        3. Expand bidirectionally by token budget
        4. If child not locatable, use full child as context

        Args:
            chunk: Retrieved chunk with parent section content
            retrieval_pass: Which retrieval pass (1=main, 2=補充)

        Returns:
            AnalysisEvidence with context_text including child in context
        """
        child_text = chunk.content
        parent_text = chunk.section_content

        # Try to locate child in parent
        child_pos = parent_text.find(child_text)

        if child_pos == -1:
            # Child not found in parent (shouldn't happen, but safe fallback)
            context_text = child_text
        else:
            # Keep the entire hit, then expand outward at paragraph/sentence
            # boundaries until its share of the paper budget is reached.
            if self.token_counter(child_text) >= context_token_budget:
                context_text = child_text
            else:
                start, end = child_pos, child_pos + len(child_text)
                boundaries = [
                    match.end() for match in re.finditer(r"(?:\n\s*\n|[.!?。！？])", parent_text)
                ]
                for boundary in reversed([value for value in boundaries if value <= start]):
                    candidate = parent_text[boundary:end].strip()
                    if self.token_counter(candidate) > context_token_budget:
                        break
                    start = boundary
                for boundary in [value for value in boundaries if value >= end]:
                    candidate = parent_text[start:boundary].strip()
                    if self.token_counter(candidate) > context_token_budget:
                        break
                    end = boundary
                context_text = parent_text[start:end].strip()
                if child_text not in context_text:
                    context_text = child_text

        return AnalysisEvidence(
            paper_id=chunk.paper_id,
            document_version_id=chunk.document_version_id,
            section_id=chunk.section_id,
            chunk_id=chunk.chunk_id,
            section_type=chunk.section_type,
            section_heading=chunk.section_heading,
            page_number=chunk.page_number,
            page_end=None,  # Would need section.page_end from chunk
            child_text=child_text,
            context_text=context_text,
            rerank_score=chunk.rerank_score or 0.0,
            retrieval_pass=retrieval_pass,
        )
