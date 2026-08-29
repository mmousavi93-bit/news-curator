"""Leaf type checks shared by the settings validators.

Split out of settings.py because settings_llm.py's nested validator needs
the same checks, and neither module may import the other (settings.py
imports settings_llm.py). Imports nothing -- no cycles possible.
"""

from __future__ import annotations

import collections.abc as cabc
import typing
from typing import Any


def _type_name(expected: Any) -> str:
    return expected.__name__ if isinstance(expected, type) else str(expected)


def _type_matches(value: Any, expected: Any) -> bool:
    """True if `value`'s runtime type matches the leaf annotation `expected`.

    Two traps this exists to close, both real in PyYAML output:
      - `isinstance(True, int)` is True, so a naive int/float check would
        silently accept `max_calls_per_run: true`. bool is checked and
        rejected explicitly, for both int and float fields.
      - A numeric-looking string is never coerced. If the field expects
        int/float and the value is a str, that is a mismatch, full stop --
        coercion would hide a config typo instead of failing on it.
    """
    origin = typing.get_origin(expected)
    if origin is not None:
        if isinstance(origin, type) and issubclass(origin, cabc.Mapping):
            return isinstance(value, dict)
        if isinstance(origin, type) and issubclass(origin, cabc.Sequence):
            return isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes))
        return isinstance(value, origin)
    if expected is Any:
        return True
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is str:
        return isinstance(value, str)
    return isinstance(value, expected)
