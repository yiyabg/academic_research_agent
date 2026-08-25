"""Qdrant payload isolation tests for research evidence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.literature_research.evidence_locator import build_parsed_block
from app.services.literature_research.vector_index import (
    LocalSentenceTransformerProvider,
    ResearchVectorIndex,
    get_research_embedding_provider,
)


class FixedEmbedder:
    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 0.5] for text in texts]


def test_project_llm_key_does_not_switch_research_embeddings_to_openai() -> None:
    with (
        patch(
            "app.services.literature_research.vector_index.settings."
            "RESEARCH_EMBEDDING_PROVIDER",
            "local",
        ),
        patch(
            "app.services.literature_research.vector_index.settings.OPENAI_API_KEY",
            "responses-gateway-fixture",
        ),
    ):
        provider, dimension, version = get_research_embedding_provider()

    assert isinstance(provider, LocalSentenceTransformerProvider)
    assert dimension == 384
    assert version == "sentence-transformers/all-MiniLM-L6-v2"


def test_empty_qdrant_api_key_keeps_internal_connection_on_http() -> None:
    with (
        patch("app.services.literature_research.vector_index.settings.QDRANT_HOST", "qdrant"),
        patch("app.services.literature_research.vector_index.settings.QDRANT_PORT", 6333),
        patch("app.services.literature_research.vector_index.settings.QDRANT_API_KEY", ""),
    ):
        index = ResearchVectorIndex(embedder=FixedEmbedder(), dimension=2)

    assert index.client._client.rest_uri == "http://qdrant:6333"


@pytest.mark.anyio
async def test_upsert_payload_and_search_filter_bind_tenant_project_and_run() -> None:
    client = AsyncMock()
    client.get_collections.return_value = SimpleNamespace(collections=[])
    client.query_points.return_value = SimpleNamespace(points=[])
    index = ResearchVectorIndex(client=client, embedder=FixedEmbedder(), dimension=2)
    organization_id, project_id, run_id, work_id, version_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    block = build_parsed_block(block_id="p1", text="audit evidence", char_start=0)

    await index.upsert_blocks(
        organization_id=organization_id,
        project_id=project_id,
        run_id=run_id,
        work_id=work_id,
        version_id=version_id,
        blocks=[block],
    )
    point = client.upsert.await_args.kwargs["points"][0]
    assert point.payload["tenant_id"] == str(organization_id)
    assert point.payload["project_id"] == str(project_id)
    assert point.payload["run_id"] == str(run_id)

    await index.search(
        organization_id=organization_id,
        project_id=project_id,
        run_id=run_id,
        query="audit",
    )
    conditions = client.query_points.await_args.kwargs["query_filter"].must
    assert {(item.key, item.match.value) for item in conditions} == {
        ("tenant_id", str(organization_id)),
        ("project_id", str(project_id)),
        ("run_id", str(run_id)),
    }


@pytest.mark.anyio
async def test_project_memory_index_uses_separate_tenant_project_namespace() -> None:
    client = AsyncMock()
    client.get_collections.return_value = SimpleNamespace(collections=[])
    client.query_points.return_value = SimpleNamespace(points=[])
    index = ResearchVectorIndex(client=client, embedder=FixedEmbedder(), dimension=2)
    organization_id, project_id, memory_id = uuid4(), uuid4(), uuid4()

    await index.upsert_project_memory(
        organization_id=organization_id,
        project_id=project_id,
        memory_id=memory_id,
        memory_type="CORRECTION",
        content='{"decision":"EXCLUDE"}',
        source="USER_FEEDBACK",
    )
    upsert_call = client.upsert.await_args.kwargs
    assert upsert_call["collection_name"].startswith("research_memory_")
    point = upsert_call["points"][0]
    assert point.payload["tenant_id"] == str(organization_id)
    assert point.payload["project_id"] == str(project_id)
    assert point.payload["memory_id"] == str(memory_id)

    await index.search_project_memories(
        organization_id=organization_id,
        project_id=project_id,
        query="exclude this paper",
    )
    conditions = client.query_points.await_args.kwargs["query_filter"].must
    assert {(item.key, item.match.value) for item in conditions} == {
        ("tenant_id", str(organization_id)),
        ("project_id", str(project_id)),
    }
