"""The opening splash — the only custom CSS in PaperLens.

Why CSS exists here and nowhere else
------------------------------------
Everything else in this app is themed through ``.streamlit/config.toml`` and
native Streamlit containers, deliberately: injected stylesheets break across
Streamlit versions and bypass the accessibility behaviour we get for free. That
rule holds everywhere except this file, because an intro animation was asked for
explicitly and there is no native equivalent.

So the exception is quarantined. Every selector below is namespaced (``#pl-splash``,
``.pl-*``, or the one generated ``.st-key-*`` hook for the button), nothing here
styles an app surface, and no other module in ``ui/`` may grow a stylesheet. If
you find yourself importing this pattern elsewhere, reach for ``config.toml``.

What it shows
-------------
The wordmark, a highlight band wiping across it, then a green check. That is the
product in miniature — *claim → highlighted evidence → Verified* is the whole
interaction PaperLens exists for — and it is why the single green accent is
earned here rather than decorative. The green is read from the same constant the
badges use, so the two can never drift apart.

It is a **gate**: the sequence plays over ~4s and then holds until the reader
presses Enter. Nothing auto-advances, so a demo cannot clear itself mid-sentence.

The failure mode this is built around
-------------------------------------
A fixed full-viewport overlay that holds indefinitely is the most dangerous kind
of UI to get wrong — a stripped stylesheet could leave it covering the app with
no way past. Three properties make that impossible:

* **The overlay's resting state is invisible** (``opacity: 0``) and the animation
  reveals it. No animation, no overlay — the app is simply untouched.
* **The overlay never takes pointer events.** It cannot swallow a click even
  while fully visible, so it can never intercept its own way out.
* **Enter is a real Streamlit widget**, not a styled ``<div>``. Its click path
  does not depend on any CSS surviving; the stylesheet only *positions* it. If
  the CSS is stripped, the button simply renders inline and still works.

``prefers-reduced-motion`` skips the motion and shows the finished state
immediately, so reduced-motion users get the gate without the animation rather
than being locked out of it.
"""

from __future__ import annotations

import streamlit as st

from ui import state
from ui.theme import STATUS_STYLES

# ─── Tokens ────────────────────────────────────────────────────────────────
# Mirrors DESIGN.md. Kept as a dict so no hex is written twice in the stylesheet.
_TOKENS = {
    # The verification green, read from the badge table rather than copied, so a
    # change to the palette carries here automatically.
    "tick": STATUS_STYLES["verified"].highlight,
    "canvas": "#f6f5f4",   # {colors.canvas-soft} — the warm paper page
    "ink": "#000000",      # {colors.ink}
    "band": "#e6e6e6",     # {colors.hairline} — the band stays monochrome
    "muted": "#615d59",    # {colors.ink-muted}
}

#: Streamlit turns a widget ``key`` into a ``.st-key-<key>`` class on its
#: container. That generated hook is the documented way to position a native
#: widget with CSS, and it is what lets Enter be a real button rather than a
#: styled div pretending to be one.
_ENTER_KEY = "pl_splash_enter"

#: Length of the animated sequence. The overlay holds after this until Enter.
SEQUENCE_S = 4.0

_SPLASH_HTML = """
<div id="pl-splash" aria-hidden="true">
  <div class="pl-inner">
    <div class="pl-mark">
      <span class="pl-band"></span>
      <span class="pl-word">PaperLens</span>
    </div>
    <svg class="pl-tick" viewBox="0 0 24 24" width="38" height="38"
         fill="none" stroke="{tick}" stroke-width="3"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M4 12.5 L9.5 18 L20 6.5" />
    </svg>
  </div>
  <div class="pl-tagline">evidence-grounded research</div>
</div>

<style>
/* The st.html wrapper holds only an out-of-flow child; collapse it so the
   layout underneath gains no phantom gap. */
[data-testid="stHtml"]:has(#pl-splash) {{
  height: 0; margin: 0; padding: 0; overflow: visible;
}}

#pl-splash {{
  position: fixed;
  inset: 0;
  z-index: 9990;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  background: {canvas};

  /* Resting state is invisible and inert. The animation reveals it, and it then
     holds — there is no fade-out, because Enter is what ends the gate. If the
     animation never runs, nothing is shown and nothing is blocked. */
  opacity: 0;
  pointer-events: none;

  animation: pl-veil 0.45s ease-out forwards;
}}

.pl-inner {{
  display: flex;
  align-items: center;
  gap: 18px;
}}

.pl-mark {{
  position: relative;
  display: inline-block;
}}

.pl-word {{
  position: relative;
  z-index: 1;
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  font-weight: 700;
  font-size: clamp(40px, 7.5vw, 68px);
  /* DESIGN.md display-1 tracking, scaled to the clamped size. */
  letter-spacing: -0.033em;
  line-height: 1;
  color: {ink};
  animation: pl-rise 0.9s cubic-bezier(0.2, 0.8, 0.2, 1) 0.15s both;
}}

/* The highlight band, behind the lower half of the word — a highlighter pass,
   which is exactly what the app does to a verified quote. */
.pl-band {{
  position: absolute;
  left: -0.06em;
  bottom: 0.06em;
  height: 0.40em;
  width: 0;
  background: {band};
  z-index: 0;
  animation: pl-wipe 1.2s cubic-bezier(0.2, 0.8, 0.2, 1) 1.2s both;
}}

.pl-tick {{
  /* Slight overshoot, so it reads as a stamp rather than a fade. */
  animation: pl-stamp 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) 2.5s both;
}}

.pl-tagline {{
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 15px;
  font-weight: 400;
  letter-spacing: 0.01em;
  color: {muted};
  animation: pl-rise 0.9s cubic-bezier(0.2, 0.8, 0.2, 1) 0.45s both;
}}

/* Enter — a real Streamlit button, positioned over the overlay via the
   generated .st-key hook. It sits above #pl-splash and re-enables pointer
   events for itself only, so it is the single interactive thing on screen. */
.st-key-{enter_key} {{
  position: fixed;
  left: 50%;
  top: 62%;
  transform: translateX(-50%);
  z-index: 9999;
  pointer-events: auto;
  width: auto;
  /* Its own keyframes, not pl-rise: the button is centred by a transform, and
     animating `transform` would otherwise overwrite the centring. */
  animation: pl-rise-centred 0.7s cubic-bezier(0.2, 0.8, 0.2, 1) 3.3s both;
}}

@keyframes pl-veil {{
  from {{ opacity: 0; }}
  to   {{ opacity: 1; }}
}}

@keyframes pl-rise {{
  from {{ opacity: 0; transform: translateY(10px); }}
  to   {{ opacity: 1; transform: translateY(0); }}
}}

@keyframes pl-rise-centred {{
  from {{ opacity: 0; transform: translateX(-50%) translateY(10px); }}
  to   {{ opacity: 1; transform: translateX(-50%) translateY(0); }}
}}

@keyframes pl-wipe {{
  from {{ width: 0; }}
  to   {{ width: calc(100% + 0.12em); }}
}}

@keyframes pl-stamp {{
  from {{ opacity: 0; transform: scale(0.6); }}
  to   {{ opacity: 1; transform: scale(1); }}
}}

/* Reduced motion: show the finished state at once rather than removing the
   gate. Skipping the animation must not skip the way out of it. */
@media (prefers-reduced-motion: reduce) {{
  #pl-splash {{ animation: none; opacity: 1; }}
  .pl-word, .pl-tagline, .pl-tick {{ animation: none; opacity: 1; transform: none; }}
  .pl-band {{ animation: none; width: calc(100% + 0.12em); }}
  .st-key-{enter_key} {{
    animation: none;
    opacity: 1;
    transform: translateX(-50%);
  }}
}}
</style>
"""


def markup() -> str:
    """The rendered splash HTML. Exposed so tests can assert on it."""
    return _SPLASH_HTML.format(enter_key=_ENTER_KEY, **_TOKENS)


def is_open() -> bool:
    """True while the gate is still up."""
    return not st.session_state.get(state.SPLASH_DISMISSED, False)


def render_gate() -> None:
    """Hold the splash until the reader presses Enter.

    Unlike a timed splash, this re-renders on every script run while the gate is
    open — that is what makes it a gate rather than a flash. The only rerun that
    normally happens while it is up is the Enter click itself, which closes it.
    """
    if not is_open():
        return

    st.html(markup())

    if st.button(
        "Enter",
        key=_ENTER_KEY,
        type="primary",
        icon=":material/arrow_forward:",
        help="Open PaperLens.",
    ):
        st.session_state[state.SPLASH_DISMISSED] = True
        st.rerun()
