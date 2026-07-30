"""Locating structural blocks in extracted page text.

Shared because two callers need the same answer from the same input: the local
fallback parser, and the adapter that repairs the real parser's output before the
citation extractor sees it.

The repair matters more than it sounds. The shipped parser emits ``sections`` as
``[id, title, level, page_start, page_end]`` — **no text** — and no
``references_text``, while the citation extractor looks for a section literally
named "References" and reads its ``text``. The two never meet, so extraction
returned **zero** references on a paper that has forty.
"""

from __future__ import annotations

import re

_REFERENCE_HEADING = re.compile(
    r"^\s*(references|bibliography|works cited|literature cited)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def references_text(full_text_by_page: dict[int, str]) -> str:
    """Everything from the 'References' heading to the end of the document.

    Takes whole pages after the heading rather than stopping at the next one,
    because a reference list spans several pages and truncating it at the first
    page boundary is how a 40-entry list becomes a 4-entry one.

    Returns ``""`` when no heading is found, so callers can tell "nothing here"
    from "everything here".
    """
    if not full_text_by_page:
        return ""

    ordered = sorted(full_text_by_page)
    for page_no in ordered:
        page_text = full_text_by_page.get(page_no) or ""
        match = _REFERENCE_HEADING.search(page_text)
        if not match:
            continue
        parts = [page_text[match.end():]]
        parts.extend(full_text_by_page.get(p) or "" for p in ordered if p > page_no)
        return "\n".join(parts)
    return ""
