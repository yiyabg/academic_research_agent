# ADR-002: Quantity never overrides the quality floor

Status: Accepted (2026-08-21)

`target_count` is a target, not permission to relax constraints. Approved hard
constraints are frozen by a canonical protocol hash. A paper is selectable only
when every applicable hard constraint is `PASS`; `UNKNOWN` fails closed.

If fewer papers qualify, the run emits a shortfall report. Relaxation requires
an explicit user-approved new protocol version, preserving the original run and
its results.
