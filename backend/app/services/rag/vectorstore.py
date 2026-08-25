import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from app.schemas.rag import RAGDocumentItem, RAGDocumentList
from app.services.rag.models import (
    CollectionInfo,
    Document,
    DocumentInfo,
    DocumentPageChunk,
    SearchResult,
)

logger = logging.getLogger(__name__)

_COLLECTION_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
_RESERVED_COLLECTION_NAMES = frozenset({"all"})


class BaseVectorStore(ABC):
    @abstractmethod
    async def insert_document(self, collection_name: str, document: Document) -> None:
        pass

    @abstractmethod
    async def search(
        self, collection_name: str, query: str, limit: int = 4, filter_expr: str = ""
    ) -> list[SearchResult]:
        pass

    @abstractmethod
    async def delete_collection(self, collection_name: str) -> None:
        pass

    @abstractmethod
    async def delete_document(self, collection_name: str, document_id: str) -> None:
        pass

    @abstractmethod
    async def get_collection_info(self, collection_name: str) -> CollectionInfo:
        pass

    @abstractmethod
    async def list_collections(self) -> list[str]:
        pass

    @abstractmethod
    async def get_documents(self, collection_name: str) -> list[DocumentInfo]:
        pass

    async def get_document_list(self, collection_name: str) -> RAGDocumentList:
        docs = await self.get_documents(collection_name)
        return RAGDocumentList(
            items=[
                RAGDocumentItem(
                    document_id=doc.document_id,
                    filename=doc.filename,
                    filesize=doc.filesize,
                    filetype=doc.filetype,
                    chunk_count=doc.chunk_count,
                    additional_info=doc.additional_info,
                )
                for doc in docs
            ],
            total=len(docs),
        )

    async def create_collection(self, name: str) -> None:
        if not _COLLECTION_NAME_RE.match(name):
            raise ValueError(
                "Collection name must start with a letter and contain only "
                "letters, numbers, and underscores (max 64 chars)"
            )
        if name.lower() in _RESERVED_COLLECTION_NAMES:
            raise ValueError(f"'{name}' is a reserved collection name")
        await self._ensure_collection(name)

    def _build_chunk_metadata(
        self, chunk: "DocumentPageChunk", document: Document
    ) -> dict[str, Any]:
        # `document.metadata.model_dump()` is spread last so it can override per-chunk
        # defaults. `getattr` with defaults is used for optional image fields that may
        # not be present on all chunk types.
        meta = {
            "page_num": chunk.page_num,
            "chunk_num": chunk.chunk_num,
            **document.metadata.model_dump(),
        }
        return meta

    def _sanitize_id(self, document_id: str) -> str:
        """Sanitize document_id to prevent filter injection."""
        return document_id.replace('"', "").replace("\\", "")

    def _group_documents(self, results: list[dict[str, Any]]) -> list[DocumentInfo]:
        # Iterates results twice: first to record the initial occurrence of each
        # parent_doc_id (capturing filename/filesize/filetype and merging source_path,
        # content_hash, and any extra dict into additional_info), then to increment
        # chunk_count for every row belonging to that document.
        doc_map: dict[str, dict[str, Any]] = {}
        for item in results:
            doc_id = item.get("parent_doc_id")
            metadata = item.get("metadata", {})
            if doc_id and doc_id not in doc_map:
                doc_map[doc_id] = {
                    "document_id": doc_id,
                    "filename": metadata.get("filename"),
                    "filesize": metadata.get("filesize"),
                    "filetype": metadata.get("filetype"),
                    "additional_info": {
                        "source_path": metadata.get("source_path", ""),
                        "content_hash": metadata.get("content_hash", ""),
                        **(metadata.get("additional_info") or {}),
                    },
                    "chunk_count": 0,
                }
            if doc_id:
                doc_map[doc_id]["chunk_count"] += 1
        return [
            DocumentInfo(
                document_id=d["document_id"],
                filename=d.get("filename"),
                filesize=d.get("filesize"),
                filetype=d.get("filetype"),
                chunk_count=d["chunk_count"],
                additional_info=d.get("additional_info"),
            )
            for d in doc_map.values()
        ]


from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings as app_settings
from app.services.rag.config import RAGSettings
from app.services.rag.embeddings import EmbeddingService


class QdrantVectorStore(BaseVectorStore):
    """Qdrant vector store implementation."""

    def __init__(self, settings: RAGSettings, embedding_service: EmbeddingService):
        self.settings = settings
        self.embedder = embedding_service
        self.client = AsyncQdrantClient(
            host=app_settings.QDRANT_HOST,
            port=app_settings.QDRANT_PORT,
            api_key=app_settings.QDRANT_API_KEY or None,
        )

    async def _ensure_collection(self, name: str) -> None:
        collections = await self.client.get_collections()
        if name not in [c.name for c in collections.collections]:
            await self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self.settings.embeddings_config.dim,
                    distance=Distance.COSINE,
                ),
            )

    async def insert_document(self, collection_name: str, document: Document) -> None:
        await self._ensure_collection(collection_name)
        if not document.chunked_pages:
            raise ValueError("Document has no chunked pages.")
        vectors = self.embedder.embed_document(document)
        points = [
            PointStruct(
                id=chunk.chunk_id,
                vector=vectors[i],
                payload={
                    "content": chunk.chunk_content,
                    "parent_doc_id": chunk.parent_doc_id,
                    "metadata": self._build_chunk_metadata(chunk, document),
                },
            )
            for i, chunk in enumerate(document.chunked_pages)
        ]
        await self.client.upsert(collection_name=collection_name, points=points)

    async def search(
        self, collection_name: str, query: str, limit: int = 4, filter_expr: str = ""
    ) -> list[SearchResult]:
        query_vector = self.embedder.embed_query(query)
        qdrant_filter = None
        if filter_expr and "parent_doc_id" in filter_expr:
            m = re.search(r'parent_doc_id\s*==\s*"([^"]+)"', filter_expr)
            if m:
                qdrant_filter = Filter(
                    must=[FieldCondition(key="parent_doc_id", match=MatchValue(value=m.group(1)))]
                )
        results = await self.client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
            query_filter=qdrant_filter,
        )
        return [
            SearchResult(
                content=hit.payload.get("content", ""),
                score=hit.score,
                metadata=hit.payload.get("metadata", {}),
                parent_doc_id=hit.payload.get("parent_doc_id"),
            )
            for hit in results
        ]

    async def get_collection_info(self, collection_name: str) -> CollectionInfo:
        info = await self.client.get_collection(collection_name)
        return CollectionInfo(
            name=collection_name,
            total_vectors=info.points_count or 0,
            dim=self.settings.embeddings_config.dim,
        )

    async def delete_collection(self, collection_name: str) -> None:
        await self.client.delete_collection(collection_name)

    async def delete_document(self, collection_name: str, document_id: str) -> None:
        sanitized = self._sanitize_id(document_id)
        await self.client.delete(
            collection_name=collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="parent_doc_id", match=MatchValue(value=sanitized))]
                )
            ),
        )

    async def get_documents(self, collection_name: str) -> list[DocumentInfo]:
        await self._ensure_collection(collection_name)
        records, _ = await self.client.scroll(
            collection_name=collection_name, limit=10000, with_payload=True
        )
        results = [
            {
                "parent_doc_id": r.payload.get("parent_doc_id"),
                "metadata": r.payload.get("metadata", {}),
            }
            for r in records
        ]
        return self._group_documents(results)

    async def list_collections(self) -> list[str]:
        collections = await self.client.get_collections()
        return [c.name for c in collections.collections]
