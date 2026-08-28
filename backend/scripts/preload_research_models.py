"""Preload immutable local-paper retrieval and parser models before runtime."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sentence_transformers import CrossEncoder, SentenceTransformer

from app.core.config import settings


def _preload_docling_artifacts(cache_dir: Path) -> Path:
    """Use Docling's supported CLI to make its offline artifact layout."""
    artifacts_dir = cache_dir / "docling-v7"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    print(f"preloading Docling v7 artifacts into {artifacts_dir}")
    subprocess.run(
        [
            "docling-tools",
            "models",
            "download",
            "--output-dir",
            str(artifacts_dir),
            "layout",
            "tableformer",
        ],
        check=True,
    )
    layout_dirs = [
        path for path in artifacts_dir.iterdir() if path.is_dir() and "layout" in path.name
    ]
    table_model = (
        artifacts_dir / "docling-project--docling-models" / "model_artifacts" / "tableformer"
    )
    if not any((path / "config.json").is_file() for path in layout_dirs):
        raise RuntimeError("Docling layout artifact is missing config.json after prefetch")
    if not table_model.exists():
        raise RuntimeError("Docling TableFormer artifact is missing after prefetch")
    return artifacts_dir


def main() -> None:
    cache_dir_path = Path(settings.MODELS_CACHE_DIR)
    cache_dir = str(cache_dir_path)
    artifacts_dir = _preload_docling_artifacts(cache_dir_path)
    os.environ["DOCLING_SERVE_ARTIFACTS_PATH"] = str(artifacts_dir)
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
    print(f"research models ready; Docling artifacts: {artifacts_dir}")


if __name__ == "__main__":
    main()
