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

from OCP.gp import gp_Pnt, gp_Ax1, gp_Dir, gp_Vec
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_MakeFace,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol, BRepPrimAPI_MakePrism
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.TColgp import TColgp_HArray1OfPnt
from OCP.TopoDS import TopoDS_Shape

from pipeline.fitting import rdp, detect_arc_runs


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


def build_prism_solid(xy_pts, z_lo: float, z_hi: float, r_fillet_thresh: float = None) -> TopoDS_Shape:
    """`xy_pts`: closed-ring (x, y) points (first == last, as returned by a shapely polygon's
    `.exterior.coords`/`.interiors[i].coords`), raw or lightly deduped. Builds the cross-section
    wire as a hybrid of exact circular-arc edges (`GC_MakeArcOfCircle`, 3-point through each
    detected fillet run) bridged by straight edges everywhere else, then extrudes that single
    closed-wire profile from z_lo to z_hi. This is the non-axisymmetric counterpart to
    `build_revolve_solid`, for a cross-section that is constant along the axis (a prismatic
    bore/envelope, e.g. M3's star bore) rather than one that is a surface of revolution.

    Two prior approaches were tried and reverted (PROGRESS.md M3 log):
    - A straight-edge polygon (one edge per RDP-retained vertex) passed every accuracy gate
      (volume/bbox/surface_deviation) but `gmsh_tet` failed at min_quality~0.016 (gate 0.1):
      even the *truth* STEP only barely clears that gate (0.137) at the same hmash, so the sharp
      polyline corners approximating each fillet arc condition the tet mesh badly right there.
    - A single closed periodic B-spline through all the ring points collapsed the whole boundary
      to 1 face (solving face_count_max) but rounded off the star's sharp valley cusps, pushing
      volume_err_pct to ~0.89% (gate 0.1%).
    The arc+line hybrid fixes both: `detect_arc_runs` (pipeline/fitting.py) classifies each
    boundary point via a local windowed circle fit (small/finite local radius = curved, large/
    ill-conditioned = straight run); each curved run becomes one exact 3-point circular arc edge
    (matches the truth generator's own construction almost exactly, so surface_deviation and
    volume error both stay tiny), each straight run collapses to a single straight edge between
    consecutive arc endpoints (keeps face_count_max low, and cusps stay sharp since they're true
    polygon vertices, not spline-smoothed). Falls back to a straight-edge polygon (no arcs
    detected) when the ring has no curvature to find.

    `r_fillet_thresh` defaults to `None`, which lets `detect_arc_runs` pick the curved/straight
    split itself from the ring's own local-radius distribution (see its docstring) rather than a
    fixed fraction of the ring's bounding radius. A fixed fraction worked for M3 (one small
    fillet radius vs. ill-conditioned straight sides) but is wrong for M4/M5's finocyl bore,
    where a ~300 mm main-bore arc is real curvature needing an exact arc edge, not a fraction of
    the ~700 mm fin-tip radius away from a hardcoded threshold that happened to put it on the
    "straight" side (18 mm chord sagitta against a 1 mm deviation gate)."""
    pts = [p for p in xy_pts]
    if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-9:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError("prism cross-section needs at least 3 distinct points")

    arcs = detect_arc_runs(pts, r_fillet_thresh)

    def P(idx):
        x, y = pts[idx]
        return gp_Pnt(x, y, z_lo)

    mkwire = BRepBuilderAPI_MakeWire()
    if not arcs:
        n = len(pts)
        for i in range(n):
            mkwire.Add(BRepBuilderAPI_MakeEdge(P(i), P((i + 1) % n)).Edge())
    else:
        n_arcs = len(arcs)
        for i in range(n_arcs):
            run = arcs[i]
            p0, pm, p1 = P(run[0]), P(run[len(run) // 2]), P(run[-1])
            # A run can degenerate to 1-2 (near-)duplicate points at the classifier's boundary
            # (a single sample straddling a real corner, misread as "curved" by its own
            # 5-point local window) — GC_MakeArcOfCircle raises Standard_Failure on a
            # zero-length/collinear 3-point set. Fall back to a straight pass-through edge for
            # just that run rather than losing the whole cross-section to a crash.
            try:
                if len(run) < 2:
                    raise ValueError("degenerate arc run")
                arc = GC_MakeArcOfCircle(p0, pm, p1).Value()
                mkwire.Add(BRepBuilderAPI_MakeEdge(arc).Edge())
            except Exception:
                mkwire.Add(BRepBuilderAPI_MakeEdge(p0, p1).Edge())
            next_run = arcs[(i + 1) % n_arcs]
            mkwire.Add(BRepBuilderAPI_MakeEdge(p1, P(next_run[0])).Edge())

    if not mkwire.IsDone():
        raise RuntimeError("prism cross-section wire construction failed")
    wire = mkwire.Wire()
    if not wire.Closed():
        raise RuntimeError("prism cross-section wire is not closed")

    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, z_hi - z_lo))
    if not prism.IsDone():
        raise RuntimeError("prism extrusion failed")
    return prism.Shape()
