"""Tenant/project/run-isolated Qdrant evidence index."""

import asyncio
import hashlib
from typing import Protocol
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    ScoredPoint,
    VectorParams,
)
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.schemas.literature_research.evidence import ParsedBlock
from app.services.literature_research.vector_namespace import (
    research_collection_name,
    research_memory_collection_name,
)
from app.services.rag.embeddings import EmbeddingService


class TextEmbeddingProvider(Protocol):
    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...


class LocalSentenceTransformerProvider:
    """Lazy local embedding backend used when no hosted embedding key is configured."""

    def __init__(self, model_name: str, cache_dir: str) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self.model_name, cache_folder=self.cache_dir)
        return self._model

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]


def get_research_embedding_provider() -> tuple[TextEmbeddingProvider, int, str]:
    # Generative credentials must not implicitly select an embedding backend:
    # an OpenAI-compatible Responses gateway may not expose /embeddings.
    if settings.RESEARCH_EMBEDDING_PROVIDER == "openai":
        provider = EmbeddingService(settings.rag).provider
        return provider, settings.rag.embeddings_config.dim, settings.EMBEDDING_MODEL
    provider = LocalSentenceTransformerProvider(
        settings.RESEARCH_LOCAL_EMBEDDING_MODEL, str(settings.MODELS_CACHE_DIR)
    )
    return (
        provider,
        settings.RESEARCH_LOCAL_EMBEDDING_DIM,
        settings.RESEARCH_LOCAL_EMBEDDING_MODEL,
    )


class ResearchVectorIndex:
    def __init__(
        self,
        *,
        client: AsyncQdrantClient | None = None,
        embedder: TextEmbeddingProvider | None = None,
        dimension: int | None = None,
    ) -> None:
        if embedder is None:
            provider, default_dimension, _ = get_research_embedding_provider()
            self.embedder = provider
        else:
            self.embedder = embedder
            default_dimension = settings.rag.embeddings_config.dim
        self.dimension = dimension or default_dimension
        self.client = client or AsyncQdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT,
            # qdrant-client interprets any non-None api_key, including an
            # empty string, as a signal to default to HTTPS.
            api_key=settings.QDRANT_API_KEY or None,
        )

    async def ensure_collection(self, name: str) -> None:
        collections = await self.client.get_collections()
        if name not in {item.name for item in collections.collections}:
            await self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
            )

    async def upsert_blocks(
        self,
        *,
        organization_id: UUID | None,
        project_id: UUID,
        run_id: UUID,
        work_id: UUID,
        version_id: UUID,
        blocks: list[ParsedBlock],
    ) -> None:
        collection = research_collection_name(organization_id, project_id)
        await self.ensure_collection(collection)
        vectors = await asyncio.to_thread(
            self.embedder.embed_queries, [block.text for block in blocks]
        )
        tenant_id = str(organization_id) if organization_id else "personal"
        points = [
            PointStruct(
                id=hashlib.sha256(f"{run_id}:{version_id}:{block.block_id}".encode()).hexdigest()[
                    :32
                ],
                vector=vectors[index],
                payload={
                    "tenant_id": tenant_id,
                    "project_id": str(project_id),
                    "run_id": str(run_id),
                    "work_id": str(work_id),
                    "version_id": str(version_id),
                    "block_id": block.block_id,
                    "page_number": block.page_number,
                    "section_path": block.section_path,
                    "text_sha256": block.text_sha256,
                    "content": block.text,
                },
            )
            for index, block in enumerate(blocks)
        ]
        await self.client.upsert(collection_name=collection, points=points)

    async def search(
        self,
        *,
        organization_id: UUID | None,
        project_id: UUID,
        run_id: UUID,
        query: str,
        limit: int = 10,
    ) -> list[ScoredPoint]:
        collection = research_collection_name(organization_id, project_id)
        vector = (await asyncio.to_thread(self.embedder.embed_queries, [query]))[0]
        tenant_id = str(organization_id) if organization_id else "personal"
        query_filter = Filter(
            must=[
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                FieldCondition(key="project_id", match=MatchValue(value=str(project_id))),
                FieldCondition(key="run_id", match=MatchValue(value=str(run_id))),
            ]
        )
        response = await self.client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=query_filter,
            limit=limit,
        )
        return response.points

    async def upsert_project_memory(
        self,
        *,
        organization_id: UUID | None,
        project_id: UUID,
        memory_id: UUID,
        memory_type: str,
        content: str,
        source: str,
    ) -> None:
        collection = research_memory_collection_name(organization_id, project_id)
        await self.ensure_collection(collection)
        vector = (await asyncio.to_thread(self.embedder.embed_queries, [content]))[0]
        tenant_id = str(organization_id) if organization_id else "personal"
        await self.client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=memory_id.hex,
                    vector=vector,
                    payload={
                        "tenant_id": tenant_id,
                        "project_id": str(project_id),
                        "memory_id": str(memory_id),
                        "memory_type": memory_type,
                        "source": source,
                        "content": content,
                    },
                )
            ],
        )

    async def search_project_memories(
        self,
        *,
        organization_id: UUID | None,
        project_id: UUID,
        query: str,
        limit: int = 10,
    ) -> list[ScoredPoint]:
        collection = research_memory_collection_name(organization_id, project_id)
        vector = (await asyncio.to_thread(self.embedder.embed_queries, [query]))[0]
        tenant_id = str(organization_id) if organization_id else "personal"
        response = await self.client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=Filter(
                must=[
                    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                    FieldCondition(key="project_id", match=MatchValue(value=str(project_id))),
                ]
            ),
            limit=limit,
        )
        return response.points
