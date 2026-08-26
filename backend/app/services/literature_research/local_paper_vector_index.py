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
    PayloadSchemaType,
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
    document_version_id: UUID | None = None
    figure_id: UUID | None = None
    node_type: str = "text"
    embedding_text: str | None = None


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
        # These fields are used in every private-library query. Creating the
        # payload indexes makes metadata-filtered dense recall scale with the
        # corpus instead of scanning all points in Python.
        create_payload_index = getattr(self.client, "create_payload_index", None)
        if create_payload_index is None:
            # Small in-process fakes used by unit tests intentionally model
            # only vector operations. Real AsyncQdrantClient always exposes
            # this method, so production never loses its payload indexes.
            return
        for field in ("paper_id", "document_version_id", "node_type"):
            try:
                await create_payload_index(
                    collection_name=collection,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            except Exception as exc:
                # Qdrant reports an already-existing index as an error in some
                # versions. Any other error must still surface.
                if "already exists" not in str(exc).lower():
                    raise

    async def replace_paper_chunks(
        self, *, collection: str, paper_id: UUID, chunks: list[LocalPaperVectorChunk]
    ) -> None:
        await self.ensure_collection(collection)
        await self.delete_paper(collection=collection, paper_id=paper_id)
        if not chunks:
            return
        vectors = await self._embed_texts(
            [chunk.embedding_text or chunk.content for chunk in chunks]
        )
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
                    "document_version_id": (
                        str(chunk.document_version_id) if chunk.document_version_id else None
                    ),
                    "page_number": chunk.page_number,
                    "chunk_index": chunk.chunk_index,
                    "paragraph_index": chunk.paragraph_index,
                    "heading": chunk.heading,
                    "node_type": chunk.node_type,
                    "figure_id": str(chunk.figure_id) if chunk.figure_id else None,
                },
            )
            for position, chunk in enumerate(chunks)
        ]
        await self.client.upsert(collection_name=collection, points=points)

    async def activate_document_version_chunks(
        self,
        *,
        collection: str,
        paper_id: UUID,
        document_version_id: UUID,
        chunks: list[LocalPaperVectorChunk],
    ) -> None:
        """Upsert a complete new version before deleting older paper points.

        A sync may die between database writes and Qdrant operations.  The
        active PostgreSQL version remains authoritative; this order guarantees
        it never observes a paper with *no* dense points during a normal
        version switch.
        """
        await self.ensure_collection(collection)
        if chunks:
            vectors = await self._embed_texts(
                [chunk.embedding_text or chunk.content for chunk in chunks]
            )
            if any(len(vector) != self.dimension for vector in vectors):
                raise ValueError(
                    f"Local-paper embedding dimension mismatch; expected {self.dimension}."
                )
            await self.client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(
                        id=hashlib.sha256(str(chunk.chunk_id).encode()).hexdigest()[:32],
                        vector=vectors[position],
                        payload={
                            "chunk_id": str(chunk.chunk_id),
                            "paper_id": str(chunk.paper_id),
                            "section_id": str(chunk.section_id),
                            "document_version_id": str(document_version_id),
                            "page_number": chunk.page_number,
                            "chunk_index": chunk.chunk_index,
                            "paragraph_index": chunk.paragraph_index,
                            "heading": chunk.heading,
                            "node_type": chunk.node_type,
                            "figure_id": str(chunk.figure_id) if chunk.figure_id else None,
                        },
                    )
                    for position, chunk in enumerate(chunks)
                ],
                wait=True,
            )
        # This v7 collection contains document-version payloads only.  It is
        # versioned by collection name, so no legacy points need a risky
        # best-effort null filter.
        await self.client.delete(
            collection_name=collection,
            points_selector=Filter(
                must=[FieldCondition(key="paper_id", match=MatchValue(value=str(paper_id)))],
                must_not=[
                    FieldCondition(
                        key="document_version_id", match=MatchValue(value=str(document_version_id))
                    )
                ],
            ),
            wait=True,
        )

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
        document_version_ids: list[UUID] | None = None,
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
        conditions = [
            FieldCondition(
                key="paper_id",
                match=MatchAny(any=[str(paper_id) for paper_id in paper_ids]),
            )
        ]
        if document_version_ids:
            conditions.append(
                FieldCondition(
                    key="document_version_id",
                    match=MatchAny(any=[str(version_id) for version_id in document_version_ids]),
                )
            )
        response = await self.client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=Filter(must=conditions),
            limit=limit,
            with_vectors=True,
        )
        return list(response.points)

    async def fetch_chunk_vectors(
        self, *, collection: str, chunk_ids: list[UUID]
    ) -> dict[UUID, list[float]]:
        """Load vectors for lexical-only fusion candidates before MMR.

        RRF can surface an FTS candidate that did not appear in dense top-K.
        Fetching its stored BGE vector prevents MMR from silently becoming a
        relevance-only selector for that branch.
        """
        if not chunk_ids:
            return {}
        point_ids = [hashlib.sha256(str(chunk_id).encode()).hexdigest()[:32] for chunk_id in chunk_ids]
        points = await self.client.retrieve(
            collection_name=collection,
            ids=point_ids,
            with_vectors=True,
        )
        result: dict[UUID, list[float]] = {}
        for point in points:
            payload = getattr(point, "payload", {}) or {}
            try:
                chunk_id = UUID(str(payload.get("chunk_id")))
            except (ValueError, TypeError):
                continue
            raw_vector = getattr(point, "vector", None)
            if isinstance(raw_vector, dict):
                raw_vector = next(iter(raw_vector.values()), None)
            if isinstance(raw_vector, list):
                result[chunk_id] = [float(value) for value in raw_vector]
        return result
