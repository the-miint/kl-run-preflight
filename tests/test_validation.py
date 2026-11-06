"""Tests for run_preflight.validation."""

from __future__ import annotations

import unittest
from typing import Literal

from run_preflight.constants import PlatformSpecificSampleKind
from run_preflight.validation import require_literal_member, strip_text


class TestRequireLiteralMember(unittest.TestCase):
    def test_require_literal_member(self):
        # A member passes silently, so the call returns None
        result = require_literal_member(
            "pacbio", PlatformSpecificSampleKind, "sample kind"
        )
        self.assertIsNone(result)

    def test_require_literal_member_rejects_non_member(self):
        # The message names the label, the value, and every member
        with self.assertRaisesRegex(
            ValueError,
            r"^Unsupported sample kind 'nanopore'; "
            r"supported: \['illumina', 'pacbio', 'tellseq'\]$",
        ):
            require_literal_member(
                "nanopore", PlatformSpecificSampleKind, "sample kind"
            )

    def test_require_literal_member_sorts_members(self):
        # Members are listed sorted, whatever their declaration order
        unsorted_kind = Literal["zeta", "alpha"]
        with self.assertRaisesRegex(ValueError, r"supported: \['alpha', 'zeta'\]$"):
            require_literal_member("beta", unsorted_kind, "letter")


class TestStripText(unittest.TestCase):
    def test_strip_text(self):
        # Whitespace and invisible format characters go from both ends,
        # even interleaved; interior characters are kept
        cases = {
            " X ": "X",
            "\tX\r\n": "X",
            "\u00a0X\u3000": "X",
            "\u200bX\ufeff": "X",
            "\u2060X\u180e": "X",
            "\x00X\x00": "X",
            " \u200b \ufeffX \u2060 ": "X",
            "A B\u200bC": "A B\u200bC",
            " \u200b\t\ufeff ": "",
        }
        results = {value: strip_text(value) for value in cases}
        self.assertEqual(results, cases)
