"""Reviewer panel — PRD Feature 8, with the Feature 9 consistency flags.

Reviewer Mode is not in the five-function integration contract (it appears in the
PRD's folder layout but nobody's public function covers it), and it is explicitly
first in the cut order. So this panel is built to be genuinely optional: if
nothing exports a reviewer, it says so calmly and the rest of the dashboard is
untouched.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from integration import pipeline
from integration.contracts import Document, to_evidence
from ui import components, state


def render(doc: Document) -> None:
    review, error = _load(doc)

    if error:
        components.unavailable(
            "Reviewer mode",
            "The reviewer module failed. Summary, chat and citations are unaffected.",
        )
        with st.expander("Error detail", icon=":material/bug_report:"):
            st.code(error, language="text")
        return

    if not review:
        components.empty_state(
            "Reviewer mode not available",
            "No reviewer module is wired up yet. This is a stretch feature — "
            "the rest of the dashboard does not depend on it.",
            icon=":material/rate_review:",
        )
        return

    _reproducibility(review)
    _consistency(review)
    _findings(review)


def _load(doc: Document):
    cached = state.get(state.REVIEW)
    if cached is not None:
        return cached, state.error_for("Reviewer mode")

    with st.spinner("Reviewing the paper…"):
        review, error = pipeline.load_review(state.get(state.DOC_KEY) or "", doc)

    st.session_state[state.REVIEW] = review
    if error:
        state.record_error("Reviewer mode", error)
    return review, error


def _reproducibility(review: dict[str, Any]) -> None:
    score = review.get("reproducibility_score")
    checks = review.get("checks") or []

    if score is None and not checks:
        return

    st.subheader("Reproducibility", anchor=False)

    if score is not None:
        with st.container(border=True):
            st.metric("Reproducibility score", f"{score} / 10")
            try:
                st.progress(min(1.0, max(0.0, float(score) / 10.0)))
            except (TypeError, ValueError):
                pass

    for check in checks:
        name = str(check.get("name", "")).strip()
        if not name:
            continue
        present = bool(check.get("present"))
        st.badge(
            name,
            icon=":material/check:" if present else ":material/close:",
            color="green" if present else "gray",
            help="Mentioned in the paper text." if present
            else "No mention found in the paper text.",
        )


def _consistency(review: dict[str, Any]) -> None:
    """Feature 9 — contradictions between what the paper claims and what it shows."""
    flags = review.get("consistency") or []
    st.subheader("Consistency", anchor=False)

    if not flags:
        st.caption(
            "No cross-section contradiction check has run for this paper. "
            "This is a stretch feature (PRD Feature 9)."
        )
        return

    for position, flag in enumerate(flags):
        with st.container(border=True):
            st.warning(
                str(flag.get("text") or flag.get("claim") or "Possible inconsistency."),
                icon=":material/rule:",
            )
            evidence = to_evidence(flag)
            if evidence.has_quote:
                components.quote_block(evidence)
            if evidence.is_navigable:
                with st.container(horizontal=True, vertical_alignment="center"):
                    components.verification_badge(evidence)
                    components.evidence_button(
                        evidence,
                        key=f"reviewer-consistency-{position}",
                        claim_id=f"consistency-{position}",
                    )


def _findings(review: dict[str, Any]) -> None:
    findings = review.get("findings") or []
    if not findings:
        return

    buckets: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        buckets.setdefault(str(finding.get("kind", "finding")).lower(), []).append(finding)

    titles = {
        "strength": "Strengths",
        "weakness": "Weaknesses",
        "missing": "Missing experiments and baselines",
        "finding": "Other findings",
    }

    for kind in ("strength", "weakness", "missing", "finding"):
        items = buckets.get(kind)
        if not items:
            continue
        st.subheader(titles[kind], anchor=False)
        for position, finding in enumerate(items):
            with st.container(border=True):
                st.markdown(str(finding.get("text", "")))
                evidence = to_evidence(finding)
                if evidence.has_quote:
                    components.quote_block(evidence)
                if evidence.is_navigable:
                    with st.container(horizontal=True, vertical_alignment="center"):
                        components.verification_badge(evidence)
                        components.evidence_button(
                            evidence,
                            key=f"reviewer-{kind}-{position}",
                            claim_id=f"{kind}-{position}",
                        )
