"""Three-stage relevance funnel with explicit model/version provenance."""

import asyncio
import math
import re
from uuid import UUID

import numpy as np
from sentence_transformers import CrossEncoder

from app.schemas.literature_research.evidence import (
    AsyncScoreModel,
    RelevanceDecision,
    RelevanceScore,
)
from app.schemas.literature_research.protocol import TopicModel
from app.services.literature_research.vector_index import TextEmbeddingProvider

_TOKEN = re.compile(r"\w+", re.UNICODE)


def lexical_overlap(query: str, document: str) -> float:
    query_tokens = set(_TOKEN.findall(query.lower()))
    document_tokens = set(_TOKEN.findall(document.lower()))
    if not query_tokens:
        return 0.0
    return min(1.0, len(query_tokens & document_tokens) / len(query_tokens))


class EmbeddingCosineScoreModel:
    def __init__(self, embedding_provider: TextEmbeddingProvider, model_name: str) -> None:
        self.embedding_provider = embedding_provider
        self.version = model_name

    async def score(self, query: str, documents: list[str]) -> list[float]:
        vectors = await asyncio.to_thread(
            self.embedding_provider.embed_queries, [query, *documents]
        )
        query_vector = np.asarray(vectors[0], dtype=float)
        query_norm = np.linalg.norm(query_vector)
        scores = []
        for raw in vectors[1:]:
            vector = np.asarray(raw, dtype=float)
            denominator = query_norm * np.linalg.norm(vector)
            cosine = float(np.dot(query_vector, vector) / denominator) if denominator else 0.0
            scores.append(max(0.0, min(1.0, (cosine + 1.0) / 2.0)))
        return scores


class CrossEncoderScoreModel:
    def __init__(self, model_name: str, cache_dir: str) -> None:
        self.version = model_name
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._model: CrossEncoder | None = None

    @property
    def model(self) -> CrossEncoder:
        if self._model is None:
            self._model = CrossEncoder(self._model_name, cache_folder=self._cache_dir)
        return self._model

    async def score(self, query: str, documents: list[str]) -> list[float]:
        raw = await asyncio.to_thread(
            self.model.predict, [(query, document) for document in documents]
        )
        return [1.0 / (1.0 + math.exp(-float(value))) for value in raw]


class RelevanceScoringService:
    def __init__(
        self,
        *,
        semantic_model: AsyncScoreModel | None = None,
        cross_encoder: AsyncScoreModel | None = None,
        lexical_floor: float = 0.15,
        semantic_floor: float = 0.45,
        cross_floor: float = 0.55,
    ) -> None:
        self.semantic_model = semantic_model
        self.cross_encoder = cross_encoder
        self.lexical_floor = lexical_floor
        self.semantic_floor = semantic_floor
        self.cross_floor = cross_floor

    async def score(
        self,
        *,
        query: str,
        topic_model: TopicModel,
        documents: list[tuple[UUID, str]],
    ) -> list[RelevanceScore]:
        lexical = [lexical_overlap(query, text) for _, text in documents]
        semantic: list[float | None]
        if self.semantic_model:
            semantic = list(await self.semantic_model.score(query, [text for _, text in documents]))
        else:
            semantic = [None] * len(documents)
        cross_candidates = [
            index
            for index, score in enumerate(lexical)
            if score >= self.lexical_floor
            and (
                (semantic_score := semantic[index]) is None or semantic_score >= self.semantic_floor
            )
        ]
        cross_values: dict[int, float] = {}
        if self.cross_encoder and cross_candidates:
            scores = await self.cross_encoder.score(
                query, [documents[index][1] for index in cross_candidates]
            )
            cross_values = dict(zip(cross_candidates, scores, strict=True))

        results = []
        for index, (work_id, text) in enumerate(documents):
            semantic_score = semantic[index]
            facet_scores = {
                facet.facet_id: lexical_overlap(facet.name, text)
                for facet in topic_model.must_have_facets
            }
            reasons = []
            if lexical[index] < self.lexical_floor:
                decision = RelevanceDecision.FAIL
                reasons.append("LEXICAL_FLOOR_NOT_MET")
            elif semantic_score is not None and semantic_score < self.semantic_floor:
                decision = RelevanceDecision.FAIL
                reasons.append("SEMANTIC_FLOOR_NOT_MET")
            elif self.cross_encoder is None:
                decision = RelevanceDecision.REVIEW
                reasons.append("CROSS_ENCODER_UNAVAILABLE")
            elif cross_values.get(index, 0) < self.cross_floor:
                decision = RelevanceDecision.FAIL
                reasons.append("CROSS_ENCODER_FLOOR_NOT_MET")
            elif any(
                facet_scores[item.facet_id] < item.minimum_score
                for item in topic_model.must_have_facets
            ):
                decision = RelevanceDecision.FAIL
                reasons.append("MUST_HAVE_FACET_NOT_MET")
            else:
                decision = RelevanceDecision.PASS
            versions = {}
            if self.semantic_model:
                versions["semantic"] = self.semantic_model.version
            if self.cross_encoder:
                versions["cross_encoder"] = self.cross_encoder.version
            results.append(
                RelevanceScore(
                    work_id=work_id,
                    lexical_score=lexical[index],
                    semantic_score=semantic_score,
                    cross_encoder_score=cross_values.get(index),
                    facet_scores=facet_scores,
                    decision=decision,
                    model_versions=versions,
                    reasons=reasons,
                )
            )
        return results
