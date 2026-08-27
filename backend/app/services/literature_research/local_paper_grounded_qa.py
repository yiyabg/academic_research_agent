"""Evidence-citation validation and rendering for local-paper answers."""

from pydantic import BaseModel, Field


class GroundedClaim(BaseModel):
    """A model-generated claim whose citations are checked server-side."""

    text: str = Field(min_length=1, max_length=1600)
    citation_ids: list[int] = Field(min_length=1, max_length=8)


class GroundedAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=3000)
    claims: list[GroundedClaim] = Field(min_length=1, max_length=12)
    uncertainty: str | None = Field(default=None, max_length=1000)


def render_grounded_answer(result: GroundedAnswer, citation_count: int) -> str | None:
    """Render only identifiers from the server-issued evidence registry."""
    valid_ids = set(range(1, citation_count + 1))
    if any(not set(claim.citation_ids).issubset(valid_ids) for claim in result.claims):
        return None
    lines = ["## 综合回答", result.answer, "", "## 有证据的观点"]
    lines.extend(
        f"- {claim.text} {' '.join(f'[{index}]' for index in claim.citation_ids)}"
        for claim in result.claims
    )
    if result.uncertainty:
        lines.extend(["", "## 不确定性", result.uncertainty])
    return "\n".join(lines)
