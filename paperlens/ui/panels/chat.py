"""Chat panel — PRD Feature 5, grounded question answering.

Three things here are load-bearing for the demo (script step 4):

* Every answer carries a verification badge, exactly like a summary claim.
* **The viewer moves on its own.** A new answer points the PDF pane at its
  evidence immediately — no click — and cites the exact paragraph it came from,
  resolved from the document rather than asserted by the model. Only the newest
  answer navigates; re-rendering history must never hijack the pane, which is why
  it happens at answer time rather than during render.
* The "not enough evidence" response is a designed state, not an error. The demo
  deliberately asks a question the paper cannot answer, and that moment only
  lands if the refusal looks like the product working as intended rather than
  like a failure.
"""

from __future__ import annotations

import streamlit as st

from integration import adapters, highlight, pipeline
from integration.contracts import ChatTurn, Document, to_chat_turn
from ui import components, state
from ui.theme import style_for

#: User's explicit opt-in to the offline keyword retriever.
#:
#: This is plain session state, deliberately **not** a widget key. Using the
#: widget's own key here was a bug: switching the toggle on made the offline
#: index build, which meant the failure branch stopped rendering, which meant the
#: toggle itself was no longer on the page — and Streamlit discards widget state
#: for widgets that disappear. The setting silently flipped back off on the next
#: rerun. The widget now writes here through a callback, so the choice outlives
#: the widget that made it.
_LOCAL_MODE = "pl.chat_local"

#: The toggle widget's own key. Kept distinct from the value above.
_LOCAL_MODE_WIDGET = "pl.chat_local_toggle"


def _local_mode() -> bool:
    return bool(st.session_state.get(_LOCAL_MODE, False))


def _sync_local_mode() -> None:
    """Copy the toggle's value into persistent state."""
    st.session_state[_LOCAL_MODE] = bool(st.session_state.get(_LOCAL_MODE_WIDGET, False))


def _local_mode_toggle() -> None:
    """The offline-search opt-in.

    Rendered whenever it is relevant — both when credentials are missing *and*
    while offline mode is active — so there is always a way back to semantic
    retrieval, and so the widget never vanishes mid-session.
    """
    st.toggle(
        "Use local keyword search",
        value=_local_mode(),
        key=_LOCAL_MODE_WIDGET,
        on_change=_sync_local_mode,
        help=(
            "Answers from the paper by matching words rather than meaning. "
            "No API key needed. Weaker than semantic retrieval — it will miss "
            "questions phrased differently from the text."
        ),
    )


def _index_failure(error: str) -> None:
    """Explain why chat is unavailable, and offer the one thing that helps.

    A missing API key is by far the most likely cause on a fresh checkout, and it
    is a *configuration* state, not a bug — so it gets a plain instruction and an
    explicit opt-in to offline search, rather than a stack trace. Anything else
    gets the scoped-degradation treatment plus the detail.
    """
    if adapters.is_credential_error(error):
        st.info(
            "Chat needs an OpenAI API key. The rest of the dashboard works without one.",
            icon=":material/key:",
        )
        st.markdown(
            "Set it and restart the server:\n"
            "```bash\n"
            "export OPENAI_API_KEY=sk-...\n"
            "```"
        )
        # Deliberately opt-in. Downgrading semantic retrieval to keyword matching
        # automatically would leave the user unable to tell which one produced an
        # answer, in a product whose whole point is knowing what to trust.
        _local_mode_toggle()
        return

    components.unavailable(
        "Chat",
        "The retrieval index could not be built, so questions cannot be answered "
        "from the paper.",
    )
    with st.expander("Error detail", icon=":material/bug_report:"):
        st.code(error, language="text")


def render(doc: Document) -> None:
    if not doc.full_text_by_page:
        components.empty_state(
            "Chat needs the parsed paper",
            "Document parsing did not produce page text, so questions cannot be grounded.",
            icon=":material/forum:",
        )
        return

    use_local = _local_mode()
    index, index_error = pipeline.get_index(
        state.get(state.DOC_KEY) or "", doc, local=use_local
    )

    if index_error:
        _index_failure(index_error)
        return

    if use_local:
        # Keep the toggle on screen while offline mode is active: it is both the
        # disclosure that answers are keyword-matched, and the way back out.
        with st.container(horizontal=True, vertical_alignment="center"):
            _local_mode_toggle()
            st.caption("Matching words, not meaning. Set `OPENAI_API_KEY` for semantic search.")

    history: list[ChatTurn] = state.get(state.CHAT) or []

    if not history:
        st.caption(
            "Ask anything about this paper. Answers come only from the uploaded PDF, "
            "and each one is checked against the page text before you see it."
        )

    for position, turn in enumerate(history):
        _render_turn(turn, position)

    question = st.chat_input("Ask a question about this paper")
    if question:
        _handle_question(question, doc, index, local=use_local)


def _handle_question(question: str, doc: Document, index, *, local: bool = False) -> None:
    """Run one question through the chat module and store the turn."""
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Searching the paper and verifying the answer…"):
            outcome = adapters.run_ask_question(question, doc, index, local=local)

        if not outcome.ok:
            if adapters.is_credential_error(outcome.error):
                st.info("Chat needs an OpenAI API key.", icon=":material/key:")
            else:
                st.error("The chat module failed on that question.", icon=":material/error:")
            st.caption(outcome.error)
            return

        turn = to_chat_turn(question, outcome.value)

        # Backstop: if the answer arrived without a verification score, verify it
        # here. The PRD is explicit that no unverified answer is displayed
        # without a badge.
        if turn.evidence.has_quote and turn.evidence.score is None:
            verified = adapters.run_verify_claim(
                turn.evidence.quote, turn.evidence.page, doc.full_text_by_page
            )
            if verified.ok:
                from dataclasses import replace

                from integration.contracts import to_evidence

                checked = to_evidence(verified.value)
                turn = replace(
                    turn,
                    evidence=replace(
                        turn.evidence,
                        page=checked.page or turn.evidence.page,
                        score=checked.score,
                        status=checked.status,
                    ),
                )

    state.get(state.CHAT).append(turn)

    # Point the viewer at this answer without waiting for a click. Only the newest
    # answer does this — re-rendering history must never hijack the pane, which is
    # why it happens here at answer time rather than in _render_turn.
    if turn.evidence.is_navigable:
        state.show_evidence(
            turn.evidence.page,
            turn.evidence.quote,
            turn.evidence.status,
            claim_id=f"chat-{len(state.get(state.CHAT)) - 1}",
        )

    # Rerun so the new turn renders through the same path as the history, rather
    # than being drawn twice by two different code paths.
    st.rerun()


def _render_turn(turn: ChatTurn, position: int) -> None:
    with st.chat_message("user"):
        st.markdown(turn.question)

    with st.chat_message("assistant"):
        if turn.no_evidence:
            _render_no_evidence(turn)
            return

        st.markdown(turn.answer or "_No answer text was returned._")

        with st.container(horizontal=True, vertical_alignment="center"):
            components.verification_badge(turn.evidence)
            if turn.confidence:
                st.caption(f"Confidence: {turn.confidence}")

        _citation(turn)

        if turn.evidence.is_navigable:
            components.evidence_button(
                turn.evidence,
                key=f"chat-evidence-{position}",
                claim_id=f"chat-{position}",
                label="Show in paper",
            )


#: Guard on the inline paragraph. Chosen over an expander, but one pathological
#: block should not push the chat input off screen.
_MAX_PARAGRAPH_CHARS = 1200


def _citation(turn: ChatTurn) -> None:
    """Where the answer came from: locator, quote, and the surrounding paragraph.

    The paragraph is resolved from the PDF rather than the model, so the citation
    is a fact about the document rather than another thing the model asserted.
    """
    evidence = turn.evidence
    if not evidence.has_quote:
        components.quote_block(evidence)
        return

    passage = None
    pdf_path = state.get(state.PDF_PATH)
    if pdf_path and evidence.is_navigable:
        passage = highlight.locate_passage_cached(
            state.get(state.DOC_KEY) or "",
            pdf_path,
            evidence.quote,
            evidence.page,
            style_for(evidence.status).highlight,
        )

    with st.container(border=True):
        locator = passage.locator if passage is not None else (
            f"Page {evidence.page}" if evidence.page else ""
        )
        if locator:
            st.caption(locator)

        components.quote_block(evidence)

        if passage is not None and passage.paragraph_text:
            body = passage.paragraph_text
            if len(body) > _MAX_PARAGRAPH_CHARS:
                body = body[: _MAX_PARAGRAPH_CHARS - 1].rstrip() + "…"
            st.caption("In context")
            st.markdown(body)


def _render_no_evidence(turn: ChatTurn) -> None:
    """The honest refusal.

    Deliberately calm: an ``st.info`` with a neutral icon, not a warning. The
    paper simply does not cover this, which is information, not a malfunction.
    Showing it as an error would teach users to distrust the one behaviour that
    proves the system is not making things up.
    """
    st.info(
        turn.answer or "The uploaded paper does not provide enough evidence to answer this.",
        icon=":material/help_center:",
    )
    st.caption(
        "No supporting passage cleared the verification threshold, so no answer is offered. "
        "PaperLens does not answer from outside the paper."
    )
