# ADR-001: PostgreSQL is the business truth source

Status: Accepted (2026-08-21)

Research protocols, runs, state transitions, candidate decisions, evidence,
analyses, event outbox records, and artifact metadata are authoritative only in
PostgreSQL. Redis is limited to Celery transport, rate limiting, short leases,
and disposable caches. Qdrant stores searchable embeddings plus identifiers,
never the only copy of a research fact. S3/MinIO stores immutable source files
and artifacts whose hashes and ownership remain in PostgreSQL.

This lets a worker restart, WebSocket disconnect, or vector index rebuild occur
without changing research decisions or losing provenance.
