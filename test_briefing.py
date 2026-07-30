import json
from briefing import generate_brief

if __name__ == "__main__":
    print("🚀 Loading mock document...")
    with open("/Users/shivanshuprakash/Desktop/bkchodi/mock_dock.json", "r") as f:
        mock_doc = json.load(f)

    print("⚡ Running parallel LLM briefing extraction...")
    result = generate_brief(mock_doc)

    print("\n✅ Extraction Successful! Output JSON:\n")
    print(json.dumps(result, indent=2))