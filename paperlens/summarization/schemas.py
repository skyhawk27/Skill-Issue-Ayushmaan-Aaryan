from typing import List
from pydantic import BaseModel, Field


class ClaimItem(BaseModel):
    claim: str = Field(
        description="A clear 1-2 sentence explanation of the finding or concept."
    )
    candidate_quote: str = Field(
        description="An EXACT verbatim quote (10-25 words) directly from the text supporting the claim."
    )
    claimed_page: int = Field(
        description="The integer page number where the quote is located."
    )


class CategoryOutput(BaseModel):
    claims: List[ClaimItem]


class PaperBriefOutput(BaseModel):
    contributions: List[dict]
    methodology: List[dict]
    results: List[dict]
    limitations: List[dict]
    prerequisites: List[str]