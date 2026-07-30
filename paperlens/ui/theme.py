"""Design tokens that Python needs at runtime.

Almost all of PaperLens' appearance lives in ``.streamlit/config.toml`` — that is
deliberate, and this module is intentionally small. Only two kinds of value
belong here:

1. Tokens a *third party* needs as a literal string. ``streamlit-pdf-viewer``
   takes annotation colours as hex and knows nothing about the Streamlit theme,
   so the highlight colours must exist in Python.
2. Semantics, not styling — the Feature 4B score thresholds, and the mapping
   from a verification status to its label and icon.

If you are tempted to add a colour here for something Streamlit renders itself,
put it in ``config.toml`` instead.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

# ─── Verification status ────────────────────────────────────────────────────
# PRD Feature 4B defines three bands by fuzzy match score. These are the
# product's core semantics, so they are named constants rather than magic
# numbers scattered through the panels.

Status = Literal["verified", "paraphrased", "unsupported", "unverified"]

VERIFIED_THRESHOLD = 0.90
PARAPHRASED_THRESHOLD = 0.60


class StatusStyle(NamedTuple):
    """How one verification status presents itself across the whole UI."""

    label: str
    icon: str          # Material Symbols name, per the Streamlit design guidance
    badge_color: str   # an st.badge colour name; the hex comes from config.toml
    highlight: str     # hex, for the PDF annotation overlay
    blurb: str         # plain-language meaning, used in tooltips


#: The single source of truth for status presentation. Every badge, every PDF
#: highlight and every tooltip in the app resolves through this table, so a
#: status can never look like one thing in the summary and another in chat.
STATUS_STYLES: dict[Status, StatusStyle] = {
    "verified": StatusStyle(
        label="Verified",
        icon=":material/check_circle:",
        badge_color="green",
        highlight="#1aae39",
        blurb="Quote found essentially verbatim on the cited page.",
    ),
    "paraphrased": StatusStyle(
        label="Paraphrased",
        icon=":material/change_circle:",
        badge_color="orange",
        highlight="#dd5b00",
        blurb="The idea is present on the page, but the wording differs.",
    ),
    "unsupported": StatusStyle(
        label="Unsupported",
        icon=":material/cancel:",
        badge_color="red",
        highlight="#c0362c",
        blurb="The quote could not be located in the paper. Treat with caution.",
    ),
    "unverified": StatusStyle(
        label="Not verified",
        icon=":material/help:",
        badge_color="gray",
        highlight="#615d59",
        blurb="The verification pipeline did not run for this claim.",
    ),
}


def status_for_score(score: float | None) -> Status:
    """Bucket a fuzzy match score into a Feature 4B status band.

    ``None`` means verification never ran, which is materially different from
    scoring zero — the PRD is emphatic that unverified claims are labelled
    rather than hidden or silently passed off as checked.
    """
    if score is None:
        return "unverified"
    if score >= VERIFIED_THRESHOLD:
        return "verified"
    if score >= PARAPHRASED_THRESHOLD:
        return "paraphrased"
    return "unsupported"


def style_for(status: str | None) -> StatusStyle:
    """Look up a status style, tolerating unknown or missing values.

    Teammates' modules are still moving; an unexpected status string should
    degrade to "not verified" rather than raise inside a render pass.
    """
    if status is None:
        return STATUS_STYLES["unverified"]
    return STATUS_STYLES.get(status.strip().lower(), STATUS_STYLES["unverified"])


# ─── Layout ────────────────────────────────────────────────────────────────
# The two panes are fixed-height scroll containers (st.container(height=...)),
# which is what keeps the PDF visible while the left pane scrolls — no CSS
# needed. Tuned so both panes fit a 900px-tall laptop viewport without the
# outer page itself scrolling.

PANE_HEIGHT = 760
PDF_VIEWER_HEIGHT = PANE_HEIGHT - 96  # room for the page-nav row above it

#: Left nav rail / content / PDF. The rail is narrow because it holds icons and
#: a tally, not prose.
SPLIT_RATIO = (2, 5, 6)

#: Width handed to pdf_viewer. The component documents that an explicit width is
#: mandatory inside tabs, expanders and dialogs or it renders blank, so we never
#: rely on the default.
PDF_VIEWER_WIDTH = "100%"
