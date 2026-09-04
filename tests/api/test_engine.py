"""Tests for pipeline/engine.py (MISSION.md §12, G1) — the API `app/` will consume."""
import json

import pytest

from harness import generators
from pipeline import engine


@pytest.fixture(scope="module")
def m1_stl(tmp_path_factory):
    truth = generators.make("M1")
    return str(truth.stl_path)


def test_analyze_reports_sane_frame_and_extent(m1_stl):
    a = engine.analyze(m1_stl, axis="z", units="mm")
    assert isinstance(a, engine.Analysis)
    assert a.body_count == 1
    assert a.is_watertight is True
    assert a.triangle_count > 0
    assert a.median_edge_length_mm > 0
    assert a.axial_extent_mm > 0
    assert a.units == "mm"
    assert 0.0 <= a.axis_confidence <= 1.0
    assert len(a.frame_axis) == 3
    assert len(a.origin_xy_mm) == 2


def test_analyze_does_not_write_any_output(m1_stl, tmp_path):
    before = sorted(tmp_path.iterdir())
    engine.analyze(m1_stl, axis="z", units="mm")
    after = sorted(tmp_path.iterdir())
    assert before == after


def test_rebuild_success_returns_result_with_report(m1_stl, tmp_path):
    out_step = tmp_path / "out.step"
    out_report = tmp_path / "out.report.json"
    opts = engine.RebuildOptions(
        input_stl=m1_stl, output=str(out_step), axis="z", units="mm",
        sections=10, chord_tol=0.5, report=str(out_report),
    )
    result = engine.rebuild(opts)
    assert isinstance(result, engine.Result)
    assert out_step.exists()
    assert result.output_path == str(out_step)
    assert result.report is not None
    assert result.report["n_stations"] == 10
    # cross-check against what report.write actually wrote to disk
    with open(out_report) as f:
        on_disk = json.load(f)
    assert on_disk["n_stations"] == 10


def test_rebuild_reports_progress_per_station(m1_stl, tmp_path):
    stages = []
    opts = engine.RebuildOptions(
        input_stl=m1_stl, output=str(tmp_path / "out.step"), axis="z", units="mm",
        sections=6, chord_tol=0.5,
    )
    engine.rebuild(opts, on_progress=lambda stage, frac, msg: stages.append((stage, frac)))
    assert ("load", 0.0) in stages
    assert ("done", 1.0) in stages
    station_fracs = [frac for stage, frac in stages if stage == "stations"]
    assert len(station_fracs) == 6
    assert station_fracs == sorted(station_fracs)


def test_rebuild_cancel_raises_and_stops_early(m1_stl, tmp_path):
    calls = []

    def cancel_after_two():
        calls.append(1)
        return len(calls) > 2

    opts = engine.RebuildOptions(
        input_stl=m1_stl, output=str(tmp_path / "out.step"), axis="z", units="mm",
        sections=20, chord_tol=0.5,
    )
    with pytest.raises(engine.RebuildCancelled):
        engine.rebuild(opts, cancel=cancel_after_two)
    assert not (tmp_path / "out.step").exists()


def test_rebuild_non_watertight_raises_input_error(tmp_path):
    import trimesh
    bad = trimesh.creation.box(extents=(10, 10, 10))
    # Remove a face to break watertightness.
    bad.faces = bad.faces[:-1]
    bad_stl = tmp_path / "bad.stl"
    bad.export(bad_stl)

    opts = engine.RebuildOptions(input_stl=str(bad_stl), output=str(tmp_path / "out.step"))
    with pytest.raises(engine.InputError) as excinfo:
        engine.rebuild(opts)
    assert excinfo.value.exit_code == 3
    assert "watertight" in str(excinfo.value)


def test_rebuild_bad_input_path_raises_usage_or_crash_error(tmp_path):
    opts = engine.RebuildOptions(
        input_stl=str(tmp_path / "does_not_exist.stl"), output=str(tmp_path / "out.step"))
    with pytest.raises(engine.UsageOrCrashError) as excinfo:
        engine.rebuild(opts)
    assert excinfo.value.exit_code == 2


def _non_watertight_stl(tmp_path):
    import trimesh
    bad = trimesh.creation.box(extents=(10, 10, 10))
    bad.faces = bad.faces[:-1]  # remove a face to break watertightness
    bad_stl = tmp_path / "bad.stl"
    bad.export(bad_stl)
    return str(bad_stl)


def test_rebuild_clean_failure_still_writes_report(tmp_path):
    """MISSION §5.2 step 8 / §7.2: the report JSON must exist on EVERY exit path, including a
    clean typed failure — with status, exit_code and the printed failure reason as `error`."""
    report_path = tmp_path / "out.report.json"
    opts = engine.RebuildOptions(
        input_stl=_non_watertight_stl(tmp_path), output=str(tmp_path / "out.step"),
        report=str(report_path))
    with pytest.raises(engine.InputError):
        engine.rebuild(opts)
    assert report_path.exists()
    rep = json.loads(report_path.read_text())
    assert rep["status"] == "failed"
    assert rep["exit_code"] == 3
    assert "watertight" in rep["error"]
    assert rep["stations_z_mm"] == []
    assert rep["n_stations"] == 0


def test_rebuild_unexpected_exception_still_writes_report(m1_stl, tmp_path, monkeypatch):
    """A raw exception mid-pipeline (not a clean typed failure) must still leave a report
    behind, carrying whatever partial stations were sliced before the crash."""
    report_path = tmp_path / "out.report.json"

    def boom(*a, **k):
        raise RuntimeError("forced test failure")

    monkeypatch.setattr(engine.export, "write_step", boom)
    opts = engine.RebuildOptions(
        input_stl=m1_stl, output=str(tmp_path / "out.step"), axis="z", units="mm",
        sections=10, chord_tol=0.5, report=str(report_path))
    with pytest.raises(engine.UsageOrCrashError):
        engine.rebuild(opts)
    assert report_path.exists()
    rep = json.loads(report_path.read_text())
    assert rep["status"] == "failed"
    assert rep["exit_code"] == 2
    assert "RuntimeError" in rep["error"] and "forced test failure" in rep["error"]
    assert rep["n_stations"] == 10 and len(rep["stations_z_mm"]) == 10
    assert rep["stage_reached"] == "export"


def test_cli_failure_writes_report(tmp_path):
    """The CLI path shares the same report-on-failure wrapper as engine.rebuild()."""
    from pipeline import cli
    report_path = tmp_path / "out.report.json"
    code = cli.main([_non_watertight_stl(tmp_path), "-o", str(tmp_path / "out.step"),
                     "--report", str(report_path)])
    assert code == 3
    rep = json.loads(report_path.read_text())
    assert rep["status"] == "failed"
    assert rep["exit_code"] == 3


def test_analyze_suggested_chord_tol_is_sag_based():
    """The auto chord-tol must track the mesh's actual chordal sag, not its edge length: M8 is
    a clean CAD tessellation (median edge ~27 mm, true generation chord-tol 0.5 mm), and the
    old median-edge suggestion of ~27 put the boolean fuzzy value at real-feature scale (the
    adaptive + coarse-ct crash regime)."""
    a = engine.analyze("harness/truth/M8.stl", axis="z", units="mm")
    assert 0.1 <= a.suggested_chord_tol_mm <= 1.0
    assert a.median_edge_length_mm > 20.0  # the raw median-edge metric itself is unchanged
