"""Tests for pipeline/engine.py's verify-and-refine retry loop (2026-09-05):
`_rebuild_with_refinement`, `_refine_anchor_from_outcome`, and the report/progress plumbing
around them. Drives the orchestration logic by monkeypatching `_compute_verification` to return
scripted deviation results (so these tests exercise "does the retry loop behave correctly", not
"does redistribution actually improve this specific mesh" -- that was verified empirically on
M8/M12 this session and is recorded in PROGRESS.md, not re-tested here)."""
import json

import pytest

from harness import generators
from pipeline import engine


@pytest.fixture(scope="module")
def m1_stl():
    truth = generators.make("M1")
    return str(truth.stl_path)


@pytest.fixture(scope="module")
def m11_stl():
    truth = generators.make("M11")
    return str(truth.stl_path)


def _step_volume(path: str) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    shape = engine._read_step_shape(path)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def _scripted_verification(sequence):
    """Returns a `_compute_verification`-shaped callable that ignores its real arguments and
    returns the next scripted deviation dict from `sequence` each time it's called (repeating
    the last entry once exhausted)."""
    calls = {"n": 0}

    def fake(mesh, R_axis, shape, chord_tol, axial_extent_mm, expected_bodies,
             preview_stl=None, stations_z_mm=None, topology_events_z_mm=None,
             axial_origin_z=0.0, adaptive=None, sections=None):
        i = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        dev = sequence[i]
        return {"deviation": dev, "volume": None, "bounds": None, "bodies": None}

    fake.calls = calls
    return fake


def test_refine_passes_zero_is_a_complete_noop(m1_stl, tmp_path, monkeypatch):
    calls = {"n": 0}
    orig = engine._rebuild_impl

    def counting(args):
        calls["n"] += 1
        return orig(args)

    monkeypatch.setattr(engine, "_rebuild_impl", counting)
    opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=10, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=0)
    result = engine.rebuild(opts)
    assert calls["n"] == 1
    assert "refinement" not in (result.report or {})


def test_pass_1_already_passing_verification_is_a_noop(m1_stl, tmp_path, monkeypatch):
    """M1's real verification passes -- confirms refine_passes=1 doesn't force a retry when
    none is warranted, using the REAL (unmocked) verification computation."""
    calls = {"n": 0}
    orig = engine._rebuild_impl

    def counting(args):
        calls["n"] += 1
        return orig(args)

    monkeypatch.setattr(engine, "_rebuild_impl", counting)
    opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=10, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=1)
    result = engine.rebuild(opts)
    assert calls["n"] == 1
    assert "refinement" not in (result.report or {})
    assert result.report["verification"]["deviation"]["pass"] is True


def test_fail_then_pass_keeps_pass_2(m1_stl, tmp_path, monkeypatch):
    fake = _scripted_verification([
        {"pass": False, "approx_p95_mm": 5.0, "worst_z_mm": 3000.0, "tol_mm": 1.0},
        {"pass": True, "approx_p95_mm": 0.2, "worst_z_mm": 3000.0, "tol_mm": 1.0},
    ])
    monkeypatch.setattr(engine, "_compute_verification", fake)
    opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=10, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=1)
    result = engine.rebuild(opts)
    assert fake.calls["n"] == 2
    ref = result.report["refinement"]
    assert ref["passes_run"] == 2
    assert ref["kept_pass"] == 2
    assert ref["improved"] is True
    assert result.report["verification"]["deviation"]["pass"] is True


def test_fail_then_worse_restores_pass_1(m1_stl, tmp_path, monkeypatch):
    # Baseline: what pass 1 alone (refine_passes=0) produces -- compared by re-tessellated
    # volume rather than raw bytes, since OCCT's STEP writer embeds a write timestamp that
    # differs between any two separate write calls even for byte-identical geometry.
    monkeypatch.setattr(engine, "_compute_verification",
                        _scripted_verification([{"pass": False, "approx_p95_mm": 5.0,
                                                 "worst_z_mm": 3000.0, "tol_mm": 1.0}]))
    baseline_opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "baseline.step"),
                                          axis="z", units="mm", sections=10, chord_tol=0.5,
                                          report=str(tmp_path / "baseline.report.json"),
                                          refine_passes=0)
    engine.rebuild(baseline_opts)
    baseline_volume = _step_volume(str(tmp_path / "baseline.step"))

    fake = _scripted_verification([
        {"pass": False, "approx_p95_mm": 5.0, "worst_z_mm": 3000.0, "tol_mm": 1.0},
        {"pass": False, "approx_p95_mm": 8.0, "worst_z_mm": 3000.0, "tol_mm": 1.0},
    ])
    monkeypatch.setattr(engine, "_compute_verification", fake)
    opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=10, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=1)
    result = engine.rebuild(opts)
    assert fake.calls["n"] == 2
    ref = result.report["refinement"]
    assert ref["kept_pass"] == 1
    assert ref["improved"] is False
    assert result.report["verification"]["deviation"]["approx_p95_mm"] == 5.0
    assert _step_volume(str(tmp_path / "out.step")) == pytest.approx(baseline_volume, rel=1e-9)


def test_pass_2_exception_restores_pass_1(m1_stl, tmp_path, monkeypatch):
    fake = _scripted_verification([
        {"pass": False, "approx_p95_mm": 5.0, "worst_z_mm": 3000.0, "tol_mm": 1.0},
    ])
    monkeypatch.setattr(engine, "_compute_verification", fake)

    orig_argparse = engine._rebuild_argparse
    call_count = {"n": 0}

    def flaky(args):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("forced pass-2 failure")
        return orig_argparse(args)

    monkeypatch.setattr(engine, "_rebuild_argparse", flaky)
    opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=10, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=1)
    result = engine.rebuild(opts)  # must not raise -- pass 1 succeeded, exit code 0
    ref = result.report["refinement"]
    assert ref["kept_pass"] == 1
    assert "RuntimeError" in ref["attempts"][1]["error"]
    assert os_path_exists(tmp_path / "out.step")


def os_path_exists(p):
    import os
    return os.path.exists(str(p))


def test_never_converges_reports_honestly(m1_stl, tmp_path, monkeypatch):
    fake = _scripted_verification([
        {"pass": False, "approx_p95_mm": 5.0, "worst_z_mm": 3000.0, "tol_mm": 1.0},
        {"pass": False, "approx_p95_mm": 5.0, "worst_z_mm": 3000.0, "tol_mm": 1.0},
    ])
    monkeypatch.setattr(engine, "_compute_verification", fake)
    opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=10, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=1)
    result = engine.rebuild(opts)  # exit code 0 -- a failed verification is informational only
    ref = result.report["refinement"]
    assert ref["passes_run"] == 2
    assert ref["improved"] is False
    assert result.report["verification"]["deviation"]["pass"] is False


def test_retry_skipped_when_worst_point_already_has_a_station(m1_stl, tmp_path, monkeypatch):
    # Learn a REAL station's exact report-frame z from an unmocked pass, so the mocked
    # "worst_z_mm" below coincides EXACTLY with an existing station -- the most direct possible
    # proof that "already covered" is correctly detected, with no dependence on guessing where
    # uniform_stations happens to cluster.
    probe_opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "probe.step"),
                                       axis="z", units="mm", sections=10, chord_tol=0.5,
                                       report=str(tmp_path / "probe.report.json"))
    probe_result = engine.rebuild(probe_opts)
    real_station_z = probe_result.report["stations_z_mm"][0]

    fake = _scripted_verification([
        {"pass": False, "approx_p95_mm": 5.0, "worst_z_mm": real_station_z, "tol_mm": 1.0},
    ])
    monkeypatch.setattr(engine, "_compute_verification", fake)
    opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=10, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=1)
    result = engine.rebuild(opts)
    assert fake.calls["n"] == 1  # no second pass attempted
    ref = result.report["refinement"]
    assert ref["passes_run"] == 1
    assert "skipped" in ref


def test_multi_body_never_retries(m11_stl, tmp_path, monkeypatch):
    fake = _scripted_verification([
        {"pass": False, "approx_p95_mm": 5.0, "worst_z_mm": 100.0, "tol_mm": 1.0},
    ])
    monkeypatch.setattr(engine, "_compute_verification", fake)
    opts = engine.RebuildOptions(input_stl=m11_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=10, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=1)
    result = engine.rebuild(opts)
    assert "refinement" not in (result.report or {})


def test_cancel_before_refinement_pass_restores_pass_1(m1_stl, tmp_path, monkeypatch):
    fake = _scripted_verification([
        {"pass": False, "approx_p95_mm": 5.0, "worst_z_mm": 3000.0, "tol_mm": 1.0},
    ])
    monkeypatch.setattr(engine, "_compute_verification", fake)
    calls = {"n": 0}
    sections = 10

    def cancel_after_pass_1():
        # `cancel()` is polled once per station inside `_rebuild_impl`'s loop, so pass 1 alone
        # makes `sections` calls before it ever gets a chance to return True here -- only start
        # cancelling once pass 1 has clearly finished and `_rebuild_with_refinement`'s own
        # pre-retry check is what will see the first True.
        calls["n"] += 1
        return calls["n"] > sections

    opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=sections, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=1)
    with pytest.raises(engine.RebuildCancelled):
        engine.rebuild(opts, cancel=cancel_after_pass_1)
    assert os_path_exists(tmp_path / "out.step")  # pass 1's valid result stays on disk
    with open(tmp_path / "out.report.json") as f:
        rep = json.load(f)
    assert "refinement" not in rep  # never annotated -- pass 2 never completed


def test_progress_monotonic_across_forced_retry(m1_stl, tmp_path, monkeypatch):
    fake = _scripted_verification([
        {"pass": False, "approx_p95_mm": 5.0, "worst_z_mm": 3000.0, "tol_mm": 1.0},
        {"pass": True, "approx_p95_mm": 0.2, "worst_z_mm": 3000.0, "tol_mm": 1.0},
    ])
    monkeypatch.setattr(engine, "_compute_verification", fake)
    stages = []
    opts = engine.RebuildOptions(input_stl=m1_stl, output=str(tmp_path / "out.step"),
                                 axis="z", units="mm", sections=10, chord_tol=0.5,
                                 report=str(tmp_path / "out.report.json"), refine_passes=1)
    engine.rebuild(opts, on_progress=lambda stage, frac, msg: stages.append((stage, frac)))
    fracs = [f for _, f in stages]
    assert fracs == sorted(fracs)
    done_events = [s for s in stages if s[0] == "done"]
    assert done_events == [("done", 1.0)]
    assert any(s == "refine" for s, _ in stages)


def test_cli_refine_passes_flag_parses(m1_stl, tmp_path):
    from pipeline import cli
    args = cli._parse_args([m1_stl, "-o", str(tmp_path / "out.step"), "--refine-passes", "0"])
    assert args.refine_passes == 0
    code = cli.main([m1_stl, "-o", str(tmp_path / "out2.step"), "--refine-passes", "0"])
    assert code == 0
