"""
pathguard.py — output-caltable path validation for ms_modify write tools.

Every tool that archives-and-rewrites an output caltable (gaincal, bandpass,
polcal, fluxscale, ...) must validate the user-supplied path with
validate_output_caltable() before embedding it in a generated script or
passing it to a CASA task. The generated scripts additionally carry a runtime
guard (SAFE_RM_TABLE_SNIPPET) so a stale script can never touch an MS, and so
a retry archives the previous attempt's caltable aside instead of deleting it.
"""

from __future__ import annotations

from pathlib import Path

from ms_inspect.exceptions import ComputationError


def _is_measurement_set(p: Path) -> bool:
    """True if p is an existing CASA table whose table.info marks it as an MS."""
    info = p / "table.info"
    if not info.is_file():
        return False
    try:
        first = info.read_text(errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return False
    return "Measurement Set" in first


def validate_output_caltable(
    caltable: str,
    workdir: str,
    ms_path: str,
) -> Path:
    """
    Validate an output caltable path that the tool will delete-and-rewrite.

    Raises ComputationError if the path:
      - resolves to the MS itself,
      - is an existing Measurement Set (per table.info),
      - is not inside workdir.

    Returns the resolved Path.
    """
    ct = Path(caltable).expanduser().resolve()
    wd = Path(workdir).expanduser().resolve()
    ms = Path(ms_path).expanduser().resolve()

    if ct == ms:
        raise ComputationError(
            f"Output caltable path equals the MS path ({ct}) — refusing: the "
            "caltable is deleted and rewritten on each run.",
            ms_path=ms_path,
        )
    if ct.exists() and _is_measurement_set(ct):
        raise ComputationError(
            f"Output caltable path {ct} is an existing Measurement Set — refusing to overwrite it.",
            ms_path=ms_path,
        )
    if not ct.is_relative_to(wd):
        raise ComputationError(
            f"Output caltable {ct} is not inside workdir {wd}. Caltables are "
            "archived aside and rewritten on each run, so they must live in "
            "workdir.",
            ms_path=ms_path,
        )
    return ct


# Runtime guard embedded in generated scripts. A retry must not destroy the
# only copy of what an earlier attempt produced (PLAN.md, "Where the trouble
# is" #1: a second gaincal used to overwrite the first gain.G with nothing to
# undo it). So this archives the existing table aside -- os.rename, atomic on
# the same filesystem, which workdir always is here -- rather than deleting
# it. Refuses to touch anything whose table.info identifies it as a
# Measurement Set, even if the script is edited or re-run against a changed
# filesystem.
SAFE_RM_TABLE_SNIPPET = '''\
def _safe_rm_table(path):
    """Archive an existing caltable aside; refuse to touch a Measurement Set.

    The prior attempt is renamed to "<path>.attemptN" (N picks the first free
    slot) rather than removed, so a retry's output never destroys the
    previous attempt's. Nothing reads the ".attemptN" name automatically --
    it exists so the file is recoverable, not so a later stage picks it up.
    """
    if not os.path.exists(path):
        return
    info = os.path.join(path, "table.info")
    if os.path.isfile(info):
        with open(info) as _fh:
            if "Measurement Set" in _fh.readline():
                raise RuntimeError(
                    f"Refusing to touch {path!r}: table.info identifies it "
                    "as a Measurement Set, not a caltable."
                )
    n = 1
    while os.path.exists(f"{path}.attempt{n}"):
        n += 1
    os.rename(path, f"{path}.attempt{n}")
'''
