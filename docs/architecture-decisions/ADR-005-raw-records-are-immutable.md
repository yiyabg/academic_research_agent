# ADR-005: Raw source records are immutable

Status: Accepted (2026-08-21)

Every scholarly source response is stored with request fingerprint, retrieval
time, status, content hash, and object key. Normalization selects a canonical
field value while retaining all candidates and the resolution rule. Deduplication
links records and versions; it does not overwrite or delete source evidence.
