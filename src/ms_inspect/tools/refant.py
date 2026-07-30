"""
tools/refant.py — ms_refant

Selects the best reference antenna from a Measurement Set using geometry
and flagging heuristics. Read-only. Algorithm adapted from
evla_pipe.utils.RefAntHeuristics / RefAntGeometry / RefAntFlagging.

Two independent scores, each normalised to [0, n_antennas]:

  Geometry score:
    - Array centre = component-wise median of non-flagged antenna ECEF positions
    - geo_score[ant] = (1 - distance/max_distance) * n_antennas
    - Closest to centre, highest score
    - `distance_from_centre_m` (per antenna) and `max_distance_m` are also
      returned. In an extended configuration (VLA A-config, uGMRT long arms)
      the single most distant antenna sets max_distance_m to tens of
      kilometres, so every antenna within the compact core scores above
      ~0.94 * n_antennas and geo_score alone reads as saturated
      (near-binary central/not-central). These two fields let the reader see
      that saturation directly instead of inferring it from geo_score alone.

  Flagging score:
    - casatasks.flagdata(mode='summary', field=...) → per-antenna unflagged count
    - flag_score[ant] = (good[ant] / max_good) * n_antennas
    - Most unflagged data, highest score
    - This score is an average over all SpWs and both polarizations, so an
      antenna that is fully flagged in one SpW out of many but clean
      elsewhere still scores near the top. Because a reference antenna is
      used per SpW, a single bad SpW is enough to break or silently
      re-reference that SpW's solutions. `worst_spw_flag_frac`,
      `worst_spw_id`, `median_spw_flag_frac`, and `worst_spw_excess`
      (per antenna) surface that per-SpW structure next to the aggregate score.
    - Read `worst_spw_excess` (worst minus median), NOT the raw worst, to judge
      per-SpW health. Shadowing in a compact configuration (VLA C/D) flags an
      antenna across every SpW at once and preferentially hits the central
      antennas that geo_score ranks highest, so a perfectly good D-config
      candidate can show a 40% worst-SpW fraction with an excess near zero.
      Uniform flagging is already accounted for in flag_score. A large excess
      is one outlier SpW, which is the condition that actually disqualifies a
      reference antenna.
    - These come from one additional flagdata call in list mode (multiple named
      mode='summary' sub-commands, one per SpW, in a single casatasks
      invocation) rather than one flagdata call per SpW, to bound the runtime
      cost. It still roughly doubles this tool's runtime; pass
      per_spw_breakdown=False to skip it on a very large MS.

Combined score = geo_score + flag_score (when both enabled). Sort descending.
The full ranked list, including the per-item inputs above, is returned so the
skill can check the ranking against the numbers and fall back to refant[1].
"""

from __future__ import annotations

import numpy as np

from ms_inspect.util.casa_context import open_table, validate_ms_path
from ms_inspect.util.formatting import response_envelope

TOOL_NAME = "ms_refant"


# ---------------------------------------------------------------------------
# Score helpers — pure Python / numpy, no CASA dependency
# ---------------------------------------------------------------------------


def _distances_from_centre(
    positions: np.ndarray, flagged_rows: list[bool]
) -> tuple[np.ndarray, float]:
    """
    Compute each antenna's distance from the array centre and the max
    distance among active (non-flagged) antennas.

    Args:
        positions:    Shape (3, n_ant) ECEF XYZ in metres (output of tb.getcol).
        flagged_rows: Length n_ant booleans, True if FLAG_ROW is set.

    Returns:
        (distances, max_dist): distances is shape (n_ant,) float64, metres
        from the centre. max_dist is 0.0 if no antenna is active.
    """
    n_ant = positions.shape[1]
    active = np.array([not f for f in flagged_rows], dtype=bool)
    if not active.any():
        return np.zeros(n_ant, dtype=np.float64), 0.0

    pos_t = positions.T  # (n_ant, 3)
    centre = np.median(pos_t[active], axis=0)  # (3,)
    distances = np.linalg.norm(pos_t - centre, axis=1)  # (n_ant,)
    max_dist = float(distances[active].max())
    return distances, max_dist


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

    distances, max_dist = _distances_from_centre(positions, flagged_rows)

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


def _worst_spw_per_antenna(
    ant_names: list[str],
    spw_summaries: dict[str, dict],
) -> dict[str, dict[str, float | str | None]]:
    """
    Per-antenna per-SpW flag fractions, reduced to worst, median, and excess.

    The worst SpW alone is NOT interpretable on its own, and reading it that
    way biases against compact array configurations. Shadowing in VLA C and D
    config flags an antenna across every SpW at once, and it preferentially
    hits the antennas nearest the array centre, which are exactly the ones the
    geometry score ranks highest. Such an antenna can show a 40% worst-SpW
    flag fraction with nothing spectrally wrong with it, and rejecting it on
    that number would throw away the best refant candidates in D config.

    What actually disqualifies a reference antenna is SpW-LOCALIZED flagging: a
    dead front-end or a killed subband in one SpW of sixteen while the rest are
    clean. That shows up as the SPREAD across SpWs, not the absolute worst
    value. So this returns the median alongside the worst, plus their
    difference:

        worst_spw_excess = worst_spw_flag_frac - median_spw_flag_frac

    Near zero means the flagging is uniform across the band (shadowing,
    elevation, an antenna out for the whole track) and the aggregate flag score
    already accounts for it. Large means one SpW is an outlier, which is the
    condition that makes an otherwise well-scoring antenna an unsafe refant.

    All three values are returned so the skill can apply its own thresholds and
    check them against each other. Per DESIGN.md 1.1.1 the derived excess ships
    with both of its inputs.

    Args:
        ant_names:     Antenna names in ANTENNA table order.
        spw_summaries: {spw_id: flagdata(mode='summary', spw=spw_id) dict},
                       each expected to have an 'antenna' sub-dict keyed by
                       name with 'flagged' and 'total' counts.

    Returns:
        {antenna_name: {worst_spw_flag_frac, worst_spw_id,
                        median_spw_flag_frac, worst_spw_excess,
                        n_spw_measured}}. The fractions and the id are None for
        an antenna with no usable data in any SpW summary.
    """
    per_ant_fracs: dict[str, list[tuple[float, str]]] = {name: [] for name in ant_names}

    for spw_id, summary in spw_summaries.items():
        ant_summary: dict = summary.get("antenna", {}) if summary else {}
        for name in ant_names:
            stats = ant_summary.get(name)
            if not stats:
                continue
            total = float(stats.get("total", 0))
            flagged = float(stats.get("flagged", 0))
            if total <= 0:
                continue
            per_ant_fracs[name].append((flagged / total, spw_id))

    out: dict[str, dict[str, float | str | None]] = {}
    for name, fracs in per_ant_fracs.items():
        if not fracs:
            out[name] = {
                "worst_spw_flag_frac": None,
                "worst_spw_id": None,
                "median_spw_flag_frac": None,
                "worst_spw_excess": None,
                "n_spw_measured": 0,
            }
            continue
        worst_frac, worst_id = max(fracs, key=lambda t: t[0])
        median_frac = float(np.median([f for f, _ in fracs]))
        out[name] = {
            "worst_spw_flag_frac": worst_frac,
            "worst_spw_id": worst_id,
            "median_spw_flag_frac": median_frac,
            "worst_spw_excess": worst_frac - median_frac,
            "n_spw_measured": len(fracs),
        }
    return out


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------


def run(
    ms_path: str,
    field: str = "",
    use_geometry: bool = True,
    use_flagging: bool = True,
    per_spw_breakdown: bool = True,
) -> dict:
    """
    Select the best reference antenna from the MS.

    Args:
        ms_path:      Path to Measurement Set (usually a cal_only.ms).
        field:        CASA field selection string for flagging heuristic.
                      Empty string = all fields.
        use_geometry: Score by distance from array centre.
        use_flagging: Score by unflagged data fraction.
        per_spw_breakdown: Also compute worst_spw_flag_frac / worst_spw_id per
                      antenna. This costs a SECOND flagdata pass over the
                      selection (one list-mode call carrying one summary
                      sub-command per SpW), so it roughly doubles the runtime
                      of this tool. Worth it in almost every case, because an
                      antenna fully flagged in one SpW of sixteen still scores
                      near the top of the aggregate flag score and is an unsafe
                      refant. Set False on a very large MS where the caller has
                      already established per-SpW health another way.

    Returns:
        Standard response envelope with 'refant', 'refant_list',
        'max_distance_m' (labelled reference for the geometry-score
        normalisation), and 'ranked' (full per-antenna score breakdown,
        including 'distance_from_centre_m', 'worst_spw_flag_frac', and
        'worst_spw_id' alongside 'geo_score' / 'flag_score').
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
    distances, max_dist = _distances_from_centre(positions, flag_row)
    if use_geometry:
        geo_scores = _geo_score(positions, flag_row)
    else:
        geo_scores = np.zeros(n_ant, dtype=np.float64)

    # ------------------------------------------------------------------
    # Flagging score
    # ------------------------------------------------------------------
    flag_scores = np.zeros(n_ant, dtype=np.float64)
    _EMPTY_SPW_STATS: dict[str, float | str | None] = {
        "worst_spw_flag_frac": None,
        "worst_spw_id": None,
        "median_spw_flag_frac": None,
        "worst_spw_excess": None,
        "n_spw_measured": 0,
    }
    worst_spw: dict[str, dict[str, float | str | None]] = {
        name: dict(_EMPTY_SPW_STATS) for name in ant_names
    }
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

            # Per-SpW breakdown. flagdata(mode='summary') aggregates over all
            # SpWs, which hides an antenna that is fully flagged in one SpW
            # but clean elsewhere. A true per-antenna-per-SpW cross tab is
            # not offered by a single flagdata(mode='summary') call, and
            # issuing one flagdata call per SpW does not bound the cost for
            # a 16-SpW dataset. Instead, one additional flagdata call in
            # mode='list' with one named mode='summary' sub-command per SpW
            # returns the per-antenna breakdown for every SpW from a single
            # casatasks invocation.
            spw_ids = sorted(summary.get("spw", {}).keys(), key=lambda s: (len(s), s))
            if spw_ids and per_spw_breakdown:
                try:
                    list_kwargs: dict = dict(
                        vis=ms_str,
                        mode="list",
                        flagbackup=False,
                        savepars=False,
                        action="calculate",
                    )
                    field_clause = f" field='{field}'" if field else ""
                    inpfile = [
                        f"mode='summary' spw='{spw_id}'{field_clause} name='spw{spw_id}'"
                        for spw_id in spw_ids
                    ]
                    list_kwargs["inpfile"] = inpfile
                    list_result = flagdata(**list_kwargs)
                    casa_calls.append(
                        "casatasks.flagdata(vis=..., mode='list', "
                        f"inpfile=<{len(spw_ids)} per-SpW mode='summary' commands>)"
                    )
                    spw_summaries = {
                        spw_id: list_result.get(f"spw{spw_id}", {}) for spw_id in spw_ids
                    }
                    worst_spw = _worst_spw_per_antenna(ant_names, spw_summaries)
                except Exception as e:
                    warnings.append(
                        f"Per-SpW flagdata(mode='list') breakdown failed ({e}); "
                        "worst_spw_flag_frac / worst_spw_id are unavailable."
                    )

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
        name = ant_names[ant_idx]
        spw_stats = worst_spw.get(name, _EMPTY_SPW_STATS)
        worst_frac = spw_stats["worst_spw_flag_frac"]
        median_frac = spw_stats["median_spw_flag_frac"]
        excess = spw_stats["worst_spw_excess"]
        ranked.append(
            {
                "antenna": name,
                "geo_score": round(float(geo_scores[ant_idx]), 4),
                "flag_score": round(float(flag_scores[ant_idx]), 4),
                "combined_score": round(float(combined[ant_idx]), 4),
                "rank": rank_idx + 1,
                "flag_row": bool(flag_row[ant_idx]),
                "distance_from_centre_m": round(float(distances[ant_idx]), 2),
                # Read worst_spw_excess, not worst_spw_flag_frac, to judge
                # per-SpW health. A high worst with a comparable median is
                # uniform flagging (shadowing in a compact config, low
                # elevation) and is already in flag_score. A high excess is one
                # outlier SpW, which is what disqualifies a refant.
                "worst_spw_flag_frac": round(worst_frac, 4)
                if isinstance(worst_frac, float)
                else None,
                "worst_spw_id": spw_stats["worst_spw_id"],
                "median_spw_flag_frac": round(median_frac, 4)
                if isinstance(median_frac, float)
                else None,
                "worst_spw_excess": round(excess, 4) if isinstance(excess, float) else None,
                "n_spw_measured": spw_stats["n_spw_measured"],
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
        "use_geometry": use_geometry,
        "use_flagging": use_flagging,
        # Distinguishes "no per-SpW data because it was not asked for" from
        # "no per-SpW data because the call failed" (the latter adds a warning).
        "per_spw_breakdown": per_spw_breakdown,
        "field_selection": field if field else "<all fields>",
        "max_distance_m": fmt_field(
            round(max_dist, 2),
            note="Distance of the single most distant active antenna from the array "
            "centre. geo_score is normalised against this value, so in an extended "
            "configuration (e.g. VLA A-config, uGMRT) it can be tens of kilometres "
            "while most antennas sit within a compact core, and geo_score saturates "
            "near n_antennas for all of them. Compare against each antenna's "
            "distance_from_centre_m in `ranked`.",
        ),
        "ranked": ranked,
    }

    return response_envelope(
        tool_name=TOOL_NAME,
        ms_path=ms_path,
        data=data,
        warnings=warnings,
        casa_calls=casa_calls,
    )
