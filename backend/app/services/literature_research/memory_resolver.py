"""Five-layer memory precedence that cannot mutate approved hard semantics."""

from typing import Any

from app.schemas.literature_research.memory import ResolvedMemoryContext

FORBIDDEN_MEMORY_KEYS = {
    "constraints",
    "time_scope",
    "quantity_policy",
    "quality_floor",
    "approved_protocol_hash",
}


def resolve_memory_context(
    *,
    current_input: dict[str, Any],
    approved_protocol: dict[str, Any],
    project_memory: dict[str, Any],
    user_profile: dict[str, Any],
    policy: dict[str, Any],
    defaults: dict[str, Any],
) -> ResolvedMemoryContext:
    values: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    ignored = []
    layers = [
        ("system_default", defaults, False),
        ("policy_version", policy, False),
        ("confirmed_user_profile", user_profile, False),
        ("project_memory", project_memory, False),
        ("approved_protocol", approved_protocol, True),
        ("current_input", current_input, True),
    ]
    for source, layer, trusted_for_hard_semantics in layers:
        for key, value in layer.items():
            if key in FORBIDDEN_MEMORY_KEYS and not trusted_for_hard_semantics:
                ignored.append(f"{source}:{key}")
                continue
            values[key] = value
            provenance[key] = source
    return ResolvedMemoryContext(values=values, provenance=provenance, ignored=sorted(ignored))
