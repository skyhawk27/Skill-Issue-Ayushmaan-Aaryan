"""The landing screen: hero, badge legend, and the feature catalogue.

Lives apart from ``app.py`` so the entry point stays orchestration only. The
catalogue is deliberately static — no document exists yet, so a card that
navigated somewhere would land the reader on an empty panel.

Two things here are read from elsewhere rather than restated, and both matter:

* The legend is built from :data:`ui.theme.STATUS_STYLES`, the same table the real
  badges resolve through. A hand-written legend would eventually describe a badge
  the app no longer shows.
* The Summary / Chat / Citations / Reviewer cards take their icons from
  :data:`ui.state.PANEL_ICONS`, so a feature card and the nav-rail panel it
  describes are visibly the same thing.

No custom CSS. ``ui/splash.py`` is the only file permitted a stylesheet; the
layout here is native containers and columns, themed by ``.streamlit/config.toml``.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import streamlit as st

from ui.state import PANEL_ICONS
from ui.theme import STATUS_STYLES


class Feature(NamedTuple):
    """One catalogue card."""

    icon: str
    title: str
    description: str


#: The nine cards, laid out 3 x 3 in reading order. The ordering is the point:
#: the top row is the loop every reader hits (summarise -> verify -> jump to the
#: evidence), the middle row is exploration, the bottom row is analysis and
#: takeaway. Evidence verification sits centre-top because it is what makes
#: PaperLens different from any other summariser.
FEATURES: tuple[Feature, ...] = (
    Feature(
        PANEL_ICONS["Summary"],
        "Structured summary",
        "Contributions, methodology, results and limitations, pulled out and organised.",
    ),
    Feature(
        ":material/verified:",
        "Evidence verification",
        "Every claim is checked against the actual page text and badged "
        "Verified, Paraphrased or Unsupported.",
    ),
    Feature(
        ":material/find_in_page:",
        "Synced PDF viewer",
        "Click any claim to jump straight to its page, with the supporting "
        "passage highlighted.",
    ),
    Feature(
        PANEL_ICONS["Chat"],
        "Grounded chat",
        "Ask questions answered only from the uploaded paper, with page "
        "references — and an honest answer when it cannot be found.",
    ),
    Feature(
        PANEL_ICONS["Citations"],
        "Citation explorer",
        "References enriched with authors, year and citation counts, plus why "
        "each one was cited.",
    ),
    Feature(
        ":material/hub:",
        "Research family tree",
        "The paper's intellectual lineage, drawn as a graph of the work it builds on.",
    ),
    Feature(
        PANEL_ICONS["Reviewer"],
        "Reviewer mode",
        "An academic reviewer's read: strengths, weaknesses, missing baselines "
        "and a reproducibility score.",
    ),
    Feature(
        ":material/rule:",
        "Consistency checker",
        "Flags contradictions between what a paper claims and what its results "
        "actually show.",
    ),
    Feature(
        ":material/download:",
        "Verified summary export",
        "Take the summary away as a shareable card, with every verification "
        "badge intact.",
    ),
)

#: Statuses shown in the legend. ``"unverified"`` is excluded on purpose — it is
#: an internal state for claims the pipeline never reached, not one of the three
#: verdicts a reader is being asked to distinguish between.
_LEGEND_STATUSES = ("verified", "paraphrased", "unsupported")

CATALOGUE_HEADING = "What PaperLens does"

#: Cards per row. Three rather than four fills 3 x 3 exactly with no ragged row,
#: and leaves each card wide enough for a readable line of copy.
_COLUMNS = 3


def render_landing() -> Any:
    """Draw the landing screen. Returns the uploaded file, or ``None``.

    The upload itself is handed back rather than processed here so ``app.py``
    keeps ownership of the pipeline.
    """
    uploaded = _hero()
    _badge_legend()
    _catalogue()
    return uploaded


def _hero() -> Any:
    """Title, one line of copy, and the only action on the page."""
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.space("large")
        with st.container(horizontal_alignment="center"):
            st.title("PaperLens", anchor=False, text_alignment="center")
            st.markdown(
                "Understand a research paper in minutes — with every claim checked "
                "against the paper itself, not just asserted.",
                text_alignment="center",
            )
        st.space("medium")

        uploaded = st.file_uploader(
            "Upload a research paper",
            type=["pdf"],
            help="Any PDF. Nothing is uploaded anywhere — parsing happens locally.",
        )
    return uploaded


def _badge_legend() -> None:
    """The three verdicts, using the same badges the summary will show.

    Placed before the catalogue so the vocabulary is learned *before* the first
    upload rather than decoded afterwards. It is also the only colour on this
    page, which is the point: colour means verification status and nothing else.
    """
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.space("small")
        with st.container(horizontal_alignment="center"):
            st.caption("Every claim gets one of three verdicts")

        for column, status in zip(st.columns(len(_LEGEND_STATUSES)), _LEGEND_STATUSES):
            style = STATUS_STYLES[status]
            with column, st.container(horizontal_alignment="center"):
                st.badge(style.label, icon=style.icon, color=style.badge_color)
                st.caption(style.blurb)


def _catalogue() -> None:
    """The feature grid, in a wider band than the hero so cards do not crush."""
    st.space("large")

    _, middle, _ = st.columns([1, 12, 1])
    with middle:
        with st.container(horizontal_alignment="center"):
            st.subheader(CATALOGUE_HEADING, anchor=False)
        st.space("small")

        for row_start in range(0, len(FEATURES), _COLUMNS):
            row = FEATURES[row_start : row_start + _COLUMNS]
            for column, feature in zip(st.columns(_COLUMNS), row):
                with column:
                    # height="stretch" keeps a row level despite uneven copy;
                    # without it the cards end up visibly ragged.
                    with st.container(border=True, height="stretch"):
                        st.markdown(f"{feature.icon} **{feature.title}**")
                        st.caption(feature.description)
