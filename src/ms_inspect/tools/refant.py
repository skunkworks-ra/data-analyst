"""
tools/refant.py — ms_refant

Selects the best reference antenna from a Measurement Set using geometry
and flagging heuristics. Read-only. Algorithm adapted from
evla_pipe.utils.RefAntHeuristics / RefAntGeometry / RefAntFlagging.

Two independent scores, each normalised to [0, n_antennas]:

  Geometry score:
    - Array centre = component-wise median of non-flagged antenna ECEF positions
    - geo_score[ant] = (1 - distance/max_distance) * n_antennas
    - Closest to centre → highest score

  Flagging score:
    - casatasks.flagdata(mode='summary', field=...) → per-antenna unflagged count
    - flag_score[ant] = (good[ant] / max_good) * n_antennas
    - Most unflagged data → highest score

Combined score = geo_score + flag_score (when both enabled). Sort descending.
The full ranked list is returned so the skill can fall back to refant[1].

geo_score's normalisation is set by the single most distant unflagged antenna,
so in an extended configuration it saturates across the whole core and the
ranking there is decided by a term carrying almost no information. The
response therefore also carries `distance_from_centre_m` per antenna and
`max_distance_m` at top level — geo_score's own inputs — so that saturation is
visible rather than hidden. Ranking, weighting and sort order are unchanged.
"""

from __future__ import annotations

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_refant"


# ---------------------------------------------------------------------------
# Score helpers — pure Python / numpy, no CASA dependency
# ---------------------------------------------------------------------------


def _geo_distances(positions: np.ndarray, flagged_rows: list[bool]) -> tuple[np.ndarray, float]:
    """
    Distance of each antenna from the array centre, and the normalising maximum.

    These are the *inputs* to _geo_score. They are returned in the response
    because geo_score alone hides its own saturation: the normalisation is set
    by the single most distant unflagged antenna, so in an extended
    configuration every antenna in the core scores above ~0.94 * n_ant and the
    geometry term collapses to a near-binary "central or not". Since geometry
    and flagging are summed with equal weight, the ranking near that boundary
    is then decided by a saturated term. distance_from_centre_m still separates
    the antennas that geo_score cannot.

    Args:
        positions:    Shape (3, n_ant) ECEF XYZ in metres (output of tb.getcol).
        flagged_rows: Length n_ant booleans — True if FLAG_ROW is set.

    Returns:
        (distances, max_dist) — distances shape (n_ant,) in metres, for every
        antenna including flagged ones; max_dist is the maximum over unflagged
        antennas only (0.0 if none are unflagged).
    """
    active = np.array([not f for f in flagged_rows], dtype=bool)
    if not active.any():
        return np.zeros(positions.shape[1], dtype=np.float64), 0.0

    # positions shape: (3, n_ant) → transpose to (n_ant, 3)
    pos_t = positions.T  # (n_ant, 3)
    centre = np.median(pos_t[active], axis=0)  # (3,)
    distances = np.linalg.norm(pos_t - centre, axis=1)  # (n_ant,)
    return distances, float(distances[active].max())


def _geo_score(positions: np.ndarray, flagged_rows: list[bool]) -> np.ndarray:
    """
    Compute geometry scores for each antenna.

    Args:
        positions:    Shape (3, n_ant) ECEF XYZ in metres (output of tb.getcol).
        flagged_rows: Length n_ant booleans — True if FLAG_ROW is set.

    Returns:
        scores: Shape (n_ant,) float64. Flagged antennas score 0.0.
    """
    n_ant = positions.shape[1]
    scores = np.zeros(n_ant, dtype=np.float64)

    active = np.array([not f for f in flagged_rows], dtype=bool)
    if not active.any():
        return scores  # all flagged — return zeros

    distances, max_dist = _geo_distances(positions, flagged_rows)

    if max_dist == 0.0:
        # All active antennas at the same position — equal scores
        scores[active] = n_ant
        return scores

    scores[active] = (1.0 - distances[active] / max_dist) * n_ant
    return scores


def _flag_score(
    ant_names: list[str],
    flagdata_summary: dict,
) -> np.ndarray:
    """
    Compute flagging scores from a casatasks.flagdata summary dict.

    Args:
        ant_names:        Antenna names in ANTENNA table order.
        flagdata_summary: Return value of flagdata(mode='summary').
                          Expected to have an 'antenna' sub-dict keyed by name,
                          each value a dict with 'flagged' and 'total' keys.

    Returns:
        scores: Shape (n_ant,) float64.
    """
    n_ant = len(ant_names)
    scores = np.zeros(n_ant, dtype=np.float64)

    ant_summary: dict = flagdata_summary.get("antenna", {})
    if not ant_summary:
        return scores

    good = np.zeros(n_ant, dtype=np.float64)
    for i, name in enumerate(ant_names):
        if name in ant_summary:
            stats = ant_summary[name]
            total = float(stats.get("total", 0))
            flagged = float(stats.get("flagged", 0))
            good[i] = total - flagged

    max_good = good.max()
    if max_good > 0:
        scores = (good / max_good) * n_ant

    return scores


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------


def run(
    ms_path: str,
    field: str = "",
    use_geometry: bool = True,
    use_flagging: bool = True,
) -> dict:
    """
    Select the best reference antenna from the MS.

    Args:
        ms_path:      Path to Measurement Set (usually a cal_only.ms).
        field:        CASA field selection string for flagging heuristic.
                      Empty string = all fields.
        use_geometry: Score by distance from array centre.
        use_flagging: Score by unflagged data fraction.

    Returns:
        Standard response envelope with 'refant', 'refant_list', and
        'ranked' array (full per-antenna score breakdown).
    """
    from ms_inspect.util.formatting import field as fmt_field  # avoid name clash

    p = validate_ms_path(ms_path)
    ms_str = str(p)
    casa_calls: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Read ANTENNA table: positions, names, FLAG_ROW
    # ------------------------------------------------------------------
    with open_table(ms_str + "/ANTENNA") as tb:
        casa_calls.append("tb.open(ANTENNA) → getcol(NAME, POSITION, FLAG_ROW)")
        ant_names: list[str] = list(tb.getcol("NAME"))
        positions: np.ndarray = tb.getcol("POSITION")  # shape (3, n_ant)
        try:
            flag_row: list[bool] = list(tb.getcol("FLAG_ROW"))
        except Exception:
            flag_row = [False] * len(ant_names)
            warnings.append(
                "FLAG_ROW column absent from ANTENNA subtable — assuming all antennas active."
            )

    n_ant = len(ant_names)

    # Report excluded antennas
    excluded = [ant_names[i] for i, f in enumerate(flag_row) if f]
    if excluded:
        warnings.append(
            f"Antennas excluded (FLAG_ROW=True): {excluded}. "
            "These are removed from the geometry score centre calculation."
        )

    # ------------------------------------------------------------------
    # Geometry score
    # ------------------------------------------------------------------
    if use_geometry:
        geo_scores = _geo_score(positions, flag_row)
        geo_distances, max_distance_m = _geo_distances(positions, flag_row)
    else:
        geo_scores = np.zeros(n_ant, dtype=np.float64)
        geo_distances, max_distance_m = np.zeros(n_ant, dtype=np.float64), 0.0

    # ------------------------------------------------------------------
    # Flagging score
    # ------------------------------------------------------------------
    flag_scores = np.zeros(n_ant, dtype=np.float64)
    if use_flagging:
        try:
            from casatasks import flagdata  # type: ignore[import]

            flagdata_kwargs: dict = dict(
                vis=ms_str,
                mode="summary",
                flagbackup=False,
                savepars=False,
            )
            if field:
                flagdata_kwargs["field"] = field

            summary = flagdata(**flagdata_kwargs)
            field_sel_str = field if field else "<all fields>"
            casa_calls.append(
                f"casatasks.flagdata(vis=..., mode='summary', field='{field_sel_str}')"
            )
            flag_scores = _flag_score(ant_names, summary)

        except ImportError:
            warnings.append(
                "casatasks not available — flagging score skipped. Geometry score only."
            )
            use_flagging = False
        except Exception as e:
            warnings.append(
                f"flagdata(mode='summary') failed ({e}). Falling back to geometry-only scoring."
            )
            use_flagging = False

    # ------------------------------------------------------------------
    # Combined score and ranking
    # ------------------------------------------------------------------
    combined = geo_scores + flag_scores
    rank_order = np.argsort(combined)[::-1]  # descending

    ranked = []
    for rank_idx, ant_idx in enumerate(rank_order):
        ranked.append(
            {
                "antenna": ant_names[ant_idx],
                "distance_from_centre_m": round(float(geo_distances[ant_idx]), 2),
                "geo_score": round(float(geo_scores[ant_idx]), 4),
                "flag_score": round(float(flag_scores[ant_idx]), 4),
                "combined_score": round(float(combined[ant_idx]), 4),
                "rank": rank_idx + 1,
                "flag_row": bool(flag_row[ant_idx]),
            }
        )

    refant_list = [r["antenna"] for r in ranked]
    best = refant_list[0] if refant_list else None

    # Completeness flag: COMPLETE if both heuristics used, INFERRED if only one
    if use_geometry and use_flagging:
        refant_flag = "COMPLETE"
    elif excluded:
        refant_flag = "PARTIAL"
    else:
        refant_flag = "INFERRED"

    data = {
        "refant": fmt_field(best, flag=refant_flag),
        "refant_list": fmt_field(refant_list, flag=refant_flag),
        "n_antennas": n_ant,
        # geo_score is (1 - distance/max_distance_m) * n_antennas, so it
        # saturates near 1 * n_antennas for the whole core whenever
        # max_distance_m is large. These two fields are that computation's
        # inputs; use them when the top of the ranking is closely spaced.
        "max_distance_m": round(float(max_distance_m), 2),
        "use_geometry": use_geometry,
        "use_flagging": use_flagging,
        "field_selection": field if field else "<all fields>",
        "ranked": ranked,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
