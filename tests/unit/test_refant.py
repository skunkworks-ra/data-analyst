"""
Unit tests for ms_refant scoring helpers.

No CASA required. Tests cover _geo_score, _geo_distances and _flag_score with
synthetic hand-built inputs. Nothing here reads a real ANTENNA subtable.
"""

from __future__ import annotations

import numpy as np
import pytest

from ms_inspect.tools.refant import _flag_score, _geo_score

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


# ---------------------------------------------------------------------------
# _geo_distances — the inputs behind geo_score
# ---------------------------------------------------------------------------


class TestGeoDistances:
    """Covers the saturation _geo_score hides, with a synthetic extended array.

    These are pure-numpy tests on hand-built ECEF positions. They do NOT test
    that the values are read correctly from a real ANTENNA subtable, nor that
    the ranking is scientifically right for any real configuration.
    """

    @staticmethod
    def _extended_config():
        """Ten antennas in a 1 km core plus one outlier at 30 km.

        This is the shape of a VLA A-configuration relative to its core: the
        normalising antenna is tens of times further out than the spread among
        the candidates you actually want to rank.
        """
        core_x = np.linspace(-500.0, 500.0, 10)
        x = np.concatenate([core_x, [30000.0]])
        zeros = np.zeros_like(x)
        return np.array([x, zeros, zeros])

    def test_geo_score_saturates_across_the_core(self):
        positions = self._extended_config()
        flags = [False] * positions.shape[1]

        scores = _geo_score(positions, flags)
        n_ant = positions.shape[1]

        core_scores = scores[:10] / n_ant  # normalise out the n_ant factor
        # Every core antenna lands above 0.94 of the maximum possible score:
        # geo_score cannot separate them.
        assert core_scores.min() > 0.94
        assert core_scores.max() - core_scores.min() < 0.06

    def test_distance_still_separates_what_geo_score_cannot(self):
        from ms_inspect.tools.refant import _geo_distances

        positions = self._extended_config()
        flags = [False] * positions.shape[1]

        scores = _geo_score(positions, flags)
        distances, max_dist = _geo_distances(positions, flags)

        # The two extreme core antennas are ~1 km apart in distance-from-centre
        # terms while their geo_scores differ by a few percent of full scale.
        spread_m = distances[:10].max() - distances[:10].min()
        assert spread_m > 400.0

        n_ant = positions.shape[1]
        score_spread_frac = (scores[:10].max() - scores[:10].min()) / n_ant
        assert score_spread_frac < 0.06

        # And max_distance_m makes the cause of the saturation visible.
        assert max_dist == pytest.approx(distances[10])
        assert max_dist > 25000.0

    def test_all_flagged_returns_zeros(self):
        from ms_inspect.tools.refant import _geo_distances

        positions = self._extended_config()
        distances, max_dist = _geo_distances(positions, [True] * positions.shape[1])

        assert max_dist == 0.0
        assert np.all(distances == 0.0)

    def test_distances_are_reported_for_flagged_antennas_too(self):
        from ms_inspect.tools.refant import _geo_distances

        positions = self._extended_config()
        flags = [False] * 10 + [True]  # the far outlier is flagged
        distances, max_dist = _geo_distances(positions, flags)

        # max_dist normalises over unflagged antennas only ...
        assert max_dist < 1000.0
        # ... but the flagged antenna's own distance is still measured.
        assert distances[10] > 25000.0
