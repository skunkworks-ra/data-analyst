"""
Unit tests for ms_refant scoring helpers.

No CASA required. Tests cover _geo_score and _flag_score with synthetic inputs.
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
        positions = np.array([
            [-100.0, 0.0, 100.0],  # X
            [0.0,    0.0,   0.0],  # Y
            [0.0,    0.0,   0.0],  # Z
        ])
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
        positions = np.array([
            [-1.0, -1.0, 1.0, 1.0],   # X
            [-1.0,  1.0,-1.0, 1.0],   # Y
            [ 0.0,  0.0, 0.0, 0.0],   # Z
        ])
        flags = [False, False, False, False]
        scores = _geo_score(positions, flags)

        assert scores[0] == pytest.approx(scores[1], abs=1e-6)
        assert scores[1] == pytest.approx(scores[2], abs=1e-6)
        assert scores[2] == pytest.approx(scores[3], abs=1e-6)

    def test_flagged_antenna_scores_zero(self):
        """Antennas with FLAG_ROW=True should score 0."""
        positions = np.array([
            [-500.0, 0.0, 500.0],
            [0.0,    0.0,   0.0],
            [0.0,    0.0,   0.0],
        ])
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
        positions = np.array([
            [0.0, 0.0, 1000.0],
            [0.0, 0.0,    0.0],
            [0.0, 0.0,    0.0],
        ])
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
        return {
            "antenna": {
                name: {"flagged": f, "total": t}
                for name, (f, t) in ant_data.items()
            }
        }

    def test_unflagged_data_scores_highest(self):
        """Antenna with most unflagged data should score highest."""
        ant_names = ["ea01", "ea02", "ea03"]
        summary = self._make_summary({
            "ea01": (0, 1000),    # 0% flagged → good = 1000
            "ea02": (500, 1000),  # 50% flagged → good = 500
            "ea03": (900, 1000),  # 90% flagged → good = 100
        })
        scores = _flag_score(ant_names, summary)

        assert scores[0] > scores[1] > scores[2]

    def test_fully_flagged_scores_zero(self):
        """Antenna with all data flagged should score 0."""
        ant_names = ["ea01", "ea02"]
        summary = self._make_summary({
            "ea01": (1000, 1000),  # 100% flagged
            "ea02": (0, 1000),     # 0% flagged
        })
        scores = _flag_score(ant_names, summary)

        assert scores[0] == pytest.approx(0.0)
        assert scores[1] == pytest.approx(float(len(ant_names)))

    def test_missing_antenna_in_summary_scores_zero(self):
        """An antenna absent from the flagdata summary should score 0."""
        ant_names = ["ea01", "ea02", "ea03"]
        summary = self._make_summary({
            "ea01": (0, 1000),
            # ea02 and ea03 missing
        })
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
        positions = np.array([
            [1000.0, 500.0, 0.0],
            [0.0,    0.0,   0.0],
            [0.0,    0.0,   0.0],
        ])
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
        positions = np.array([
            [0.0, 1000.0],
            [0.0,    0.0],
            [0.0,    0.0],
        ])
        geo = _geo_score(positions, [False, False])

        ant_names = ["ea01", "ea02"]
        summary = {
            "antenna": {
                "ea01": {"flagged": 950, "total": 1000},   # 95% flagged
                "ea02": {"flagged": 0, "total": 1000},      # 0% flagged
            }
        }
        flag = _flag_score(ant_names, summary)
        combined = geo + flag

        best_idx = int(np.argmax(combined))
        assert ant_names[best_idx] == "ea02"
