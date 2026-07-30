import asyncio
import json
import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

# 1. Load environment variables from .env file
load_dotenv()

NVIDIA_KEY = os.getenv("NVIDIA_API_KEY")

if not NVIDIA_KEY:
    print(
        "⚠️ Warning: NVIDIA_API_KEY not found in .env file! Please check your .env setup."
    )

# 2. Initialize AsyncOpenAI with NVIDIA NIM base_url and custom headers
client = AsyncOpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_KEY,
    default_headers={"User-Agent": "PaperLens-App/1.0"},
)

# ---------------------------------------------------------------------------
# MOCK STUB FOR MEMBER 3'S VERIFICATION FUNCTION (Feature 4B)
# ---------------------------------------------------------------------------
try:
    from verification import verify_claim
except ImportError:

    def verify_claim(doc_json: dict, candidate_quote: str, claimed_page: int):
        """Temporary mock stub until Member 3 completes Feature 4B."""
        return {"match_score": 0.94, "status": "verified"}


# ---------------------------------------------------------------------------
# CATEGORIES & PROMPT INSTRUCTIONS
# ---------------------------------------------------------------------------
CATEGORIES = {
    "contributions": "Main novel contributions and core problems solved. Do NOT repeat implementation or benchmark metrics here.",
    "methodology": "Technical architecture, model design, and training process. Focus purely on HOW it was built.",
    "results": "Quantitative benchmark metrics, BLEU scores, or experimental performance. Focus purely on empirical numbers.",
    "limitations": "Explicit weaknesses, failure cases, or missing comparisons stated in the paper. If none are explicitly stated in the text, return an empty array [].",
}


async def extract_category_claims(
    doc_text: str, category: str, description: str
) -> list:
    """Asynchronously calls NVIDIA API to extract category claims."""

    system_prompt = f"""
    You are an expert academic paper analyzer. Analyze the provided research paper text.
    Extract key claims for category: '{category.upper()}' ({description}).

    CRITICAL RULES:
    1. Extract ONLY claims that fit strictly under '{category.upper()}'. Do NOT include claims that belong in other sections.
    2. For 'LIMITATIONS': Include ONLY explicit weaknesses or failure cases stated directly in the text. 
       If no explicit limitations are mentioned in the text, return ONLY: {{"claims": []}}.
       NEVER output claims stating that "the paper does not discuss X" or "there are no limitations".

    For EVERY valid claim found:
    1. "claim": A concise 1-sentence summary.
    2. "candidate_quote": An EXACT verbatim quote (10 to 25 words) directly from the text.
    3. "claimed_page": The exact integer page number.

    Return ONLY valid JSON matching this schema:
    {{
      "claims": [
        {{
          "claim": "...",
          "candidate_quote": "...",
          "claimed_page": 1
        }}
      ]
    }}
    """

    try:
        response = await client.chat.completions.create(
            model="meta/llama-3.1-8b-instruct",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Paper Content:\n{doc_text[:12000]}",
                },
            ],
            temperature=0.0,  # Forces deterministic adherence to instructions
        )

        content = response.choices[0].message.content.strip()

        # Clean markdown code fences if present in model output
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        data = json.loads(content.strip())
        claims = data.get("claims", [])

        # Filter out invalid/empty entries with missing quotes or page 0
        valid_claims = [
            c
            for c in claims
            if c.get("candidate_quote") and c.get("claimed_page", 0) > 0
        ]

        return valid_claims

    except Exception as e:
        print(f"Error extracting category '{category}' via NVIDIA API: {e}")
        return []


# ---------------------------------------------------------------------------
# MAIN EXPOSED FUNCTION: generate_brief()
# ---------------------------------------------------------------------------
async def generate_brief_async(doc_json: dict) -> dict:
    """Asynchronously generates paper brief and passes claims to Member 3's verification pipeline."""
    full_text = "\n\n".join(
        [
            f"--- PAGE {p.get('page_number', i+1)} ---\n{p.get('text', '')}"
            for i, p in enumerate(doc_json.get("pages", []))
        ]
    )

    # 1. Execute parallel extraction calls across categories
    tasks = [
        extract_category_claims(full_text, category, description)
        for category, description in CATEGORIES.items()
    ]

    results = await asyncio.gather(*tasks)

    raw_brief = {
        category: claims for category, claims in zip(CATEGORIES.keys(), results)
    }

    # 2. Add Prerequisites static list
    raw_brief["prerequisites"] = [
        "Fundamental Deep Learning",
        "Transformer Architectures",
        "Attention Mechanisms",
    ]

    # 3. Pass claims to Feature 4B verification
    for category in ["contributions", "methodology", "results", "limitations"]:
        for item in raw_brief[category]:
            verification_res = verify_claim(
                doc_json=doc_json,
                candidate_quote=item["candidate_quote"],
                claimed_page=item["claimed_page"],
            )
            item.update(verification_res)

    return raw_brief


def generate_brief(doc_json: dict) -> dict:
    """Synchronous wrapper function for Member 5's Streamlit integration."""
    return asyncio.run(generate_brief_async(doc_json))


# ---------------------------------------------------------------------------
# LOCAL TEST SUITE
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("🚀 Testing refined `briefing.py` using NVIDIA NIM API...")

    mock_doc = {
        "paper_title": "Attention Is All You Need",
        "pages": [
            {
                "page_number": 1,
                "text": "We propose the Transformer, a model architecture based entirely on attention mechanisms, dispensing with recurrence and convolutions entirely. On two machine translation tasks, these models are superior in quality while being more parallelizable.",
            },
            {
                "page_number": 3,
                "text": "An attention function can be described as mapping a query and a set of key-value pairs to an output. Multi-head attention allows the model to jointly attend to information from different representation subspaces at different positions.",
            },
            {
                "page_number": 7,
                "text": "On the WMT 2014 English-to-German translation task, the big transformer model achieves 28.4 BLEU, improving over the existing best results by over 2.0 BLEU. The model reached 41.8 BLEU on English-to-French.",
            },
        ],
    }

    brief_output = generate_brief(mock_doc)

    print("\n✅ Extraction Complete! Output JSON:\n")
    print(json.dumps(brief_output, indent=2))