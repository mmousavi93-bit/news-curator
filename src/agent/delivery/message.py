"""Message data model for Telegram delivery. Dataclasses only, no I/O.

The composer (a later phase) decides what matters and hands it down as
explicit `priority` values -- the formatter and budget layers must never
guess importance from content. Convention: LOWER priority number = MORE
important = kept longer when the message has to be cut down to fit
Telegram's 4,096-character cap (see budget.py).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Item:
    """One line item in a delivered message (an event, a watchlist entry).

    `detail` is optional colour that can be dropped before the item itself
    is dropped. `url`, if set, turns the headline into a link instead of
    bold text.
    """

    headline: str
    priority: int
    detail: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class Message:
    """A message the pipeline wants delivered. `header` carries the alert
    level / timestamp / run marker and is never dropped by the budget layer.
    `footer` is boilerplate (e.g. a run id) and is dropped silently if there
    is no room -- it is not itemised content, so constraint 11 (never invent
    or silently discard content) does not apply to it.
    """

    header: str
    items: tuple[Item, ...] = ()
    footer: str | None = None
