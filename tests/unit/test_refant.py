"""
Unit tests for ms_refant scoring helpers.

No CASA required. Tests cover _geo_score and _flag_score with synthetic inputs.
"""

from __future__ import annotations

import numpy as np
import pytest

from ms_inspect.tools.refant import (
    _distances_from_centre,
    _flag_score,
    _geo_score,
    _worst_spw_per_antenna,
)

# ---------------------------------------------------------------------------
# _geo_score tests
# ---------------------------------------------------------------------------


class TestGeoScore:
    def test_centre_antenna_scores_highest(self):
        """
        3-antenna array: antenna 1 at centre, 0 and 2 equidistant outward.
        Antenna 1 should score highest.
        """
        # Positions shape (3, n_ant): X component only varies
        positions = np.array(
            [
                [-100.0, 0.0, 100.0],  # X
                [0.0, 0.0, 0.0],  # Y
                [0.0, 0.0, 0.0],  # Z
            ]
        )
        flags = [False, False, False]
        scores = _geo_score(positions, flags)

        assert scores[1] > scores[0]
        assert scores[1] > scores[2]
        # Outer two are equidistant → equal scores
        assert scores[0] == pytest.approx(scores[2])

    def test_equidistant_antennas_equal_scores(self):
        """
        4 antennas at corners of a square: (-1,-1), (-1,+1), (+1,-1), (+1,+1).
        Component-wise median = (0, 0) — the geometric centre.
        All are equidistant (sqrt(2)) → scores should be equal.
        """
        positions = np.array(
            [
                [-1.0, -1.0, 1.0, 1.0],  # X
                [-1.0, 1.0, -1.0, 1.0],  # Y
                [0.0, 0.0, 0.0, 0.0],  # Z
            ]
        )
        flags = [False, False, False, False]
        scores = _geo_score(positions, flags)

        assert scores[0] == pytest.approx(scores[1], abs=1e-6)
        assert scores[1] == pytest.approx(scores[2], abs=1e-6)
        assert scores[2] == pytest.approx(scores[3], abs=1e-6)

    def test_flagged_antenna_scores_zero(self):
        """Antennas with FLAG_ROW=True should score 0."""
        positions = np.array(
            [
                [-500.0, 0.0, 500.0],
                [0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0],
            ]
        )
        flags = [True, False, False]
        scores = _geo_score(positions, flags)

        assert scores[0] == pytest.approx(0.0)
        # Active antennas score >= 0
        assert scores[1] >= 0.0
        assert scores[2] >= 0.0

    def test_n_antennas_normalisation(self):
        """
        Maximum possible score for any antenna is n_antennas (for the antenna
        at the exact centre, distance=0). Centre antenna should return n_antennas.
        """
        # Antenna 0 at the same location as the median (the centre)
        positions = np.array(
            [
                [0.0, 0.0, 1000.0],
                [0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0],
            ]
        )
        flags = [False, False, False]
        scores = _geo_score(positions, flags)
        n_ant = positions.shape[1]

        # The median of [0, 0, 1000] = 0, so antenna 0 is at the centre
        assert scores[0] == pytest.approx(float(n_ant))


# ---------------------------------------------------------------------------
# _distances_from_centre tests
# ---------------------------------------------------------------------------


class TestDistancesFromCentre:
    def test_matches_geo_score_inputs(self):
        """distances/max_dist should reproduce what _geo_score computes internally."""
        positions = np.array(
            [
                [-100.0, 0.0, 100.0],
                [0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0],
            ]
        )
        flags = [False, False, False]
        distances, max_dist = _distances_from_centre(positions, flags)

        assert distances[1] == pytest.approx(0.0)
        assert distances[0] == pytest.approx(100.0)
        assert distances[2] == pytest.approx(100.0)
        assert max_dist == pytest.approx(100.0)

    def test_all_flagged_returns_zero_max_dist(self):
        positions = np.array([[0.0, 500.0], [0.0, 0.0], [0.0, 0.0]])
        distances, max_dist = _distances_from_centre(positions, [True, True])
        assert max_dist == 0.0
        assert np.all(distances == 0.0)

    def test_extended_configuration_saturates_geo_score(self):
        """
        Reproduces the VLA A-config / uGMRT failure mode: one very distant
        antenna sets max_distance_m, so every antenna in a compact core
        scores above ~0.94 * n_antennas even though their actual distances
        differ. distance_from_centre_m must expose that saturation.
        """
        # 5 antennas: one at ~18 km (sets max_dist), four within 1 km of
        # centre but at different distances from each other.
        positions = np.array(
            [
                [0.0, 200.0, 500.0, 900.0, 18000.0],
                [0.0, 0.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 0.0],
            ]
        )
        flags = [False] * 5
        distances, max_dist = _distances_from_centre(positions, flags)
        geo = _geo_score(positions, flags)

        assert max_dist == pytest.approx(17500.0, rel=1e-3)
        # The four core antennas have materially different distances...
        assert distances[0] != pytest.approx(distances[1])
        assert distances[1] != pytest.approx(distances[2])
        assert distances[2] != pytest.approx(distances[3])
        # ...but geo_score alone collapses them to near-identical, saturated
        # values close to n_antennas (5), which is exactly what
        # distance_from_centre_m / max_distance_m must make visible.
        n_ant = 5
        assert geo[0] > 0.94 * n_ant
        assert geo[3] > 0.94 * n_ant
        assert (geo[0] - geo[3]) < 0.06 * n_ant


# ---------------------------------------------------------------------------
# _flag_score tests
# ---------------------------------------------------------------------------


class TestFlagScore:
    def _make_summary(self, ant_data: dict[str, tuple[float, float]]) -> dict:
        """
        Build a flagdata summary dict from {name: (flagged, total)}.
        """
        return {"antenna": {name: {"flagged": f, "total": t} for name, (f, t) in ant_data.items()}}

    def test_unflagged_data_scores_highest(self):
        """Antenna with most unflagged data should score highest."""
        ant_names = ["ea01", "ea02", "ea03"]
        summary = self._make_summary(
            {
                "ea01": (0, 1000),  # 0% flagged → good = 1000
                "ea02": (500, 1000),  # 50% flagged → good = 500
                "ea03": (900, 1000),  # 90% flagged → good = 100
            }
        )
        scores = _flag_score(ant_names, summary)

        assert scores[0] > scores[1] > scores[2]

    def test_fully_flagged_scores_zero(self):
        """Antenna with all data flagged should score 0."""
        ant_names = ["ea01", "ea02"]
        summary = self._make_summary(
            {
                "ea01": (1000, 1000),  # 100% flagged
                "ea02": (0, 1000),  # 0% flagged
            }
        )
        scores = _flag_score(ant_names, summary)

        assert scores[0] == pytest.approx(0.0)
        assert scores[1] == pytest.approx(float(len(ant_names)))

    def test_missing_antenna_in_summary_scores_zero(self):
        """An antenna absent from the flagdata summary should score 0."""
        ant_names = ["ea01", "ea02", "ea03"]
        summary = self._make_summary(
            {
                "ea01": (0, 1000),
                # ea02 and ea03 missing
            }
        )
        scores = _flag_score(ant_names, summary)

        assert scores[1] == pytest.approx(0.0)
        assert scores[2] == pytest.approx(0.0)
        assert scores[0] > 0.0

    def test_empty_summary_returns_zeros(self):
        """An empty flagdata summary should return all zeros."""
        ant_names = ["ea01", "ea02"]
        scores = _flag_score(ant_names, {})
        assert np.all(scores == 0.0)


# ---------------------------------------------------------------------------
# Combined ranking tests
# ---------------------------------------------------------------------------


class TestCombinedRanking:
    """Verify that combined score = geo + flag ranks correctly."""

    def test_agreement_ranks_correctly(self):
        """
        When geometry and flagging agree on the best antenna, it should
        rank first in the combined score.
        """
        # Positions: X = [1000, 500, 0] → median = 500 → ant1 (X=500) is centre
        # Antenna 0: worst geo (far), heavily flagged
        # Antenna 1: best geo (centre), minimal flags  ← should win
        # Antenna 2: mid geo, mid flags
        positions = np.array(
            [
                [1000.0, 500.0, 0.0],
                [0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0],
            ]
        )
        geo = _geo_score(positions, [False, False, False])

        ant_names = ["ea01", "ea02", "ea03"]
        summary = {
            "antenna": {
                "ea01": {"flagged": 900, "total": 1000},
                "ea02": {"flagged": 10, "total": 1000},
                "ea03": {"flagged": 500, "total": 1000},
            }
        }
        flag = _flag_score(ant_names, summary)
        combined = geo + flag

        best_idx = int(np.argmax(combined))
        assert ant_names[best_idx] == "ea02"

    def test_worst_spw_hidden_by_aggregate_score(self):
        """
        Reproduces the per-SpW blindness failure mode: an antenna fully
        flagged in one SpW out of several, clean elsewhere, scores near the
        top of the aggregate flag_score, but _worst_spw_per_antenna must
        still surface the bad SpW.
        """
        ant_names = ["ea01", "ea02"]
        # Aggregate: ea01 flagged in 1 of 4 equal-sized SpWs -> 25% overall,
        # still scores well above an antenna that's uniformly flagged.
        agg_summary = {
            "antenna": {
                "ea01": {"flagged": 250, "total": 1000},
                "ea02": {"flagged": 0, "total": 1000},
            }
        }
        agg_scores = _flag_score(ant_names, agg_summary)
        assert agg_scores[0] > 0.7 * len(ant_names)  # looks fine in aggregate

        spw_summaries = {
            "0": {
                "antenna": {
                    "ea01": {"flagged": 250, "total": 250},
                    "ea02": {"flagged": 0, "total": 250},
                }
            },
            "1": {
                "antenna": {
                    "ea01": {"flagged": 0, "total": 250},
                    "ea02": {"flagged": 0, "total": 250},
                }
            },
            "2": {
                "antenna": {
                    "ea01": {"flagged": 0, "total": 250},
                    "ea02": {"flagged": 0, "total": 250},
                }
            },
            "3": {
                "antenna": {
                    "ea01": {"flagged": 0, "total": 250},
                    "ea02": {"flagged": 0, "total": 250},
                }
            },
        }
        worst = _worst_spw_per_antenna(ant_names, spw_summaries)

        # ea01 is dead in SpW 0 and clean in the other three. The worst value
        # alone is 1.0, but what makes it disqualifying is that the median is
        # 0.0, so the excess is the full 1.0.
        assert worst["ea01"]["worst_spw_flag_frac"] == 1.0
        assert worst["ea01"]["worst_spw_id"] == "0"
        assert worst["ea01"]["median_spw_flag_frac"] == 0.0
        assert worst["ea01"]["worst_spw_excess"] == 1.0
        assert worst["ea01"]["n_spw_measured"] == 4

        assert worst["ea02"]["worst_spw_flag_frac"] == 0.0
        assert worst["ea02"]["worst_spw_excess"] == 0.0

    def test_uniform_flagging_yields_zero_excess(self):
        """
        The compact-configuration case. Shadowing in VLA C/D config flags an
        antenna across every SpW at once, and it hits the central antennas the
        geometry score ranks highest. Such an antenna must NOT look
        SpW-pathological: the worst fraction is high but the excess is ~0, so a
        skill thresholding on excess does not reject the best D-config refant
        candidates.
        """
        ant_names = ["ea01"]
        # 40% flagged in every SpW — heavy, but perfectly uniform.
        spw_summaries = {
            str(i): {"antenna": {"ea01": {"flagged": 400, "total": 1000}}} for i in range(4)
        }
        worst = _worst_spw_per_antenna(ant_names, spw_summaries)

        assert worst["ea01"]["worst_spw_flag_frac"] == pytest.approx(0.4)
        assert worst["ea01"]["median_spw_flag_frac"] == pytest.approx(0.4)
        assert worst["ea01"]["worst_spw_excess"] == pytest.approx(0.0)

    def test_no_usable_data_returns_none(self):
        worst = _worst_spw_per_antenna(["ea01"], {"0": {"antenna": {}}})
        assert worst["ea01"]["worst_spw_flag_frac"] is None
        assert worst["ea01"]["worst_spw_id"] is None
        assert worst["ea01"]["median_spw_flag_frac"] is None
        assert worst["ea01"]["worst_spw_excess"] is None
        assert worst["ea01"]["n_spw_measured"] == 0

    def test_disagreement_flagging_overrides_geometry(self):
        """
        Central antenna (best geo) is >90% flagged.
        A more distant antenna with clean data should win.
        """
        # Antenna 0: at centre (geo=n=3), but 95% flagged → flag=0.1*3=0.3
        # Antenna 1: far from centre (geo≈0), but 0% flagged → flag=3
        # Combined: ant1 wins
        positions = np.array(
            [
                [0.0, 1000.0],
                [0.0, 0.0],
                [0.0, 0.0],
            ]
        )
        geo = _geo_score(positions, [False, False])

        ant_names = ["ea01", "ea02"]
        summary = {
            "antenna": {
                "ea01": {"flagged": 950, "total": 1000},  # 95% flagged
                "ea02": {"flagged": 0, "total": 1000},  # 0% flagged
            }
        }
        flag = _flag_score(ant_names, summary)
        combined = geo + flag

        best_idx = int(np.argmax(combined))
        assert ant_names[best_idx] == "ea02"
