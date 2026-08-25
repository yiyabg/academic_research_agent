# ADR-003: Agents do not own the workflow state machine

Status: Accepted (2026-08-21)

The application service controls state transitions, retries, idempotency,
selection, and release. Celery executes bounded stage work. PydanticAI experts
only produce schema-validated semantic outputs such as topic facets, relevance
judgments, analysis sections, figure interpretations, audits, and synthesis.

Agents cannot change approved thresholds, mark a paper selected, write workflow
state directly, or publish artifacts.
