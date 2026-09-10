"""Tests for Phase A of `docs/plans/tapered_bore_dome_pinch_and_surface_area.md`:
`pipeline.engine._measure_end_radius` (§2.1), the asymmetric pinch-override gate (§2.2), and
the non-pinch curved-endpoint snap (§2.3).

The flat-cap fixture below is probe B from that plan (§1.3/§1.5), rebuilt at a smaller scale
(L=1000 instead of 10000) purely for test speed via the same read-only `harness.generators`
helper functions the plan's own probe used -- these are pure geometry builders with no side
effects on the milestone truth cache."""
import math

import numpy as np
import pytest
import trimesh
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from harness import generators
from pipeline import engine

# Radii/z_cap/R_bore match the plan's own probe B EXACTLY (§1.3) -- the pinch-override gate's
# `50.0` mm acceptance window (engine.py's `is_pinch_*`/A.2) is an ABSOLUTE constant, calibrated
# against full-scale (~10000 mm) motors, so it does not scale down: shrinking these radii to
# "test size" would shrink the envelope-vs-bore gap below that fixed 50 mm window and silently
# test a different (accidentally-pinching) geometry instead. Only `L` (the barrel length, which
# does not affect the near-cap dome physics at all) is shortened, purely for test speed.
_L, _R_O, _DOME_H, _Z_CAP, _R_BORE, _CHORD_TOL = 2000.0, 1000.0, 500.0, 150.0, 300.0, 1.0


def _envelope_radius(R_o, dome_h, z):
    """Analytic 2:1 ellipsoidal dome envelope radius at axial position `z` (fore dome, apex at
    z=0) -- the ground truth `_measure_end_radius` must recover from the mesh."""
    return R_o * math.sqrt(max(0.0, 1.0 - ((dome_h - z) / dome_h) ** 2))


@pytest.fixture(scope="module")
def flat_cap_stl(tmp_path_factory):
    """Probe B's construction (plan §1.5), scaled down: a capsule outer shape flat-capped at
    z=15 (well inside real dome curvature) with a straight bore through it -- the exact shape
    class that arms the dome-pinch-override bug (report §4a) at the fore end."""
    path = tmp_path_factory.mktemp("end_measure") / "probeB_small.stl"
    outer = generators._capsule_outer_shape(_L, _R_O, _DOME_H)
    box = BRepPrimAPI_MakeBox(gp_Pnt(-5 * _R_O, -5 * _R_O, -5 * _R_O),
                              gp_Pnt(5 * _R_O, 5 * _R_O, _Z_CAP)).Shape()
    capped = BRepAlgoAPI_Cut(outer, box)
    capped.Build()
    assert capped.IsDone()
    bore = generators._straight_bore(_R_BORE, _L)
    cut = BRepAlgoAPI_Cut(capped.Shape(), bore)
    cut.Build()
    assert cut.IsDone()
    generators._write_stl(cut.Shape(), path, chord_tol=_CHORD_TOL)
    return path


@pytest.fixture(scope="module")
def flat_cap_mesh(flat_cap_stl):
    return trimesh.load(str(flat_cap_stl))


@pytest.fixture(scope="module")
def m2_mesh():
    truth = generators.make("M2")
    return trimesh.load(str(truth.stl_path))


def test_measure_end_radius_on_flat_cap_matches_envelope(flat_cap_mesh):
    """A flat cap well inside real dome curvature: `_measure_end_radius` must read the true
    (large) envelope radius there, not anything close to the bore radius -- this is the
    measurement the outward pinch-override gate (§2.2) checks against."""
    z_min = float(flat_cap_mesh.bounds[0][2])
    resid_gate = 1.5 * _CHORD_TOL
    result = engine._measure_end_radius(flat_cap_mesh, z_min, True, _CHORD_TOL, resid_gate)
    assert result is not None
    R_meas, max_resid, z_probe = result
    expected = _envelope_radius(_R_O, _DOME_H, z_min)
    # `_measure_end_radius` probes `inset = max(2*chord_tol, ...)` INWARD from z_min, onto a
    # still-curving dome -- at chord_tol=1.0 that is a 2 mm axial offset, which on this dome's
    # local slope (~1.9 mm of radius per mm of z near the cap, matching the plan's own probe B
    # measurement) legitimately shifts the true envelope radius there by a few mm from the
    # envelope value evaluated exactly AT z_min; `abs=5.0` covers that expected offset without
    # masking a real regression (the pre-fix failure mode was a ~54 mm phantom extension, an
    # order of magnitude larger).
    assert R_meas == pytest.approx(expected, abs=5.0)
    assert max_resid < resid_gate
    assert z_probe > z_min  # probed INWARD from the fore bound, onto real material
    # The load-bearing decision itself (§2.2): this measurement is FAR outside the 50 mm window
    # around the bore radius, so the outward override must refuse to extend the part here.
    assert R_meas > _R_BORE + 50.0


def test_measure_end_radius_on_m2_pinch_end_matches_bore(m2_mesh):
    """A genuine dome/bore pinch (M2, R_i=300): the measured radius near the true tip must land
    close to the bore radius, within the same 50 mm window `is_pinch_start`/`is_pinch_end`
    already use -- so the outward override's measurement-support check accepts a real pinch."""
    z_min = float(m2_mesh.bounds[0][2])
    chord_tol = 0.5
    resid_gate = 1.5 * chord_tol
    result = engine._measure_end_radius(m2_mesh, z_min, True, chord_tol, resid_gate)
    assert result is not None
    R_meas, _max_resid, _z_probe = result
    assert abs(R_meas - 300.0) < 50.0


def test_flat_cap_rebuild_lands_at_the_true_cap_not_past_it(flat_cap_stl):
    """The core Phase A regression (report §4a, plan probe B): before the fix, the dome-pinch
    override extended the fore end outward past a real flat cap (measured 54.4 mm on the plan's
    full-scale probe). At this smaller scale the rebuilt solid's z-min must land AT the mesh's
    own cap (within the bounds check's own tolerance), never noticeably below it."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        opts = engine.RebuildOptions(
            input_stl=str(flat_cap_stl), output=f"{tmp}/out.step",
            axis="z", sections=60, chord_tol=_CHORD_TOL, refine_passes=0,
            report=f"{tmp}/out.report.json")
        result = engine.rebuild(opts)
    z_bounds = result.report["verification"]["bounds"]["z"]
    assert z_bounds["solid_mm"][0] >= _Z_CAP - z_bounds["tol_mm"]
    assert z_bounds["pass"] is True


def test_flat_cap_rebuild_endpoint_snaps_to_measured_envelope_radius(flat_cap_stl):
    """§2.3: the non-pinch curved endpoint's radius comes from `_measure_end_radius`, not the
    (possibly poorly-conditioned) dome model extrapolated to the cap -- slice the REBUILT solid
    right at the fore cap and check its own radius there agrees with the analytic envelope
    radius (a global bounds check can't see this: the dome's max radius is at the shoulder, far
    from the cap, and would pass even if the cap-plane radius itself were wrong)."""
    import tempfile

    from pipeline.fitting import fit_circle_robust
    from pipeline.slicing import slice_station

    with tempfile.TemporaryDirectory() as tmp:
        opts = engine.RebuildOptions(
            input_stl=str(flat_cap_stl), output=f"{tmp}/out.step",
            axis="z", sections=60, chord_tol=_CHORD_TOL, refine_passes=0,
            stl=f"{tmp}/out.stl", report=f"{tmp}/out.report.json")
        engine.rebuild(opts)
        solid_mesh = trimesh.load(f"{tmp}/out.stl")

    polys, _zz = slice_station(solid_mesh, _Z_CAP + 1.0, _CHORD_TOL)
    assert len(polys) == 1
    ext = polys[0].exterior.coords
    cx, cy, R_slice, max_resid, _ = fit_circle_robust(np.asarray(ext))
    expected = _envelope_radius(_R_O, _DOME_H, _Z_CAP + 1.0)
    assert R_slice == pytest.approx(expected, abs=2.0)
