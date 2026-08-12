"""UTF-16 length counting and priority-based fitting for Telegram's
4,096-character cap (CLAUDE.md constraint #8).

Telegram counts message length in UTF-16 code units, not Python `len()` and
not UTF-8 bytes. Persian/Arabic/Hebrew text stays 1:1 with Python `len()`
(all BMP), but emoji and other astral-plane characters need 2 UTF-16 units
for 1 Python character -- get this wrong and a message that looks like it
fits under a naive count fails to send.

Fitting works on whole rendered fragments only. A fragment (header, an
item's full or headline-only form, footer) is either included whole or
dropped whole -- never sliced -- so an HTML tag emitted by formatter.py can
never be split. The one exception is the header, which is always plain
text with no markup (formatter.py never wraps it in a tag), so truncating
it as a last resort is safe.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_UNITS = 4096
DEFAULT_MAX_MESSAGES = 3

_SEPARATOR = "\n\n"
_OVERFLOW_TEMPLATE = "… +{n} more"  # "… +7 more"


def utf16_len(text: str) -> int:
    """Length in UTF-16 code units -- what Telegram's cap actually counts."""
    return len(text.encode("utf-16-le")) // 2


def utf16_truncate(text: str, max_units: int) -> str:
    """Truncate to at most `max_units` UTF-16 code units without splitting a
    surrogate pair. Only ever called on plain text with no markup (the
    header) -- never on a string that already contains HTML tags."""
    if max_units <= 0:
        return ""
    encoded = text.encode("utf-16-le")
    if len(encoded) // 2 <= max_units:
        return text
    return encoded[: max_units * 2].decode("utf-16-le", errors="ignore")


@dataclass(frozen=True, slots=True)
class Fragment:
    """One item, already rendered to HTML by formatter.py.

    `body` is the full rendered form (headline + detail line, if any);
    `headline_only` is the headline alone. Both are whole, self-contained
    HTML fragments -- fitting never slices into either.
    """

    priority: int
    order: int
    body: str
    headline_only: str


def _ensure_fits(text: str, max_units: int) -> str:
    if utf16_len(text) > max_units:
        return utf16_truncate(text, max_units)
    return text


def _append_footer(body: str, footer: str, max_units: int) -> str:
    if not footer:
        return body
    candidate = body + _SEPARATOR + footer
    if utf16_len(candidate) <= max_units:
        return candidate
    return body  # footer is boilerplate, not itemised content -- drop silently


def _greedy_pack(
    fragments: list[Fragment], budget: int, *, is_final: bool
) -> tuple[dict[int, str], list[Fragment], int]:
    """One greedy pass: ranked-priority order, whole fragments only.
    Returns (chosen-by-order, leftover, dropped-count)."""
    chosen: dict[int, str] = {}
    leftover: list[Fragment] = []
    dropped = 0
    for frag in fragments:
        full = _SEPARATOR + frag.body
        if utf16_len(full) <= budget:
            chosen[frag.order] = full
            budget -= utf16_len(full)
            continue
        headline = _SEPARATOR + frag.headline_only
        fits_headline_only = frag.headline_only != frag.body and utf16_len(headline) <= budget
        if fits_headline_only:
            chosen[frag.order] = headline
            budget -= utf16_len(headline)
            continue
        if is_final:
            dropped += 1
        else:
            leftover.append(frag)
    return chosen, leftover, dropped


def _pack_page(
    header: str, fragments: list[Fragment], max_units: int, *, is_final: bool
) -> tuple[str, list[Fragment]]:
    """Fill one message body from `header` + as many `fragments` as fit,
    ranked best-priority-first. An item that doesn't fit in full is retried
    headline-only before being dropped. On a non-final page, an item that
    doesn't fit at all is deferred to the next page instead of being
    dropped. On the final page, anything still unresolved is dropped and
    counted into an honest overflow marker.

    The marker's room is reserved up front, sized for the longest marker
    this page could ever need (`dropped` cannot exceed `len(fragments)`, and
    a marker only gets longer as its count gains digits) -- truncating the
    header, which is always plain text, if that is what it takes. That
    guarantees the marker always fits in the repack, so it is never stranded
    by a header that alone consumed most or all of the budget (brief
    requirement: an overflow marker must always be added, never silently
    dropped for lack of room).

    Returns (page_body, leftover_fragments) -- leftover is always empty when
    `is_final` is True.
    """
    header = _ensure_fits(header, max_units)
    base_budget = max_units - utf16_len(header)

    chosen, leftover, dropped = _greedy_pack(fragments, base_budget, is_final=is_final)

    if is_final and dropped:
        marker_reserve = utf16_len(_SEPARATOR + _OVERFLOW_TEMPLATE.format(n=len(fragments)))
        header_budget = max(max_units - marker_reserve, 0)
        if utf16_len(header) > header_budget:
            header = utf16_truncate(header, header_budget)
        item_budget = max(max_units - utf16_len(header) - marker_reserve, 0)
        chosen, leftover, dropped = _greedy_pack(fragments, item_budget, is_final=is_final)
        marker = _SEPARATOR + _OVERFLOW_TEMPLATE.format(n=dropped)  # count may have grown
        prefix = header + "".join(chosen[order] for order in sorted(chosen))
        # marker_reserve was sized for len(fragments) digits, not the (possibly
        # smaller) actual `dropped` count, and max_units itself may be too
        # small to hold the marker at all -- clamp so the invariant (returned
        # string never exceeds max_units) holds unconditionally, even at
        # max_units=0, rather than trusting the reservation above.
        marker_budget = max(max_units - utf16_len(prefix), 0)
        marker = utf16_truncate(marker, marker_budget)
        body = prefix + marker
        return body, leftover

    body = header + "".join(chosen[order] for order in sorted(chosen))
    return body, leftover


def fit_single(header: str, fragments: list[Fragment], footer: str, max_units: int) -> str:
    """Assemble exactly one message body <= max_units, dropping
    lowest-priority items (and then details) first, with an honest overflow
    marker if anything was dropped entirely."""
    ranked = sorted(fragments, key=lambda f: (f.priority, f.order))
    body, _leftover = _pack_page(header, ranked, max_units, is_final=True)
    return _append_footer(body, footer, max_units)


def fit_split(
    header: str,
    fragments: list[Fragment],
    footer: str,
    max_units: int,
    max_messages: int = DEFAULT_MAX_MESSAGES,
) -> list[str]:
    """Assemble up to `max_messages` message bodies, each <= max_units.
    Continuation pages repeat the header with a "(cont.)" marker so every
    page is self-contained. The last page (or the page that empties the
    remaining fragments) applies the same drop-and-mark fallback as
    `fit_single` for anything that still doesn't fit."""
    remaining = sorted(fragments, key=lambda f: (f.priority, f.order))
    pages: list[str] = []
    for i in range(max_messages):
        is_final = i == max_messages - 1
        page_header = header if i == 0 else f"{header} (cont.)"
        body, remaining = _pack_page(page_header, remaining, max_units, is_final=is_final)
        pages.append(body)
        if not remaining:
            break
    pages[-1] = _append_footer(pages[-1], footer, max_units)
    return pages
