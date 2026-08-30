"""Stage 6 (solids): axisymmetric revolve fast path. MISSION.md §5.2 step 6, §5.1.

Only the "all circles centered on the axis" decision-order case is implemented so far: a chain
is a list of (z, R) samples, one per station, whose fitted circle centers all sit on the axis.
The meridian (R, z) polyline is RDP-simplified then revolved 360 deg about Z. Loft / prism paths
(non-axisymmetric sections) are not yet built — out of scope until M2/M3 need them.

Tried and reverted this iteration (see PROGRESS.md iter 15): fitting a single global curve
(both `GeomAPI_Interpolate` and `GeomAPI_PointsToBSpline`) through the RDP-retained (z, R)
points instead of straight chords, to remove the chord-vs-arc volume bias on M2's domes.
Interpolation overshot +20% volume from a single bulge between the sparse mid-cylinder points
(non-uniform station spacing makes a global cubic spline badly conditioned); the approximating
fit avoided the overshoot but failed to revolve (self-intersecting profile) across the steep,
under-sampled ~100 mm gap between the pinch endpoint and the first station outside the
excluded-residual inset band. Both are global-curve approaches fighting the same problem: this
meridian mixes a near-vertical-tangent region (the dome-bore pinch) with a flat one (the
cylinder) in one z-parametrized curve. The actual fix landed in `pipeline/cli.py`
(`_fill_pinch_gap`): densify only that specific gap with the already-validated local
quadratic-in-R^2 model, then keep this module's simple straight-chord polyline everywhere else.
"""
import math

from OCP.gp import gp_Pnt, gp_Ax1, gp_Dir
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_MakeFace,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.TColgp import TColgp_HArray1OfPnt
from OCP.TopoDS import TopoDS_Shape

from pipeline.fitting import rdp


def _dedupe(pts):
    dedup = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - dedup[-1][0]) > 1e-9:
            dedup.append(p)
    return dedup


def build_revolve_solid(z_r_pairs, chord_tol: float, curve_windows=None) -> TopoDS_Shape:
    """`z_r_pairs`: list of (z, R) sorted by increasing z, already extended to the true axial
    extent (envelope: true motor ends; cutter: ends past the envelope by eps_cut). Builds a
    closed meridian wire (radial edge at z0, wall polyline, radial edge at zn, axis edge back to
    z0) and revolves it into a solid.

    `curve_windows`: optional list of (z_lo, z_hi) ranges (iter 17, M2). Points whose z falls
    inside a window are NOT straight-chord/RDP-simplified; instead every point in that window
    (in order) is fit to a single interpolating B-spline edge (`GeomAPI_Interpolate`), replacing
    what would otherwise be dozens of straight-chord faces (one dR/dz-driven chord per
    `_densify_dome_chords` sample) with ONE curved face. This is what actually broke the
    accuracy-vs-face_count_max tension iter 16 hit: raising `_densify_dome_chords`'s n_samples
    to satisfy `surface_deviation_max_mm`/`p99` (needs ~50+ points per dome-pinch window) always
    blew `face_count_max<=40` (each straight chord became its own revolved face; 50 points in
    two windows alone is ~100 faces). A single spline edge costs the same 1 face regardless of
    how many points went into fitting it. Safe here (unlike the reverted *global* spline attempt
    in PROGRESS.md iter 15) because each window is scoped to one smooth analytic region only
    (the validated quadratic-in-R^2 dome band from `_fit_r2_quadratic`) with no flat-cylinder
    points mixed in and no circle-fit noise (the points come from `_densify_dome_chords`'s clean
    model resample, not raw stations) — the exact conditions that broke the earlier attempt
    (mixed near-vertical/flat regions, real noisy stations) don't apply inside a window."""
    curve_windows = curve_windows or []
    pts_all = sorted(z_r_pairs, key=lambda p: p[0])
    if len(pts_all) < 2:
        raise ValueError("revolve meridian needs at least 2 distinct axial points")

    def which_window(z):
        for i, (lo, hi) in enumerate(curve_windows):
            if lo - 1e-6 <= z <= hi + 1e-6:
                return i
        return None

    runs = []
    cur_idx, cur = which_window(pts_all[0][0]), [pts_all[0]]
    for p in pts_all[1:]:
        idx = which_window(p[0])
        if idx != cur_idx:
            runs.append((cur_idx, cur))
            cur_idx, cur = idx, [p]
        else:
            cur.append(p)
    runs.append((cur_idx, cur))

    z0, r0 = pts_all[0]
    zn, rn = pts_all[-1]

    profile_edges = []
    for i, (idx, run_pts) in enumerate(runs):
        if idx is None:
            # A straight (non-window) run only owns the points strictly between two windows
            # (or the profile's own ends) -- it does NOT include the window's own boundary
            # point, so without anchoring, the edge connecting the window's last point to this
            # run's first point would simply never be built (a silent gap in the wire, not a
            # wire-construction error -- MakeWire.IsDone() stays true on the remaining pieces).
            # Anchor to the neighboring run's shared boundary point so RDP sees, and connects,
            # the true adjacency.
            pts_for_rdp = list(run_pts)
            if i > 0:
                pts_for_rdp = [runs[i - 1][1][-1]] + pts_for_rdp
            if i < len(runs) - 1:
                pts_for_rdp = pts_for_rdp + [runs[i + 1][1][0]]
            simplified = _dedupe(rdp(pts_for_rdp, 0.5 * chord_tol))
            for (za, ra), (zb, rb) in zip(simplified, simplified[1:]):
                profile_edges.append(
                    BRepBuilderAPI_MakeEdge(gp_Pnt(ra, 0.0, za), gp_Pnt(rb, 0.0, zb)).Edge())
        else:
            run_pts = _dedupe(run_pts)
            if len(run_pts) < 2:
                continue
            harray = TColgp_HArray1OfPnt(1, len(run_pts))
            for i, (z, r) in enumerate(run_pts):
                harray.SetValue(i + 1, gp_Pnt(r, 0.0, z))
            interp = GeomAPI_Interpolate(harray, False, 1e-7)
            interp.Perform()
            profile_edges.append(BRepBuilderAPI_MakeEdge(interp.Curve()).Edge())

    edges = [BRepBuilderAPI_MakeEdge(gp_Pnt(0.0, 0.0, z0), gp_Pnt(r0, 0.0, z0)).Edge()]
    edges.extend(profile_edges)
    edges.append(BRepBuilderAPI_MakeEdge(gp_Pnt(rn, 0.0, zn), gp_Pnt(0.0, 0.0, zn)).Edge())
    edges.append(BRepBuilderAPI_MakeEdge(gp_Pnt(0.0, 0.0, zn), gp_Pnt(0.0, 0.0, z0)).Edge())

    mkwire = BRepBuilderAPI_MakeWire()
    for e in edges:
        mkwire.Add(e)
    if not mkwire.IsDone():
        raise RuntimeError("revolve meridian wire construction failed")

    face = BRepBuilderAPI_MakeFace(mkwire.Wire(), True).Face()
    axis = gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
    revol = BRepPrimAPI_MakeRevol(face, axis, 2.0 * math.pi)
    if not revol.IsDone():
        raise RuntimeError("revolve failed")
    return revol.Shape()
