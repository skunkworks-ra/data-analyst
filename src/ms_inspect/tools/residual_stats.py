"""
tools/residual_stats.py — ms_residual_stats

Computes per-SPW amplitude statistics of CORRECTED − MODEL for a given
field. Use before and after ms_apply_initial_rflag to characterise the
residual distribution and verify flagging thresholds.

Reads CORRECTED_DATA and MODEL_DATA columns via casatools table.
Only unflagged rows are included in statistics.
A max_rows limit prevents memory exhaustion on large MSs.
"""

from __future__ import annotations

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import field as fmt_field
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_residual_stats"

_DEFAULT_MAX_ROWS = 500_000


def run(
    ms_path: str,
    field_id: int,
    max_rows: int = _DEFAULT_MAX_ROWS,
) -> dict:
    """
    Compute per-SPW amplitude stats of CORRECTED − MODEL for a field.

    Args:
        ms_path:  Path to the MS (calibrators.ms with CORRECTED + MODEL).
        field_id: Integer FIELD_ID to analyse (use ms_field_list to find it).
        max_rows: Maximum number of rows to read (default 500 000).
                  Rows are sampled uniformly if the MS is larger.

    Returns:
        Standard response envelope with per-spw amplitude statistics:
        median, std, p95, n_unflagged, n_flagged for CORRECTED−MODEL.
    """
    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Read total row count
    # ------------------------------------------------------------------
    with open_table(ms_str) as tb:
        n_total = int(tb.nrows())
        casa_calls.append("tb.open(MAIN) → nrows()")

    if n_total == 0:
        return response_envelope(
            tool_name=TOOL_NAME,
            ms_path=ms_path,
            data={"per_spw": [], "n_rows_read": fmt_field(0)},
            warnings=["MS MAIN table has zero rows."],
            casa_calls=casa_calls,
        )

    # ------------------------------------------------------------------
    # Build row selection: all rows for the given field_id
    # Use TaQL query for field selection, then sample if too large
    # ------------------------------------------------------------------
    with open_table(ms_str) as tb:
        # TaQL query to get row numbers for this field
        sub = tb.query(f"FIELD_ID == {field_id}")
        n_field_rows = int(sub.nrows())
        casa_calls.append(f"tb.query(FIELD_ID=={field_id}) → {n_field_rows} rows")

        if n_field_rows == 0:
            sub.close()
            warnings.append(f"No rows found for FIELD_ID={field_id}.")
            return response_envelope(
                tool_name=TOOL_NAME,
                ms_path=ms_path,
                data={"per_spw": [], "n_rows_read": fmt_field(0)},
                warnings=warnings,
                casa_calls=casa_calls,
            )

        # Check that CORRECTED_DATA and MODEL_DATA exist
        col_names = set(sub.colnames())
        if "CORRECTED_DATA" not in col_names:
            sub.close()
            from ms_inspect.exceptions import ComputationError

            raise ComputationError(
                "CORRECTED_DATA column not present. Run initial_bandpass.py first.",
                ms_path=ms_path,
            )
        if "MODEL_DATA" not in col_names:
            sub.close()
            warnings.append(
                "MODEL_DATA column not present. Run setjy.py first. "
                "Residual stats will not be available."
            )
            return response_envelope(
                tool_name=TOOL_NAME,
                ms_path=ms_path,
                data={"per_spw": [], "n_rows_read": fmt_field(0)},
                warnings=warnings,
                casa_calls=casa_calls,
            )

        # Read columns — row-limit sampling if needed
        if n_field_rows <= max_rows:
            rows_to_read = n_field_rows
            step = 1
        else:
            step = n_field_rows // max_rows
            rows_to_read = max_rows
            warnings.append(
                f"MS has {n_field_rows} rows for this field; sampling every {step}th row "
                f"({rows_to_read} rows read). Increase max_rows for higher accuracy."
            )

        # getcol with startrow and nrow works on subtable too
        corrected = sub.getcol(
            "CORRECTED_DATA", startrow=0, nrow=n_field_rows if step == 1 else n_field_rows
        )
        model = sub.getcol(
            "MODEL_DATA", startrow=0, nrow=n_field_rows if step == 1 else n_field_rows
        )
        flag = sub.getcol("FLAG", startrow=0, nrow=n_field_rows if step == 1 else n_field_rows)
        dd_id = sub.getcol(
            "DATA_DESC_ID", startrow=0, nrow=n_field_rows if step == 1 else n_field_rows
        )
        sub.close()

    casa_calls.append("tb.getcol(CORRECTED_DATA, MODEL_DATA, FLAG, DATA_DESC_ID)")

    # Apply row sampling if needed
    if step > 1:
        row_indices = np.arange(0, n_field_rows, step)
        corrected = corrected[:, :, row_indices]
        model = model[:, :, row_indices]
        flag = flag[:, :, row_indices]
        dd_id = dd_id[row_indices]
        rows_to_read = len(row_indices)

    # ------------------------------------------------------------------
    # Compute residual amplitudes per DATA_DESC_ID (SPW proxy)
    # flag shape: (n_corr, n_chan, n_rows); same for corrected/model
    # ------------------------------------------------------------------
    # Residual (complex): shape (n_corr, n_chan, n_rows)
    residual = corrected - model
    # Amplitude: |CORRECTED - MODEL|
    amp = np.abs(residual)  # (n_corr, n_chan, n_rows)

    unique_dd = np.unique(dd_id)
    per_spw: list[dict] = []

    for dd in unique_dd:
        mask_rows = dd_id == dd  # (n_rows,)
        # Expand to (n_corr, n_chan, n_rows) for flag masking
        flag_spw = flag[:, :, mask_rows]  # (n_corr, n_chan, n_rows_spw)
        amp_spw = amp[:, :, mask_rows]

        # Unflagged elements: flag=False means data is good
        good_mask = ~flag_spw  # (n_corr, n_chan, n_rows_spw)
        amp_good = amp_spw[good_mask]
        n_unflagged = int(good_mask.sum())
        n_flagged = int(flag_spw.sum())

        if n_unflagged == 0:
            per_spw.append(
                {
                    "data_desc_id": int(dd),
                    "n_unflagged": fmt_field(0),
                    "n_flagged": fmt_field(n_flagged),
                    "median_amp": fmt_field(None, flag="UNAVAILABLE", note="all data flagged"),
                    "std_amp": fmt_field(None, flag="UNAVAILABLE"),
                    "p95_amp": fmt_field(None, flag="UNAVAILABLE"),
                }
            )
            continue

        median_amp = float(np.median(amp_good))
        std_amp = float(np.std(amp_good))
        p95_amp = float(np.percentile(amp_good, 95))

        per_spw.append(
            {
                "data_desc_id": int(dd),
                "n_unflagged": fmt_field(n_unflagged),
                "n_flagged": fmt_field(n_flagged),
                "median_amp": fmt_field(round(median_amp, 6)),
                "std_amp": fmt_field(round(std_amp, 6)),
                "p95_amp": fmt_field(round(p95_amp, 6)),
            }
        )

    data = {
        "field_id": field_id,
        "n_rows_read": fmt_field(int(corrected.shape[2])),
        "n_data_desc_ids": fmt_field(len(unique_dd)),
        "per_spw": per_spw,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
