"""
Unit tests for ms_tclean script generation.

No CASA required. open_table fails on the fake MS, which downgrades the
CORRECTED_DATA check to a warning — script generation still proceeds.
"""

from __future__ import annotations

from pathlib import Path


def _make_ms(tmp_path) -> Path:
    ms = tmp_path / "test.ms"
    ms.mkdir()
    (ms / "table.info").write_text("Type = Measurement Set\n")
    return ms


def _run(tmp_path, **kwargs):
    from ms_modify.tclean import run

    ms = _make_ms(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = run(
        str(ms),
        str(workdir / "img"),
        field="0",
        workdir=str(workdir),
        execute=False,
        **kwargs,
    )
    script = Path(result["data"]["script_path"]["value"]).read_text()
    warnings = result.get("warnings", [])
    return script, warnings


class TestCfcache:
    def test_cfcache_rendered_for_awproject(self, tmp_path):
        script, _ = _run(tmp_path, gridder="awproject", wprojplanes=32, cfcache="/data/cf.cache")
        assert "cfcache      = '/data/cf.cache'" in script

    def test_cfcache_ignored_for_awp2_with_warning(self, tmp_path):
        script, warnings = _run(tmp_path, gridder="awp2", wprojplanes=32, cfcache="/data/cf.cache")
        assert "cfcache      =" not in script
        assert any("only used by gridder='awproject'" in w for w in warnings)

    def test_awproject_without_cfcache_warns(self, tmp_path):
        _, warnings = _run(tmp_path, gridder="awproject", wprojplanes=32)
        assert any("without cfcache" in w for w in warnings)


class TestWprojplanesWarning:
    def test_awp2_without_wprojplanes_warns(self, tmp_path):
        _, warnings = _run(tmp_path, gridder="awp2")
        assert any("wprojplanes=1" in w for w in warnings)

    def test_standard_gridder_no_warning(self, tmp_path):
        _, warnings = _run(tmp_path, gridder="standard")
        assert not any("wprojplanes" in w for w in warnings)


class TestSpecmodeMvc:
    def test_mvc_rendered(self, tmp_path):
        script, _ = _run(tmp_path, gridder="awp2", wprojplanes=32, specmode="mvc")
        assert "specmode     = 'mvc'" in script
