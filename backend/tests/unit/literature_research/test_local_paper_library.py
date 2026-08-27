import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import fitz
import pytest
from pydantic import ValidationError

from app.db.models.local_paper_library import (
    LocalPaper,
    LocalPaperChunk,
    LocalPaperLibrary,
    LocalPaperSection,
)
from app.schemas.literature_research.local_library import (
    LocalPaperAskRequest,
    LocalPaperSearchRequest,
)
from app.services.literature_research import local_bge_model_servers
from app.services.literature_research.local_bge_model_servers import (
    EmbeddingRequest,
    RerankRequest,
    _model_device,
)
from app.services.literature_research.local_paper_library import (
    GroundedAnswer,
    GroundedClaim,
    LocalPaperLibraryService,
    _bm25_tokens,
    _cap_chunks_per_paper,
    _is_substantive_retrieval_chunk,
    _local_index_version,
    _local_paper_sync_event_payload,
    _render_grounded_answer,
    _rrf_fuse,
    _SafeHTMLText,
    _select_page_figure_boxes,
    _strip_null,
    _unique_figure_boxes,
    attachment_paths,
    extract_structured_source,
    parse_bibtex,
)
from app.services.literature_research.local_paper_vector_index import (
    BGEEmbeddingHTTPClient,
    LocalPaperVectorChunk,
    LocalPaperVectorIndex,
)


class FixedLocalEmbedder:
    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 0.0] for text in texts]


class RecordingLocalEmbedder(FixedLocalEmbedder):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return super().embed_queries(texts)


def test_sync_event_payload_is_plain_and_does_not_depend_on_orm_state() -> None:
    run_id = uuid4()
    summary = {"sequence": 7, "processed": 4, "stage": "INDEXING"}

    payload = _local_paper_sync_event_payload(
        sync_run_id=run_id,
        status="RUNNING",
        summary=summary,
        error_message=None,
    )

    assert payload["type"] == "local_paper_sync_event"
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["sync_run_id"] == str(run_id)
    assert data["status"] == "RUNNING"
    assert data["summary_json"] == summary
    assert data["error_message"] is None
    assert isinstance(data["updated_at"], str)


def test_pdf_text_sanitization_replaces_lone_utf16_surrogates() -> None:
    assert _strip_null("before\ud800after\x00") == "before\ufffdafter"


def test_bge_device_resolution_refuses_cuda_fallback(monkeypatch) -> None:
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(local_bge_model_servers.settings, "LOCAL_PAPER_REQUIRE_CUDA", False)

    assert _model_device("auto") == "cpu"
    with pytest.raises(RuntimeError, match="CUDA was requested"):
        _model_device("cuda")

    monkeypatch.setattr(local_bge_model_servers.settings, "LOCAL_PAPER_REQUIRE_CUDA", True)
    with pytest.raises(RuntimeError, match="LOCAL_PAPER_REQUIRE_CUDA"):
        _model_device("auto")


def test_better_bibtex_attachment_paths_accept_pdf_and_static_html_only() -> None:
    entries = parse_bibtex(
        """@article{paper_key,
          title = {Safe local paper},
          file = {PDF:files/1/paper.pdf:application/pdf;HTML:files/2/article.html:text/html},
        }"""
    )

    assert len(entries) == 1
    assert entries[0].citekey == "paper_key"
    assert attachment_paths(entries[0]) == ["files/1/paper.pdf", "files/2/article.html"]


def test_grounded_question_requires_an_explicit_paper_scope() -> None:
    with pytest.raises(ValidationError):
        LocalPaperAskRequest(question="compare these papers")

    request = LocalPaperAskRequest(question="compare these papers", paper_ids=[uuid4()])
    assert len(request.paper_ids) == 1


def test_figure_boxes_and_rerank_candidates_are_deduplicated_per_paper() -> None:
    assert _unique_figure_boxes([[10.0, 10.0, 100.0, 100.0], [10.1, 10.1, 100.1, 100.1]]) == [
        [10.0, 10.0, 100.0, 100.0]
    ]

    first_paper, second_paper = uuid4(), uuid4()

    def candidate(paper_id):
        return SimpleNamespace(chunk=SimpleNamespace(paper_id=paper_id))

    selected = _cap_chunks_per_paper(
        [
            candidate(first_paper),
            candidate(first_paper),
            candidate(first_paper),
            candidate(second_paper),
        ],
        limit=4,
        max_per_paper=2,
    )
    assert [item.chunk.paper_id for item in selected] == [first_paper, first_paper, second_paper]


def test_short_headers_and_visual_labels_do_not_become_generic_evidence() -> None:
    long_parent = "The parent contains a substantive method explanation. " * 10
    header = SimpleNamespace(
        chunk=SimpleNamespace(chunk_kind="text", content="MULTI-AGENT COMMUNICATION 10075"),
        parent=SimpleNamespace(content=long_parent),
    )
    figure_label = SimpleNamespace(
        chunk=SimpleNamespace(chunk_kind="figure_ocr", content="FIGURE 6. Communication environment."),
        parent=SimpleNamespace(content="FIGURE 6. Communication environment."),
    )
    passage = SimpleNamespace(
        chunk=SimpleNamespace(
            chunk_kind="text",
            content="A sufficiently detailed passage explaining how multi-agent communication "
            "changes the task-allocation architecture and its coordination overhead. " * 2,
        ),
        parent=SimpleNamespace(content=long_parent),
    )
    bibliography = SimpleNamespace(
        chunk=SimpleNamespace(
            chunk_kind="text",
            content="A long bibliographic entry mentioning multi-agent communication. " * 4,
        ),
        parent=SimpleNamespace(content=long_parent, heading="R EFERENCES"),
    )

    header.parent.heading = "Method"
    figure_label.parent.heading = "Results"
    passage.parent.heading = "Method"

    assert not _is_substantive_retrieval_chunk(header, query="multi-agent communication")
    assert not _is_substantive_retrieval_chunk(figure_label, query="multi-agent communication")
    assert _is_substantive_retrieval_chunk(figure_label, query="Figure 6 communication")
    assert _is_substantive_retrieval_chunk(passage, query="multi-agent communication")
    assert not _is_substantive_retrieval_chunk(bibliography, query="multi-agent communication")


def test_figure_inventory_ignores_tiny_resources_and_bounds_ocr_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_MIN_FIGURE_AREA_RATIO",
        0.1,
    )
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_MAX_FIGURES_PER_PAGE",
        2,
    )
    page = SimpleNamespace(rect=SimpleNamespace(width=100.0, height=100.0))

    selected = _select_page_figure_boxes(
        page,
        [
            [1.0, 1.0, 5.0, 5.0],  # resource/glyph: below 10% page area
            [50.0, 50.0, 100.0, 100.0],
            [0.0, 0.0, 40.0, 40.0],
            [0.1, 0.1, 40.1, 40.1],  # duplicate detection box
        ],
    )

    assert selected == [[0.0, 0.0, 40.0, 40.0], [50.0, 50.0, 100.0, 100.0]]


def test_grounded_answer_cannot_reference_nonexistent_evidence() -> None:
    valid = GroundedAnswer(
        answer="The mechanisms differ.",
        claims=[GroundedClaim(text="One uses a graph.", citation_ids=[1])],
    )
    invalid = GroundedAnswer(
        answer="The mechanisms differ.",
        claims=[GroundedClaim(text="One uses a graph.", citation_ids=[2])],
    )

    assert _render_grounded_answer(valid, 1) is not None
    assert _render_grounded_answer(invalid, 1) is None


def test_html_extractor_never_includes_script_or_style_content() -> None:
    parser = _SafeHTMLText()
    parser.feed(
        "<article><h1>Paper</h1><script>steal()</script><style>.x{}</style><p>Evidence text</p></article>"
    )
    parser.close()

    assert "Paper" in parser.text()
    assert "Evidence text" in parser.text()
    assert "steal" not in parser.text()
    assert ".x" not in parser.text()


def test_local_vector_index_accepts_only_the_explicit_local_embedder() -> None:
    embedder = FixedLocalEmbedder()
    index = LocalPaperVectorIndex(embedder=embedder, dimension=2)

    assert index.embedder is embedder
    assert index.dimension == 2


def test_local_vector_index_defaults_to_internal_bge_http_client() -> None:
    index = LocalPaperVectorIndex(client=SimpleNamespace())

    assert isinstance(index.embedder, BGEEmbeddingHTTPClient)
    assert index.dimension == 1024


def test_internal_bge_http_apps_load_only_their_own_python_models(monkeypatch) -> None:
    class EmbeddingModel:
        def get_sentence_embedding_dimension(self) -> int:
            return 1024

        def encode(self, texts, **_kwargs):
            return [[float(index)] * 1024 for index, _text in enumerate(texts, 1)]

    class RerankerModel:
        def predict(self, pairs):
            return [0.25 + index for index, _pair in enumerate(pairs)]

    monkeypatch.setattr(local_bge_model_servers.embedding_runtime, "_model", EmbeddingModel())
    monkeypatch.setattr(local_bge_model_servers.reranker_runtime, "_model", RerankerModel())

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(local_bge_model_servers.asyncio, "to_thread", immediate)

    embedding_response = asyncio.run(
        local_bge_model_servers.embed(EmbeddingRequest(texts=["first", "second"]))
    )
    reranker_response = asyncio.run(
        local_bge_model_servers.rerank(RerankRequest(query="query", documents=["first", "second"]))
    )

    assert embedding_response.dimension == 1024
    assert len(embedding_response.vectors[0]) == 1024
    assert reranker_response.scores == [0.25, 1.25]


def test_structured_pdf_extraction_preserves_parent_section_paragraph_and_bbox(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_REQUIRE_DOCLING",
        False,
    )
    path = tmp_path / "paper.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "1 Introduction", fontsize=15)
    page.insert_text((72, 100), "First paragraph contains retrieval evidence.", fontsize=10)
    page.insert_text((72, 130), "Second paragraph stays structurally separate.", fontsize=10)
    document.save(path)
    document.close()

    source = extract_structured_source(path)

    assert source.sections
    section = source.sections[0]
    assert section.page_number == 1
    assert section.heading == "1 Introduction"
    assert len(section.paragraphs) == 2
    assert section.paragraphs[0].bbox is not None
    assert "Second paragraph" in section.content


def test_structured_source_pages_keep_heading_for_deep_analysis_extractors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_REQUIRE_DOCLING",
        False,
    )
    path = tmp_path / "sections.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Abstract", fontsize=15)
    page.insert_text((72, 100), "This abstract is retained with its heading.", fontsize=10)
    page.insert_text((72, 150), "1 Introduction", fontsize=15)
    page.insert_text((72, 178), "This introduction is structurally separated.", fontsize=10)
    document.save(path)
    document.close()

    source = extract_structured_source(path)
    page_text = source.pages[0][1]

    assert "Abstract\nThis abstract is retained" in page_text
    assert "1 Introduction\nThis introduction" in page_text


def test_figure_is_cropped_for_ocr_and_persistable_as_location_evidence(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_REQUIRE_DOCLING",
        False,
    )
    path = tmp_path / "figure.pdf"
    # Create valid pixels through PyMuPDF itself so the test is codec-independent.
    png = fitz.Pixmap(fitz.csRGB, 2, 2, b"\xff\xff\xff" * 4, False).tobytes("png")
    document = fitz.open()
    page = document.new_page()
    page.insert_image(fitz.Rect(100, 100, 240, 220), stream=png)
    document.save(path)
    document.close()

    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_OCR_MIN_TEXT_CHARS",
        0,
    )
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_ENABLE_FIGURE_OCR",
        True,
    )
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=b"x axis: accuracy"),
    )

    source = extract_structured_source(path)

    assert len(source.figures) == 1
    assert source.figures[0].bbox == [100.0, 100.0, 240.0, 220.0]
    assert source.figures[0].ocr_text == "x axis: accuracy"
    assert source.figures[0].image_sha256


def test_figure_caption_ocr_and_single_body_reference_share_figure_index(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_REQUIRE_DOCLING",
        False,
    )
    path = tmp_path / "figure-links.pdf"
    png = fitz.Pixmap(fitz.csRGB, 2, 2, b"\xff\xff\xff" * 4, False).tobytes("png")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "As shown in Fig. 1, retrieval improves.", fontsize=10)
    page.insert_image(fitz.Rect(100, 100, 240, 220), stream=png)
    page.insert_text((100, 240), "Figure 1. Retrieval accuracy.", fontsize=10)
    document.save(path)
    document.close()

    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_OCR_MIN_TEXT_CHARS",
        0,
    )
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.settings.LOCAL_PAPER_ENABLE_FIGURE_OCR",
        True,
    )
    monkeypatch.setattr(
        "app.services.literature_research.local_paper_library.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=b"x axis: accuracy"),
    )

    source = extract_structured_source(path)
    paragraphs = [paragraph for section in source.sections for paragraph in section.paragraphs]

    assert source.figures[0].figure_label == "1"
    assert any(
        paragraph.figure_index == 0 and "Fig. 1" in paragraph.text for paragraph in paragraphs
    )
    assert any(
        paragraph.figure_index == 0 and "Figure 1" in paragraph.text for paragraph in paragraphs
    )
    assert any(
        paragraph.figure_index == 0 and "x axis" in paragraph.text for paragraph in paragraphs
    )


def test_rrf_uses_rank_not_incomparable_raw_scores() -> None:
    first, second = uuid4(), uuid4()
    fused = _rrf_fuse(
        dense=[(first, 0.1, [1.0, 0.0]), (second, 0.99, [0.0, 1.0])],
        bm25=[(first, 999.0)],
        rrf_k=60,
    )

    assert fused[first][0] > fused[second][0]
    assert fused[first][1] == 0.1
    assert fused[first][2] == 999.0


def test_qdrant_dense_query_filters_by_postgres_eligible_paper_ids(monkeypatch) -> None:
    class Client:
        def __init__(self) -> None:
            self.query_kwargs = None

        async def get_collections(self):
            return SimpleNamespace(collections=[])

        async def create_collection(self, **_kwargs):
            return None

        async def query_points(self, **kwargs):
            self.query_kwargs = kwargs
            return SimpleNamespace(points=[])

    client = Client()
    index = LocalPaperVectorIndex(client=client, embedder=FixedLocalEmbedder(), dimension=2)
    first, second = uuid4(), uuid4()

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(
        "app.services.literature_research.local_paper_vector_index.asyncio.to_thread", immediate
    )

    asyncio.run(
        index.search(collection="local_test", query="evidence", limit=10, paper_ids=[first, second])
    )

    condition = client.query_kwargs["query_filter"].must[0]
    assert condition.key == "paper_id"
    assert set(condition.match.any) == {str(first), str(second)}
    assert client.query_kwargs["with_vectors"] is True


@pytest.mark.anyio
async def test_qdrant_upsert_carries_child_and_parent_location_ids(monkeypatch) -> None:
    class Client:
        def __init__(self) -> None:
            self.points = []

        async def get_collections(self):
            return SimpleNamespace(collections=[])

        async def create_collection(self, **_kwargs):
            return None

        async def delete(self, **_kwargs):
            return None

        async def upsert(self, **kwargs):
            self.points = kwargs["points"]

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(
        "app.services.literature_research.local_paper_vector_index.asyncio.to_thread", immediate
    )
    client = Client()
    index = LocalPaperVectorIndex(client=client, embedder=FixedLocalEmbedder(), dimension=2)
    paper_id, section_id, chunk_id, figure_id = uuid4(), uuid4(), uuid4(), uuid4()

    await index.replace_paper_chunks(
        collection="local_test",
        paper_id=paper_id,
        chunks=[
            LocalPaperVectorChunk(
                chunk_id=chunk_id,
                paper_id=paper_id,
                section_id=section_id,
                page_number=7,
                chunk_index=3,
                paragraph_index=2,
                heading="Experiment",
                content="small child evidence",
                figure_id=figure_id,
            )
        ],
    )

    payload = client.points[0].payload
    assert payload["chunk_id"] == str(chunk_id)
    assert payload["section_id"] == str(section_id)
    assert payload["page_number"] == 7
    assert payload["figure_id"] == str(figure_id)


@pytest.mark.anyio
async def test_qdrant_upsert_batches_long_paper_children_without_reordering(monkeypatch) -> None:
    class Client:
        def __init__(self) -> None:
            self.points = []

        async def get_collections(self):
            return SimpleNamespace(collections=[])

        async def create_collection(self, **_kwargs):
            return None

        async def delete(self, **_kwargs):
            return None

        async def upsert(self, **kwargs):
            self.points = kwargs["points"]

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(
        "app.services.literature_research.local_paper_vector_index.asyncio.to_thread", immediate
    )
    client = Client()
    embedder = RecordingLocalEmbedder()
    index = LocalPaperVectorIndex(
        client=client, embedder=embedder, dimension=2, embedding_batch_size=2
    )
    paper_id, section_id = uuid4(), uuid4()
    chunks = [
        LocalPaperVectorChunk(
            chunk_id=uuid4(),
            paper_id=paper_id,
            section_id=section_id,
            page_number=1,
            chunk_index=chunk_index,
            paragraph_index=chunk_index,
            heading="Method",
            content=text,
        )
        for chunk_index, text in enumerate(["first", "second", "third"])
    ]

    await index.replace_paper_chunks(collection="local_test", paper_id=paper_id, chunks=chunks)

    assert embedder.calls == [["first", "second"], ["third"]]
    assert [point.payload["chunk_id"] for point in client.points] == [
        str(chunk.chunk_id) for chunk in chunks
    ]


class _FakeIndex:
    def __init__(self, point):
        self.point = point
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        return [self.point]

    async def fetch_chunk_vectors(self, **_kwargs):
        return {}


class _FakeReranker:
    def __init__(self):
        self.calls = []

    async def score(self, *, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        return [0.91 for _ in documents]


@pytest.mark.anyio
async def test_hybrid_service_executes_metadata_filtered_dense_bm25_rrf_and_bge() -> None:
    owner_id, library_id, paper_id, version_id, section_id, chunk_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    library = LocalPaperLibrary(
        id=library_id,
        owner_id=owner_id,
        source_root="/zotero_local_database",
        qdrant_collection="local_test",
        status="READY",
    )
    paper = LocalPaper(
        id=paper_id,
        library_id=library_id,
        citekey="paper",
        doi="10.1/test",
        title="Hybrid Retrieval Paper",
        authors_json=["Author"],
        publication_year=2025,
        bibtex_type="article",
        relative_source_path="files/paper.pdf",
        source_kind="pdf",
        source_sha256="a" * 64,
        ingestion_version=_local_index_version(),
        bibtex_entry="@article{paper}",
        active_document_version_id=version_id,
    )
    section = LocalPaperSection(
        id=section_id,
        paper_id=paper_id,
        document_version_id=version_id,
        page_number=4,
        section_index=0,
        heading="3 Method",
        heading_level=1,
        content="Large parent section with the complete method context.",
        bbox_json=[10, 20, 300, 500],
        section_sha256="b" * 64,
    )
    chunk = LocalPaperChunk(
        id=chunk_id,
        paper_id=paper_id,
        document_version_id=version_id,
        section_id=section_id,
        page_number=4,
        chunk_index=2,
        paragraph_index=1,
        heading="3 Method",
        bbox_json=[10, 100, 300, 180],
        chunk_kind="text",
        content="The method uses hybrid retrieval evidence.",
        content_sha256="c" * 64,
    )
    point = SimpleNamespace(
        payload={"chunk_id": str(chunk_id), "paper_id": str(paper_id)},
        score=0.82,
        vector=[1.0, 0.0],
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.scalar = AsyncMock(return_value=library)
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [paper]))
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(id=chunk_id, lexical_terms="hybrid retrieval evidence")
                ]
            ),
            SimpleNamespace(all=lambda: [(chunk, section)]),
        ]
    )
    index, reranker = _FakeIndex(point), _FakeReranker()
    service = LocalPaperLibraryService(db, index=index, reranker=reranker)

    result = await service.search(
        owner_id=owner_id,
        request=LocalPaperSearchRequest(query="hybrid retrieval", author="Author", limit=5),
    )

    assert result.retrieval_mode == "hybrid"
    assert result.items[0].id == paper_id
    assert result.items[0].evidence[0].parent_text == section.content
    assert result.items[0].evidence[0].rerank_score == 0.91
    assert index.calls[0]["paper_ids"] == [paper_id]
    assert reranker.calls[0][0] == "hybrid retrieval"
    assert _bm25_tokens("中文检索")
