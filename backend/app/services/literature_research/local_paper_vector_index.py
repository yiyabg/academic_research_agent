"""Versioned BGE-M3 Qdrant index for the private local paper corpus."""

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings


class LocalEmbedder(Protocol):
    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...


class BGEEmbeddingHTTPClient:
    """Synchronous adapter used from the indexer's existing worker thread."""

    def __init__(self, service_url: str | None = None) -> None:
        self.service_url = (service_url or settings.LOCAL_PAPER_EMBEDDING_SERVICE_URL).rstrip("/")

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=settings.LOCAL_PAPER_MODEL_HTTP_TIMEOUT_SECONDS) as client:
            response = client.post(f"{self.service_url}/embed", json={"texts": texts})
            response.raise_for_status()
        payload = response.json()
        vectors = payload.get("vectors")
        if not isinstance(vectors, list):
            raise RuntimeError("BGE embedding service response has no vectors list")
        return [[float(value) for value in vector] for vector in vectors]


@dataclass(frozen=True)
class LocalPaperVectorChunk:
    """A child chunk. Parent section text intentionally stays in PostgreSQL."""

    chunk_id: UUID
    paper_id: UUID
    section_id: UUID
    page_number: int
    chunk_index: int
    paragraph_index: int
    heading: str
    content: str
    figure_id: UUID | None = None


class LocalPaperVectorIndex:
    def __init__(
        self,
        *,
        client: AsyncQdrantClient | None = None,
        embedder: LocalEmbedder | None = None,
        dimension: int | None = None,
        embedding_batch_size: int | None = None,
    ) -> None:
        self.dimension = dimension or settings.LOCAL_PAPER_EMBEDDING_DIM
        self.embedding_batch_size = (
            embedding_batch_size or settings.LOCAL_PAPER_EMBEDDING_BATCH_SIZE
        )
        self.embedder = embedder or BGEEmbeddingHTTPClient()
        self.client = client or AsyncQdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
            api_key=settings.QDRANT_API_KEY or None,
        )

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed in bounded, ordered batches so a long PDF cannot exceed API limits."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.embedding_batch_size):
            batch = texts[start : start + self.embedding_batch_size]
            result = await asyncio.to_thread(self.embedder.embed_queries, batch)
            if len(result) != len(batch):
                raise RuntimeError(
                    "Local-paper embedding provider returned a count that does not match its input"
                )
            vectors.extend(result)
        return vectors

    async def ensure_collection(self, collection: str) -> None:
        collections = await self.client.get_collections()
        if collection not in {item.name for item in collections.collections}:
            await self.client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
            )

    async def replace_paper_chunks(
        self, *, collection: str, paper_id: UUID, chunks: list[LocalPaperVectorChunk]
    ) -> None:
        await self.ensure_collection(collection)
        await self.delete_paper(collection=collection, paper_id=paper_id)
        if not chunks:
            return
        vectors = await self._embed_texts([chunk.content for chunk in chunks])
        if any(len(vector) != self.dimension for vector in vectors):
            raise ValueError(
                f"Local-paper embedding dimension mismatch; expected {self.dimension}. "
                "Use a fresh versioned collection and re-sync the library."
            )
        points = [
            PointStruct(
                id=hashlib.sha256(str(chunk.chunk_id).encode()).hexdigest()[:32],
                vector=vectors[position],
                payload={
                    "chunk_id": str(chunk.chunk_id),
                    "paper_id": str(chunk.paper_id),
                    "section_id": str(chunk.section_id),
                    "page_number": chunk.page_number,
                    "chunk_index": chunk.chunk_index,
                    "paragraph_index": chunk.paragraph_index,
                    "heading": chunk.heading,
                    "figure_id": str(chunk.figure_id) if chunk.figure_id else None,
                    "content": chunk.content,
                },
            )
            for position, chunk in enumerate(chunks)
        ]
        await self.client.upsert(collection_name=collection, points=points)

    async def delete_paper(self, *, collection: str, paper_id: UUID) -> None:
        try:
            await self.client.delete(
                collection_name=collection,
                points_selector=Filter(
                    must=[FieldCondition(key="paper_id", match=MatchValue(value=str(paper_id)))]
                ),
            )
        except Exception as exc:
            # A first sync may legitimately reach this before the collection exists.
            if "not found" not in str(exc).lower():
                raise

    async def search(
        self,
        *,
        collection: str,
        query: str,
        limit: int,
        paper_ids: list[UUID],
    ) -> list[object]:
        """Dense recall after PostgreSQL metadata filtering.

        The Qdrant payload filter is deliberately part of the query rather
        than a Python post-filter. This prevents unrelated papers from using
        the finite dense candidate budget.
        """
        if not paper_ids:
            return []
        await self.ensure_collection(collection)
        vector = (await self._embed_texts([query]))[0]
        if len(vector) != self.dimension:
            raise ValueError(
                f"Local-paper query embedding dimension mismatch; expected {self.dimension}."
            )
        response = await self.client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="paper_id",
                        match=MatchAny(any=[str(paper_id) for paper_id in paper_ids]),
                    )
                ]
            ),
            limit=limit,
            with_vectors=True,
        )
        return list(response.points)
