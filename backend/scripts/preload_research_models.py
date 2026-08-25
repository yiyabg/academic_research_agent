"""Preload the dedicated local-paper BGE models into the shared Docker volume."""

from sentence_transformers import CrossEncoder, SentenceTransformer

from app.core.config import settings


def main() -> None:
    cache_dir = str(settings.MODELS_CACHE_DIR)
    print(f"preloading local-paper BGE embedding {settings.LOCAL_PAPER_EMBEDDING_MODEL}")
    embedding = SentenceTransformer(
        settings.LOCAL_PAPER_EMBEDDING_MODEL,
        cache_folder=cache_dir,
        token=settings.HF_TOKEN or None,
    )
    vectors = embedding.encode(["auditable scholarly retrieval"], normalize_embeddings=True)
    if len(vectors[0]) != settings.LOCAL_PAPER_EMBEDDING_DIM:
        raise RuntimeError(
            f"BGE embedding dimension mismatch: expected {settings.LOCAL_PAPER_EMBEDDING_DIM}, "
            f"got {len(vectors[0])}"
        )
    print(f"preloading local-paper BGE reranker {settings.LOCAL_PAPER_RERANKER_MODEL}")
    cross_encoder = CrossEncoder(settings.LOCAL_PAPER_RERANKER_MODEL, cache_folder=cache_dir)
    cross_encoder.predict([("research agent", "auditable scholarly retrieval")])
    print("research models ready")


if __name__ == "__main__":
    main()
