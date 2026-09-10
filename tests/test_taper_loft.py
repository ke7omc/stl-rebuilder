"""Tests for Phases B/C of `docs/plans/tapered_bore_dome_pinch_and_surface_area.md`: the
`_build_tapered_bore_cutter` rung selector (§3) and its ring-preparation helpers (§4.2).

Uses the real M3/M6/M16 truth STLs (already-verified milestone geometry) rather than smaller
synthetic fixtures, since the whole point of these tests is the SELECTOR's classification of
real station data -- a smaller hand-built fixture would just be a worse copy of a milestone
that already exists and is exercised end-to-end by `harness/score.py`."""
import math

import numpy as np
import pytest
import trimesh
from OCP.BRepCheck import BRepCheck_Analyzer

from harness import generators
from pipeline import engine


def _bore_rings_for(mesh, z_lo, z_hi, chord_tol, n=40):
    """Collect (z, shapely interior ring) pairs across [z_lo, z_hi] the same way the main
    station loop does: slice, take the single interior hole of a single-outer-loop station."""
    from pipeline.slicing import slice_station

    rings = []
    for z in np.linspace(z_lo, z_hi, n):
        polys, zz = slice_station(mesh, float(z), chord_tol)
        if len(polys) == 1 and len(polys[0].interiors) == 1:
            rings.append((zz, polys[0].interiors[0]))
    return rings


def test_constant_cross_section_selects_prism_rung(tmp_path):
    """M3's star bore is axially constant -- rung 1 (`_build_prism_bore`), byte-identical to
    the pre-plan behavior."""
    truth = generators.make("M3")
    mesh = trimesh.load(str(truth.stl_path))
    chord_tol = 0.5
    z_lo, z_hi = float(mesh.bounds[0][2]) + 50.0, float(mesh.bounds[1][2]) - 50.0
    bore_rings = _bore_rings_for(mesh, z_lo, z_hi, chord_tol)
    assert len(bore_rings) > 10

    solid, path = engine._build_tapered_bore_cutter(
        bore_rings, z_lo, z_hi, 5.0, 5.0, chord_tol)
    assert path == "prism"
    assert BRepCheck_Analyzer(solid).IsValid()


def test_uniform_taper_selects_proportional_loft_rung(tmp_path):
    """M6's star bore scales uniformly (tip/valley/fillets all by the same factor end to end)
    -- rung 2 (`_build_proportional_bore_loft`), gated by `_check_proportional_scaling`'s new
    acceptance test, which must ACCEPT a true uniform scaling."""
    truth = generators.make("M6")
    mesh = trimesh.load(str(truth.stl_path))
    chord_tol = 0.5
    z_lo, z_hi = float(mesh.bounds[0][2]) + 50.0, float(mesh.bounds[1][2]) - 50.0
    bore_rings = _bore_rings_for(mesh, z_lo, z_hi, chord_tol)
    assert len(bore_rings) > 10

    solid, path = engine._build_tapered_bore_cutter(
        bore_rings, z_lo, z_hi, 5.0, 5.0, chord_tol)
    assert path == "loft_proportional"
    assert BRepCheck_Analyzer(solid).IsValid()


def test_non_proportional_star_rejects_proportional_and_uses_ring_loft(tmp_path):
    """M16's two-family star grows at genuinely different rates end to end -- no scalar maps
    one end onto the other, so `_check_proportional_scaling` must REJECT rung 2 and route to
    rung 3. With the mesh available, rung 3's preferred construction is the analytic
    tangent-fillet loft (`_build_arc_fillet_loft_bore`, path `"loft_arcs"`) -- the joint
    arc+flank fit whose acceptance gates (segmentation vote, pooled vertex residual) M16's
    clean star must clear; the B-spline `"loft_rings"` path remains the fallback for zones
    the fit refuses. Either way the solid must be valid and plausible-volume (the safety net
    §4.4: BRepCheck_Analyzer alone would not catch a self-intersecting-but-"valid" loft)."""
    truth = generators.make("M16")
    mesh = trimesh.load(str(truth.stl_path))
    chord_tol = 0.5
    # Star zone only (aft of the z=6000 event, per the M16 spec) -- the pure non-circular bore.
    z_lo, z_hi = 6050.0, 9700.0
    bore_rings = _bore_rings_for(mesh, z_lo, z_hi, chord_tol, n=60)
    assert len(bore_rings) > 20

    solid, path = engine._build_tapered_bore_cutter(
        bore_rings, z_lo, z_hi, 5.0, 5.0, chord_tol, mesh=mesh)
    assert path == "loft_arcs"
    assert BRepCheck_Analyzer(solid).IsValid()
    # Volume sanity: must be in the right ballpark (not the self-intersecting-garbage class of
    # failure this plan measured and fixed -- orders of magnitude off).
    areas = np.array([engine._ring_area(np.asarray(r.coords)) for _, r in bore_rings])
    expected = float(areas.mean()) * (z_hi - z_lo)
    actual = abs(engine._solid_volume(solid))
    assert 0.5 * expected <= actual <= 2.0 * expected


def test_check_proportional_scaling_accepts_true_uniform_scale():
    """Direct unit test of the acceptance test itself (§3.1): a hand-built exactly-uniform
    scaling must pass regardless of any real-mesh noise (isolates the classifier from the
    rung-2/3 construction machinery)."""
    from shapely.geometry import Polygon

    n = 40
    theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ref_r = 100.0 + 20.0 * np.cos(3 * theta)  # a lumpy but fixed shape
    ref_pts = np.column_stack([ref_r * np.cos(theta), ref_r * np.sin(theta)])
    ref_area = Polygon(ref_pts).area

    zs = np.linspace(0.0, 1000.0, 10)
    scales = 1.0 + 0.5 * (zs / 1000.0)  # linear scale 1.0 -> 1.5
    areas = ref_area * scales ** 2
    coef = np.polyfit(zs, areas, 2)

    bore_rings = []
    for z, s in zip(zs, scales):
        pts = ref_pts * s
        ring = Polygon(pts).exterior
        bore_rings.append((float(z), ring))

    assert engine._check_proportional_scaling(bore_rings, coef, ref_pts, ref_area, chord_tol=0.5)


def test_check_proportional_scaling_rejects_non_uniform_scale():
    """The mirror case: two independently-scaling lobes (a crude two-family star stand-in)
    must be REJECTED -- no single scalar reproduces both families' radii at every station."""
    from shapely.geometry import Polygon

    n = 40
    theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
    base_r = 100.0 + 20.0 * np.cos(3 * theta)
    ref_pts = np.column_stack([base_r * np.cos(theta), base_r * np.sin(theta)])
    ref_area = Polygon(ref_pts).area

    zs = np.linspace(0.0, 1000.0, 10)
    coef = np.polyfit(zs, ref_area * (1.0 + 0.5 * (zs / 1000.0)) ** 2, 2)

    bore_rings = []
    for z in zs:
        t = z / 1000.0
        # Family A (theta in [0, pi)) scales x1.5 by the end; family B (theta in [pi, 2pi))
        # barely grows at all -- a real per-half-plane divergence, not mesh noise.
        scale = np.where(theta < np.pi, 1.0 + 0.5 * t, 1.0 + 0.02 * t)
        pts = np.column_stack([base_r * scale * np.cos(theta), base_r * scale * np.sin(theta)])
        bore_rings.append((float(z), Polygon(pts).exterior))

    assert not engine._check_proportional_scaling(
        bore_rings, coef, ref_pts, ref_area, chord_tol=0.5)
