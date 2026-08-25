# ADR-004: Venue metrics require authorized snapshots

Status: Accepted (2026-08-21)

JIF and journal partitions are accepted only from an organization-authorized,
versioned snapshot or API. Every value records metric year, source, retrieval
time, license scope, and source hash. A missing or conflicted metric is
`UNKNOWN` and cannot satisfy a hard constraint.

Conference quality uses an independent allowlist/ranking policy; conferences
must never be assigned JIF or journal partitions.
