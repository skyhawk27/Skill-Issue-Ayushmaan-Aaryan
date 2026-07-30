CATEGORIES = {
    "contributions": "Main contributions, core innovations, and primary problem solved by the paper.",
    "methodology": "System architecture, algorithms, datasets, and implementation details.",
    "results": "Quantitative metrics, benchmark performance, comparisons, and key empirical findings.",
    "limitations": "Explicit weaknesses, constraints, failure cases, or missing baselines noted in the paper.",
}

SYSTEM_PROMPT_TEMPLATE = """
You are an expert academic paper analyzer. Analyze the provided research paper text.
Extract key claims for category: '{category}' ({description}).

For EVERY claim, you MUST output:
1. "claim": A clear, 1-2 sentence explanation.
2. "candidate_quote": An EXACT verbatim quote (10 to 25 words) directly from the text supporting the claim.
3. "claimed_page": The page number integer where the quote is located.
"""


