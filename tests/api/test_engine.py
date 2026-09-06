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
    # "build" covers the previously-silent post-sectioning tail (chain matching, boolean cuts,
    # export, verification) -- must fire and stay monotonic within itself, same contract as
    # "stations" above.
    build_fracs = [frac for stage, frac in stages if stage == "build"]
    assert len(build_fracs) >= 3
    assert build_fracs == sorted(build_fracs)


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


def test_invalid_final_solid_always_has_an_actionable_hint(tmp_path):
    """Regression test for a real incident (2026-09-06): M8 at --sections 40 --adaptive with
    --chord-tol 0.5 (the GUI's raw spinbox default, used because Analyze was never run so the
    ~0.94mm mesh-appropriate value was never computed) failed BRepCheck_Analyzer's final
    validity check with NO hint at all -- a bare "failed validity check" message. Brady hit this
    same bare-message gap at least three times. Every exit path from that failure must now
    leave an actionable next step, not just report that it failed."""
    opts = engine.RebuildOptions(
        input_stl="harness/truth/M8.stl", output=str(tmp_path / "out.step"),
        axis="auto", units="mm", sections=40, adaptive=True, chord_tol=0.5,
        refine_passes=0,  # isolate the failure -- a retry pass would just repeat it
    )
    with pytest.raises(engine.GeometryError) as excinfo:
        engine.rebuild(opts)
    message = str(excinfo.value)
    assert "final solid failed BRepCheck_Analyzer validity check" in message
    # The specific, correct diagnosis for THIS incident: 0.5mm is far finer than the mesh's own
    # ~0.94mm chordal deviation. Any of these three phrasings would count as "actionable" but
    # this one is the mechanically correct one for this exact input -- assert on it precisely
    # so a regression that falls through to the generic branch instead is still caught.
    assert "is much finer than the mesh's estimated chordal deviation" in message
    assert "retry with --chord-tol" in message


def test_validity_hint_does_not_suggest_enabling_adaptive_when_already_on(tmp_path):
    """Regression test for Brady's 2026-09-06 report: the generic branch of the same validity
    hint used to say "try --adaptive" unconditionally, even on a run that already had
    --adaptive on -- confusing, and also not evidence-backed: --adaptive is what crashes this
    exact way on M8 at n=52/60 while uniform placement passes at the same n, so recommending
    turning it OFF is the correct, context-aware advice here."""
    opts = engine.RebuildOptions(
        input_stl="harness/truth/M8.stl", output=str(tmp_path / "out.step"),
        axis="z", units="mm", sections=52, adaptive=True, chord_tol=0.9436368581581187,
        refine_passes=0,
    )
    with pytest.raises(engine.GeometryError) as excinfo:
        engine.rebuild(opts)
    message = str(excinfo.value)
    assert "try turning off --adaptive" in message
    assert "try --adaptive to concentrate" not in message


def test_volume_hint_does_not_suggest_adaptive_when_already_on(tmp_path):
    opts = engine.RebuildOptions(
        input_stl="harness/truth/M8.stl", output=str(tmp_path / "out.step"),
        axis="z", units="mm", sections=40, adaptive=True, chord_tol=0.9436368581581187,
        refine_passes=0, report=str(tmp_path / "out.report.json"),
    )
    result = engine.rebuild(opts)
    hint = result.report["verification"]["volume"].get("hint", "")
    assert "adaptive" not in hint.lower()
    assert "more --sections" in hint


def test_non_axisymmetric_hint_gives_a_specific_chord_tol_when_roundness_is_the_cause():
    """Regression test for a real incident (2026-09-06): a real STL's outer loop measured
    max_resid=0.1240 against --chord-tol 0.082's gate of 0.123 -- under 1% over, ordinary mesh
    tessellation noise tripping an auto-computed chord-tol's tight gate, NOT a genuinely
    non-round part (the fitted center was 0.003mm off-axis, negligible). Brady's ask: "we need
    a user message saying to bump up the chord tolerance a bit, be specific on a percentage or
    something" -- this must give a concrete number, not a vague nudge, and must not blame
    --axis when the center is actually fine."""
    msg = engine._non_axisymmetric_hint(
        zz=109.609, cx=0.0011, cy=-0.0027, max_resid=0.1240, chord_tol=0.082)
    assert "retry with --chord-tol" in msg
    assert "check --axis" not in msg
    # The suggested value must actually clear the gate it failed, with real margin -- not just
    # barely enough to pass by the same hair it originally missed by.
    suggested = float(msg.rsplit("--chord-tol ", 1)[1].split(" ")[0])
    assert engine.tol.circle_max_resid(suggested) > 0.1240 * 1.1


def test_non_axisymmetric_hint_blames_axis_when_center_is_genuinely_off(tmp_path):
    """The OTHER branch of the same OR'd check: a center that's actually far from the axis
    (not a roundness-noise false positive) should point at --axis/--units, not chord-tol."""
    msg = engine._non_axisymmetric_hint(
        zz=50.0, cx=5.0, cy=3.0, max_resid=0.05, chord_tol=0.1)
    assert "check --axis" in msg
    assert "retry with --chord-tol" not in msg
