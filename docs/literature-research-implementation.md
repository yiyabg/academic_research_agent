# Literature research implementation map

## Baseline

- Template commit: `3428d9a6214619d3514312886d59a36400747b7d`
- Generator: `fastapi-fullstack` 0.2.19
- Runtime: CPython 3.12 (LiteParse does not publish CPython 3.13 wheels)
- Generated stack: FastAPI, SQLAlchemy 2, PostgreSQL, Redis/Celery, PydanticAI,
  Qdrant RAG, CrossEncoder, PyMuPDF/LiteParse/LlamaParse, S3/MinIO, Next.js.

The project was generated independently as `academic_research_agent`. The
existing `shopping_agent` is not a development target.

## What the template already implements

The backend follows route → service → repository → SQLAlchemy model. Its DB
session commits at the request boundary; repositories flush but do not commit.
Celery provides late acknowledgements and Redis transport. The API supports JWT
and API-key authentication. The existing WebSocket is optimized for chat turns,
and the existing `services/research.py` plus frontend `research-store.ts` model
generic Deep Research subagent activity. RAG parses, chunks, embeds, reranks,
and stores documents in Qdrant, but its generated collections are globally
shared and therefore cannot be used as the tenant-isolated paper truth source.

## Domain boundary added by this project

All paper workflow code lives under `literature_research` namespaces. The first
release uses PostgreSQL as the workflow truth source and an event outbox for
replayable progress. A run binds exactly one immutable approved protocol version.
Each stage has explicit preconditions, an idempotency hash, and a legal next
state. Celery invokes stages but cannot infer or own state.

The pipeline is implemented in the design order:

1. protocol compilation, validation, approval, and run skeleton;
2. scholarly discovery, raw snapshots, normalization, and version-aware dedup;
3. authorized venue metrics and a fail-closed constraint ledger;
4. multi-stage relevance, legal full text, parsing, and evidence spans;
5. bounded expert analysis, claim audit, stable Markdown/OPML/BibTeX/manifest;
6. project/user memory, feedback, evaluation, and operational hardening.

## Non-negotiable invariants

- Approved protocol JSON is never mutated in place.
- `selected` implies all applicable hard constraints passed.
- `UNKNOWN` never becomes `PASS` implicitly.
- Conference and journal quality policies are separate.
- Shortfall never triggers automatic relaxation.
- Every factual claim references evidence from the same work version.
- Original source records and file hashes remain available for audit.
- WebSocket events are a projection of persisted, monotonically sequenced outbox
  events and can be replayed after disconnect.
