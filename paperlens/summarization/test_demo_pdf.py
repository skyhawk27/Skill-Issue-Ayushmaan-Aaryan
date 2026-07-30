import json
import os
import sys

# ---------------------------------------------------------------------------
# DYNAMIC PATH RESOLUTION
# Allows running the script directly from inside `paperlens/summarization/`
# or from the workspace root directory (`bkchodi/`) without import errors.
# ---------------------------------------------------------------------------
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
)

import pymupdf  # Modern PyMuPDF import
from paperlens.summarization.briefing import generate_brief


def parse_pdf_to_json(pdf_path: str) -> dict:
    """Parses a real PDF into Member 1's standard doc_json format."""
    doc = pymupdf.open(pdf_path)
    pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        pages.append(
            {"page_number": page_num + 1, "text": page.get_text("text")}
        )

    title = (
        doc.metadata.get("title")
        or "A look at advanced learners' use of mobile devices for English language study"
    )
    return {"paper_title": title, "pages": pages}


if __name__ == "__main__":
    # Resolve absolute path to demo.pdf in workspace root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.abspath(os.path.join(script_dir, "../../"))
    pdf_path = os.path.join(root_dir, "/Users/shivanshuprakash/Desktop/bkchodi/paperlens/summarization/demo.pdf")

    # Fallback to local execution directory if not found in root
    if not os.path.exists(pdf_path):
        pdf_path = "demo.pdf"

    print(f"📄 Parsing '{pdf_path}'...")
    try:
        doc_json = parse_pdf_to_json(pdf_path)
    except FileNotFoundError:
        print(
            f"❌ Error: Could not find '{pdf_path}'. Make sure 'demo.pdf' is placed in your project root folder."
        )
        sys.exit(1)

    print(
        f"⚡ Running parallel summarizer on all {len(doc_json['pages'])} pages..."
    )
    result = generate_brief(doc_json)

    print("\n✅ SUCCESS! Paper Analysis Complete.")
    print(f"📌 Paper Title: {result.get('paper_title')}")
    print(f"📄 Total Pages Processed: {result.get('total_pages_processed')}")
    print(f"\n📝 Executive Conclusion:\n{result.get('conclusion')}")

    print("\n--- FIRST 2 PAGE SUMMARIES ---")
    for ps in result.get("page_by_page_summaries", [])[:2]:
        print(f"\n[Page {ps['page_number']}]:\n{ps['summary']}")

    # Save complete JSON result for inspection
    output_path = os.path.join(root_dir, "demo_paper_output.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n💾 Saved full analysis to `{output_path}`!")