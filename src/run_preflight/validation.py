"""Validate and normalize caller-supplied text and closed-set values."""

from __future__ import annotations

from typing import get_args

from .constants import INVISIBLE_FORMAT_CHARS


def require_literal_member(value: str, literal_type: object, label: str) -> None:
    """Raise ValueError unless *value* is a member of *literal_type*.

    *label* names the kind of value in the error message.
    """
    members = get_args(literal_type)
    if value not in members:
        raise ValueError(f"Unsupported {label} {value!r}; supported: {sorted(members)}")


def strip_text(value: str) -> str:
    """Return *value* without surrounding whitespace or invisible format characters.

    Whitespace is what ``str.strip`` removes. Interleaved runs of whitespace
    and format characters are removed entirely.
    """
    # Alternate the two strips until neither removes anything
    stripped = value
    previous = None
    while stripped != previous:
        previous = stripped
        stripped = stripped.strip().strip(INVISIBLE_FORMAT_CHARS)
    return stripped


def strip_str(value: object, param_name: str) -> str:
    """Return str *value* stripped as by strip_text; raise TypeError for non-str."""
    if not isinstance(value, str):
        raise TypeError(f"{param_name} must be a str, not {type(value).__name__}")
    stripped = strip_text(value)
    return stripped
