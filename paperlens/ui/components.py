"""Shared UI pieces, built from native Streamlit elements.

There is no custom CSS anywhere in PaperLens. Everything visual comes from
``.streamlit/config.toml`` plus native containers, which means the app inherits
Streamlit's own accessibility and responsive behaviour instead of fighting it.
Two consequences shaped the components below:

* ``st.container(border=True)`` is the card. It picks up ``borderColor`` and
  ``baseRadius`` from the theme, so cards match the rest of the chrome for free.
* Badges are ``st.badge``, whose ``color`` names map to the ``greenColor`` /
  ``orangeColor`` / ``redColor`` values in the theme. The verification palette is
  therefore defined in exactly one place.

Every status indicator carries **glyph + word + score**, never colour alone. A
badge has to survive a washed-out projector, a greyscale screenshot and a
colour-blind viewer, because it is the one thing in this product a user is being
asked to trust.
"""

from __future__ import annotations

from typing import Iterable

import streamlit as st

from integration.contracts import Claim, Evidence
from ui import state
from ui.theme import STATUS_STYLES, Status, style_for


def verification_badge(evidence: Evidence, *, show_score: bool = True) -> None:
    """Render the badge for one piece of evidence."""
    style = style_for(evidence.status)
    label = style.label
    if show_score and evidence.score is not None:
        label = f"{style.label} · {evidence.score:.2f}"
    st.badge(label, icon=style.icon, color=style.badge_color, help=style.blurb)


def tally_rail(counts: dict[Status, int]) -> None:
    """The nav rail's at-a-glance verification summary.

    Ordered worst-first on purpose. If anything in this paper is unsupported,
    that is the number a reader needs to see, and burying it under the green
    count would quietly undersell the risk.
    """
    order: tuple[Status, ...] = ("unsupported", "paraphrased", "verified", "unverified")
    shown = [s for s in order if counts.get(s)]
    if not shown:
        return

    st.caption("Evidence")
    for status in shown:
        style = STATUS_STYLES[status]
        st.badge(
            f"{counts[status]} {style.label.lower()}",
            icon=style.icon,
            color=style.badge_color,
            help=style.blurb,
        )


def evidence_button(evidence: Evidence, *, key: str, claim_id: str | None = None,
                    label: str | None = None) -> bool:
    """The 'jump to the evidence' control.

    Disabled with an explanation when there is nowhere to jump to — a dead button
    that silently does nothing is worse than a visibly unavailable one.
    """
    if not evidence.is_navigable:
        st.button(
            "No page cited",
            key=key,
            icon=":material/block:",
            disabled=True,
            help="This claim did not come with a page reference, so there is nothing to open.",
            width="stretch",
        )
        return False

    selected = claim_id is not None and state.get(state.ACTIVE_CLAIM) == claim_id
    text = label or f"Page {evidence.page}"
    clicked = st.button(
        text,
        key=key,
        icon=":material/my_location:" if not selected else ":material/check:",
        type="primary" if selected else "secondary",
        help="Open this page in the viewer and highlight the matched text.",
        width="stretch",
    )
    if clicked:
        state.show_evidence(evidence.page, evidence.quote, evidence.status, claim_id)
    return clicked


def quote_block(evidence: Evidence) -> None:
    """The supporting quote, as a blockquote."""
    if not evidence.has_quote:
        return
    quote = evidence.quote.strip().strip('"')
    if len(quote) > 320:
        quote = quote[:317].rstrip() + "…"
    st.markdown(f"> {quote}")


def claim_card(claim: Claim, *, key_prefix: str) -> None:
    """One claim: the assertion, its badge, its quote, and a jump control.

    Layout puts the badge beside the claim text rather than under it, so a reader
    scanning a column of claims gets the verification state in the same eye
    movement as the claim itself.
    """
    with st.container(border=True):
        text_col, badge_col = st.columns([7, 3], vertical_alignment="top")
        with text_col:
            st.markdown(claim.text or "_No claim text was provided._")
        with badge_col:
            with st.container(horizontal_alignment="right"):
                verification_badge(claim.evidence)

        quote_block(claim.evidence)

        with st.container(horizontal=True, horizontal_alignment="left"):
            evidence_button(
                claim.evidence,
                key=f"{key_prefix}-{claim.claim_id}",
                claim_id=claim.claim_id,
            )


def claim_list(claims: Iterable[Claim], *, key_prefix: str) -> None:
    for claim in claims:
        claim_card(claim, key_prefix=key_prefix)


def unavailable(feature: str, detail: str = "") -> None:
    """The scoped-degradation message required by NFR §11.

    Deliberately a ``st.warning`` rather than ``st.error``: the feature is
    missing, but the app is not broken, and the visual weight should say so.
    """
    st.warning(f"{feature} unavailable.", icon=":material/cloud_off:")
    if detail:
        st.caption(detail)


def empty_state(headline: str, detail: str = "", icon: str = ":material/inbox:") -> None:
    """A panel with nothing to show yet — said plainly, centred, low-key."""
    with st.container(horizontal_alignment="center"):
        st.markdown(f"### {icon} {headline}")
        if detail:
            st.caption(detail)


def fallback_banner(notice: str) -> None:
    """Disclose that local fallbacks are standing in for teammate modules.

    This is not decoration. The product's entire claim is that you can trust what
    it shows you, so the UI has to be explicit about which parts are real AI
    output and which are local heuristics. Collapsed by default to stay out of
    the way, but always present.
    """
    if not notice:
        return
    with st.expander("Running with local fallbacks", icon=":material/science:"):
        st.caption(notice)
