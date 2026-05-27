"""Bulk round-trip test: try every non-invalid, non-instrument CSV."""

from __future__ import annotations

import tempfile
import traceback
from pathlib import Path

from run_preflight.legacy.roundtrip import roundtrip_via_api

# TODO: put in path to directory with lots of legacy CSV sample sheets
SHEET_DIR_STR = ""
_SHEET_DIR = Path(SHEET_DIR_STR)


def roundtrip_csv(csv_path: Path) -> tuple[str, str | None]:
    """Return (status, detail) for a single CSV file.

    Status is one of "match", "mismatch", or "error".
    Detail is None for match, a diff summary for mismatch, or a
    traceback line for error.
    """
    try:
        with tempfile.TemporaryDirectory() as tmp:
            normalized, reconstructed = roundtrip_via_api(csv_path, Path(tmp))

        if normalized == reconstructed:
            return "match", None

        # if successful completion but not matching,
        # find first differing line for a useful summary
        norm_lines = normalized.splitlines()
        recon_lines = reconstructed.splitlines()
        for i, (n, r) in enumerate(zip(norm_lines, recon_lines)):
            if n != r:
                return "mismatch", (
                    f"line {i + 1}:\n    expected:  {n}\n    got:       {r}"
                )

        # All overlap matches: distinguish the spurious-trailing-row case
        # (recon has exactly one extra final row that is comma-only) from
        # a generic line-count divergence.
        if len(recon_lines) == len(norm_lines) + 1 and set(recon_lines[-1]) == {","}:
            return "mismatch", "extra trailing comma-only row in reconstructed output"

        return "mismatch", (
            f"line count differs: expected {len(norm_lines)}, got {len(recon_lines)}"
        )
    except Exception:
        last_line = traceback.format_exc().strip().splitlines()[-1]
        return "error", last_line


def main():
    # Validate that SHEET_DIR_STR has been set to a real path
    if not str(SHEET_DIR_STR):
        raise ValueError(
            "SHEET_DIR is empty; set it to a directory of legacy CSV sample sheets"
        )

    # Collect eligible CSV files
    csvs = sorted(
        p
        for p in _SHEET_DIR.glob("*.csv")
        if "invalid" not in p.name.lower() and "instrument" not in p.name.lower()
    )

    matched = []
    mismatched = []
    errored = []

    for csv_path in csvs:
        status, detail = roundtrip_csv(csv_path)
        if status == "match":
            matched.append(csv_path.name)
        elif status == "mismatch":
            mismatched.append((csv_path.name, detail))
        else:
            errored.append((csv_path.name, detail))

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"MATCHED:    {len(matched)} / {len(csvs)}")
    print(f"MISMATCHED: {len(mismatched)} / {len(csvs)}")
    print(f"ERRORED:    {len(errored)} / {len(csvs)}")

    if matched:
        print("\n--- Matched ---")
        for name in matched:
            print(f"  {name}")

    if mismatched:
        print("\n--- Mismatched ---")
        for name, detail in mismatched:
            print(f"  {name}")
            print(f"    {detail}")

    if errored:
        print("\n--- Errored ---")
        for name, detail in errored:
            print(f"  {name}")
            print(f"    {detail}")


if __name__ == "__main__":
    main()
