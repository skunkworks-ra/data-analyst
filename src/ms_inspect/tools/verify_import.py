"""
tools/verify_import.py — ms_verify_import

Checks that ms_import_asdm completed successfully:
  1. Output MS directory exists and contains a table.info file (valid MS).
  2. Online flag file exists and is non-empty.

No CASA dependency — pure filesystem checks.
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_verify_import"


def run(ms_path: str, online_flag_file: str) -> dict:
    """
    Verify that importasdm produced a valid MS and online flag file.

    Args:
        ms_path:           Expected path to the output MS directory.
        online_flag_file:  Expected path to the .flagonline.txt file.

    Returns:
        Standard response envelope with ms_exists, ms_valid, flag_file_exists,
        flag_file_n_lines, and ready_for_preflag.
    """
    warnings: list[str] = []
    casa_calls: list[str] = ["filesystem_check(ms_path, online_flag_file)"]

    ms = Path(ms_path)
    flag_file = Path(online_flag_file)

    ms_exists = ms.exists() and ms.is_dir()
    ms_valid = ms_exists and (ms / "table.info").exists()

    if not ms_exists:
        warnings.append(f"MS directory not found: {ms_path}")
    elif not ms_valid:
        warnings.append(
            f"Directory exists but missing table.info — may not be a valid MS: {ms_path}"
        )

    flag_exists = flag_file.exists() and flag_file.is_file()
    flag_n_lines = 0
    if flag_exists:
        raw = flag_file.read_text()
        flag_n_lines = len(
            [ln for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        )
        if flag_n_lines == 0:
            warnings.append(
                f"Online flag file exists but contains no flag commands: {online_flag_file}"
            )
    else:
        warnings.append(f"Online flag file not found: {online_flag_file}")

    ready = ms_valid and flag_exists and flag_n_lines > 0

    data = {
        "ms_path": fmt_field(str(ms)),
        "ms_exists": fmt_field(ms_exists),
        "ms_valid": fmt_field(ms_valid),
        "online_flag_file": fmt_field(str(flag_file)),
        "flag_file_exists": fmt_field(flag_exists),
        "flag_file_n_commands": fmt_field(flag_n_lines),
        "ready_for_preflag": fmt_field(ready),
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
