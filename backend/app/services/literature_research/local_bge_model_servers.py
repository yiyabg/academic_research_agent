"""Internal HTTP services for the private local-paper BGE models.

These apps deliberately expose only Docker-network endpoints.  They keep the
large embedding and reranking models out of the API and Celery worker
processes, while still allowing those processes to use the same local model
cache without an OpenAI embedding API.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings


class EmbeddingRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=512)


class EmbeddingResponse(BaseModel):
    model: str
    dimension: int
    vectors: list[list[float]]


class TokenizeRequest(BaseModel):
    """Internal-only BGE tokenizer request used by the ingestion worker.

    The worker deliberately does not load a second copy of BGE-M3 just to
    count tokens.  Keeping tokenisation next to the embedding model makes the
    500-token child limit reproducible across API, worker and future rebuilds.
    """

    texts: list[str] = Field(min_length=1, max_length=256)


class TokenizeResponse(BaseModel):
    model: str
    token_ids: list[list[int]]


class RerankRequest(BaseModel):
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1, max_length=100)


class RerankResponse(BaseModel):
    model: str
    scores: list[float]


def _model_device(requested: str) -> str:
    """Resolve device once and forbid an accidental production CPU fallback."""
    import torch

    cuda_available = torch.cuda.is_available()
    device = (
        "cuda"
        if requested == "auto" and cuda_available
        else "cpu"
        if requested == "auto"
        else requested
    )
    if device == "cuda" and not cuda_available:
        raise RuntimeError("CUDA was requested for the local-paper BGE service but is unavailable")
    if settings.LOCAL_PAPER_REQUIRE_CUDA and device != "cuda":
        raise RuntimeError("LOCAL_PAPER_REQUIRE_CUDA=true but the BGE service is not using CUDA")
    return device


class LocalBGEEmbeddingRuntime:
    def __init__(self) -> None:
        self._model: Any | None = None

    @property
    def model(self) -> Any:
        if self._model is None:
            # Importing transformers initializes a large model registry.  Keep
            # API process startup lightweight; healthz performs the real load.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                settings.LOCAL_PAPER_EMBEDDING_MODEL,
                cache_folder=str(settings.MODELS_CACHE_DIR),
                token=settings.HF_TOKEN or None,
                device=_model_device(settings.LOCAL_PAPER_EMBEDDING_DEVICE),
            )
            dimension = self._model.get_sentence_embedding_dimension()
            if dimension != settings.LOCAL_PAPER_EMBEDDING_DIM:
                raise RuntimeError(
                    "BGE-M3 dimension mismatch: "
                    f"expected {settings.LOCAL_PAPER_EMBEDDING_DIM}, got {dimension}"
                )
        return self._model

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        raw = self.model.encode(
            list(texts),
            batch_size=settings.LOCAL_PAPER_EMBEDDING_BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [[float(value) for value in vector] for vector in raw]

    def tokenize(self, texts: Sequence[str]) -> list[list[int]]:
        tokenizer = self.model.tokenizer
        encoded = tokenizer(
            list(texts),
            add_special_tokens=False,
            truncation=False,
            padding=False,
        )
        return [[int(token) for token in ids] for ids in encoded["input_ids"]]


class LocalBGERerankerRuntime:
    def __init__(self) -> None:
        self._model: Any | None = None

    @property
    def model(self) -> Any:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                settings.LOCAL_PAPER_RERANKER_MODEL,
                cache_folder=str(settings.MODELS_CACHE_DIR),
                token=settings.HF_TOKEN or None,
                device=_model_device(settings.LOCAL_PAPER_RERANKER_DEVICE),
            )
        return self._model

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        return [float(score) for score in self.model.predict([(query, text) for text in documents])]

    def is_ready(self) -> bool:
        return self.model is not None


embedding_runtime = LocalBGEEmbeddingRuntime()
reranker_runtime = LocalBGERerankerRuntime()

embedding_app = FastAPI(title="Local Paper BGE-M3 Embedding Service", docs_url=None, redoc_url=None)
reranker_app = FastAPI(title="Local Paper BGE Reranker Service", docs_url=None, redoc_url=None)


@embedding_app.get("/healthz")
async def embedding_health() -> dict[str, object]:
    try:
        return {
            "status": "ok",
            "model": settings.LOCAL_PAPER_EMBEDDING_MODEL,
            "dimension": embedding_runtime.model.get_sentence_embedding_dimension(),
            "device": str(embedding_runtime.model.device),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@embedding_app.post("/embed", response_model=EmbeddingResponse)
async def embed(request: EmbeddingRequest) -> EmbeddingResponse:
    try:
        vectors = await asyncio.to_thread(embedding_runtime.encode, request.texts)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return EmbeddingResponse(
        model=settings.LOCAL_PAPER_EMBEDDING_MODEL,
        dimension=settings.LOCAL_PAPER_EMBEDDING_DIM,
        vectors=vectors,
    )


@embedding_app.post("/tokenize", response_model=TokenizeResponse)
async def tokenize(request: TokenizeRequest) -> TokenizeResponse:
    try:
        token_ids = await asyncio.to_thread(embedding_runtime.tokenize, request.texts)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return TokenizeResponse(model=settings.LOCAL_PAPER_EMBEDDING_MODEL, token_ids=token_ids)


@reranker_app.get("/healthz")
async def reranker_health() -> dict[str, str]:
    try:
        reranker_runtime.is_ready()
        return {
            "status": "ok",
            "model": settings.LOCAL_PAPER_RERANKER_MODEL,
            "device": str(reranker_runtime.model.device),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@reranker_app.post("/rerank", response_model=RerankResponse)
async def rerank(request: RerankRequest) -> RerankResponse:
    try:
        scores = await asyncio.to_thread(reranker_runtime.score, request.query, request.documents)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RerankResponse(model=settings.LOCAL_PAPER_RERANKER_MODEL, scores=scores)
