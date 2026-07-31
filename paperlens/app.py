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
_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parent
sys.path.insert(0, str(_PACKAGE_DIR))


def _load_secrets() -> None:
    """Promote Streamlit secrets into ``os.environ`` — the deployed equivalent of ``.env``.

    ``.env`` is gitignored, so on Streamlit Community Cloud it simply does not
    exist; credentials arrive through the app's Secrets pane instead. Streamlit
    already copies top-level ``str``/``int``/``float`` secrets into ``os.environ``,
    which is exactly the shape ``os.getenv`` teammates read — but it does that
    lazily, the first time ``st.secrets`` is touched. Nothing touches it in this
    codebase, so without this the promotion would happen only *after* the
    import-time reads below, i.e. never in time.

    Touching ``st.secrets`` here forces that to happen first. Keep the secret keys
    flat (no TOML tables) or they are not promoted.

    Raises when no secrets file exists at all, which is the normal local case.
    """
    try:
        st.secrets.to_dict()
    except Exception:  # no secrets configured — expected off-cloud
        pass


def _load_env() -> None:
    """Load ``.env`` before anything else imports a module that reads it.

    This has to happen here, and it has to happen first. Teammate modules read
    their credentials at **import time** — ``citations/config.py`` does
    ``os.getenv("OPENALEX_API_KEY")`` at module level — and none of them call
    ``load_dotenv()`` themselves except ``summarization/briefing.py``, which
    cannot be imported without ``NVIDIA_API_KEY`` in the first place.

    The net effect before this existed: ``.env`` was never loaded at all in the
    running app, so every key read from it came back ``None`` and the failure was
    silent — OpenAlex simply behaved as though no key had been configured.

    Being the entry point, ``app.py`` is the one place that reliably runs before
    any of that. Environment variables already set in the shell win over ``.env``.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is in requirements, but do not hard-fail
        return

    for candidate in (_REPO_ROOT / ".env", _PACKAGE_DIR / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


# Secrets first, then ``.env``: ``load_dotenv(override=False)`` never clobbers what
# is already set, so a deployed secret wins and a local ``.env`` fills the gap.
_load_secrets()
_load_env()

from integration import pipeline  # noqa: E402
from ui import home, splash, state  # noqa: E402
from ui.dashboard import render_dashboard  # noqa: E402

st.set_page_config(
    page_title="PaperLens",
    page_icon=":material/document_scanner:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

state.init()

# Before the upload/dashboard branch, so a first load that restores a document
# still gets the intro. The gate holds until Enter is pressed; the screen behind
# it still renders, so the app is ready the instant it clears.
splash.render_gate()


def _upload_screen() -> None:
    """The landing state. The view lives in ``ui.home``; the pipeline stays here."""
    uploaded = home.render_landing()
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
