"""PaperLens — entry point.

Run with::

    streamlit run paperlens/app.py

Two states: an upload screen, and the dashboard. The pipeline between them
narrates itself through ``st.status`` because it takes 20-35 seconds by design
(PRD §11) and twenty seconds of silence reads as a hang.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Allow `streamlit run paperlens/app.py` from the repository root: the package
# directory itself must be importable for `ui.*` / `integration.*` to resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from integration import pipeline  # noqa: E402
from ui import state  # noqa: E402
from ui.dashboard import render_dashboard  # noqa: E402

st.set_page_config(
    page_title="PaperLens",
    page_icon=":material/document_scanner:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

state.init()


def _upload_screen() -> None:
    """The landing state: one action, stated plainly."""
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

        st.space("small")
        with st.container(horizontal_alignment="center"):
            st.caption(
                "Claims are labelled Verified, Paraphrased or Unsupported by matching "
                "each quote against the actual page text."
            )

    if uploaded is not None:
        _load(uploaded)


def _load(uploaded) -> None:
    """Persist the upload and run the core pipeline, narrating progress."""
    pdf_bytes = uploaded.getvalue()
    pdf_path, cache_key = pipeline.prepare_pdf(pdf_bytes, uploaded.name)

    if state.get(state.DOC_KEY) != cache_key:
        state.reset_document()

    st.session_state[state.DOC_KEY] = cache_key
    st.session_state[state.PDF_PATH] = pdf_path
    st.session_state[state.PDF_NAME] = uploaded.name

    with st.status("Processing the paper…", expanded=True) as status:
        def progress(message: str) -> None:
            status.write(message)

        document, brief, errors = pipeline.load_core(
            cache_key, pdf_path, uploaded.name, progress=progress
        )

        for label, message in errors.items():
            state.record_error(label, message)

        if errors.get("Document parsing"):
            status.update(label="Could not read this PDF", state="error")
            st.error(
                "This file could not be parsed as a PDF. Try a different file.",
                icon=":material/error:",
            )
            st.caption(errors["Document parsing"])
            return

        st.session_state[state.DOCUMENT] = document
        st.session_state[state.BRIEF] = brief

        verified = brief.tally().get("verified", 0)
        status.update(
            label=f"Ready — {len(brief.claims)} claims, {verified} verified",
            state="complete",
            expanded=False,
        )

    st.rerun()


def _reset_button() -> None:
    with st.sidebar:
        st.caption("Paper")
        st.markdown(f"**{state.get(state.PDF_NAME) or 'Untitled'}**")
        if st.button("Upload a different paper", icon=":material/upload_file:", width="stretch"):
            state.reset_document()
            st.session_state[state.DOC_KEY] = None
            st.session_state[state.PDF_PATH] = None
            st.session_state[state.PDF_NAME] = ""
            st.rerun()


if state.has_document():
    _reset_button()
    render_dashboard(
        state.get(state.DOCUMENT),
        state.get(state.BRIEF),
        state.get(state.REFERENCES),
    )
else:
    _upload_screen()
