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

import numpy as np

from OCP.gp import gp_Pnt, gp_Ax1, gp_Ax2, gp_Dir, gp_Vec, gp_Trsf, gp_Elips
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_Transform,
)
from OCP.TopoDS import TopoDS
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol, BRepPrimAPI_MakePrism, BRepPrimAPI_MakeCylinder
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.GC import GC_MakeArcOfCircle, GC_MakeArcOfEllipse
from OCP.GeomAbs import GeomAbs_C2
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.Approx import Approx_ChordLength
from OCP.TColgp import TColgp_HArray1OfPnt
from OCP.TopoDS import TopoDS_Shape

from pipeline import tol
from pipeline.fitting import rdp, detect_arc_runs, fit_circle


def _dedupe(pts):
    dedup = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - dedup[-1][0]) > 1e-9:
            dedup.append(p)
    return dedup


def _ellipse_arc_edge(run_pts):
    """Fit an exact elliptical arc through a dome-window point run and return the matching
    TopoDS_Edge, or None if the points don't support one (caller falls back to a B-spline
    interpolation edge).

    `_densify_dome_chords` resamples a validated quadratic-in-R^2 dome model uniformly in z, so
    a local re-fit of the SAME quadratic form to `run_pts` recovers that model (any single
    point that was snapped to an exact bore radius at one end barely perturbs a least-squares
    fit dominated by ~60 clean model points). The truth generator builds real dome surfaces as
    an exact `GC_MakeArcOfEllipse` revolve (harness/generators.py `_ellipse_dome_edge`); our own
    window previously matched that shape only approximately (a cubic B-spline through discrete
    samples), and `BRepMesh_IncrementalMesh` triangulates a spline-of-revolution differently
    enough from an ellipse-of-revolution that `surface_deviation_p99_by_region` measured ~0.011
    mm of apparent deviation at M10's fore_dome even though the spline's own (z, R) values were
    already within ~0.002 mm of the true profile (verified by comparing max-radius-per-z-slice
    profiles directly) — a triangulation-pattern mismatch, not a real geometric error. Building
    the exact analytic curve here removes that mismatch at the source.

    `R(z)^2 = a*(z-z0)^2 + b*(z-z0) + c` with `a < 0` completes the square to an ellipse:
    center `z_c = z0 - b/(2a)`, radial (major) semi-axis `sqrt(c - b^2/(4a))`, axial (minor)
    semi-axis `radial / sqrt(-a)` — the same relationship `_dome_shoulder_z` already exploits
    for the parabola-in-R^2 apex. The apex (R -> 0) is whichever end of `run_pts` has the
    smaller radius; `n_dir` picks the ellipse winding (mirrors
    `harness/generators.py::_ellipse_dome_edge`) so the arc's angle-pi/2 end lands on that side.
    """
    zs = np.asarray([p[0] for p in run_pts], dtype=float)
    rs = np.asarray([p[1] for p in run_pts], dtype=float)
    if len(zs) < 5:
        return None
    z0 = zs[0]
    try:
        coef = np.polyfit(zs - z0, rs ** 2, 2)
    except Exception:
        return None
    a, b, c = (float(v) for v in coef)
    if not (np.isfinite(a) and np.isfinite(b) and np.isfinite(c)) or a >= 0.0:
        return None
    z_center = z0 - b / (2.0 * a)
    r_max_sq = c - b * b / (4.0 * a)
    if not np.isfinite(r_max_sq) or r_max_sq <= 0.0:
        return None
    radial = math.sqrt(r_max_sq)
    if -a <= 0.0:
        return None
    axial = radial / math.sqrt(-a)
    if not (np.isfinite(radial) and np.isfinite(axial)) or axial <= 0.0:
        return None
    r_pred = np.sqrt(np.maximum(0.0, np.polyval(coef, zs - z0)))
    if float(np.max(np.abs(r_pred - rs))) > max(1e-3, 0.05 * radial):
        return None  # fit doesn't actually explain the points -- not a clean dome window

    z_first, z_last = float(zs[0]), float(zs[-1])
    apex_at_first = rs[0] < rs[-1]
    n_dir = (0.0, 1.0, 0.0) if apex_at_first else (0.0, -1.0, 0.0)

    def t_of_z(z: float):
        s = (z_center - z) / axial if apex_at_first else (z - z_center) / axial
        s = min(1.0, max(-1.0, s))
        return math.asin(s)

    t_first, t_last = t_of_z(z_first), t_of_z(z_last)
    tlo, thi = (t_first, t_last) if t_first <= t_last else (t_last, t_first)
    if thi - tlo < 1e-9:
        return None
    try:
        ax2 = gp_Ax2(gp_Pnt(0.0, 0.0, z_center), gp_Dir(*n_dir), gp_Dir(1.0, 0.0, 0.0))
        elips = gp_Elips(ax2, radial, axial)
        arc = GC_MakeArcOfEllipse(elips, tlo, thi, True)
        if not arc.IsDone():
            return None
        return BRepBuilderAPI_MakeEdge(arc.Value()).Edge()
    except Exception:
        return None


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

    # First pass: build every CURVE-window edge and record its own ACTUAL endpoints (queried
    # from the built edge itself, via `BRepAdaptor_Curve`) -- not the raw seed (z, r) that fed
    # `_ellipse_arc_edge`'s fit. The two can differ by a small but real amount even for a well-
    # conditioned fit (measured on M16's fore dome window: an independently-refit ellipse's own
    # `t_of_z` re-parametrization landed the edge's end at R=1000.058, vs. the seed's R=1000.000,
    # ~0.06 mm apart) because `_ellipse_arc_edge` fits and re-parametrizes locally; it does not
    # exactly round-trip its own input points. The SECOND pass below anchors every straight-run
    # RDP segment (and the axis-connecting radial edges) to these ACTUAL endpoints instead of
    # the raw seed, which is what `BRepBuilderAPI_MakeWire` needs for `wire.Closed()` to actually
    # be true -- a same-scale-but-nonzero gap is invisible to `mkwire.IsDone()` (stays True) but
    # leaves the wire open, producing an invalid face and a silently degenerate (measured on
    # M16: exactly zero-volume, `BRepCheck_Analyzer`-invalid) revolve with no exception raised
    # anywhere. This keeps `_ellipse_arc_edge` itself byte-identical (still returns exactly the
    # edge it always has) -- only how its neighbors connect to it changes.
    curve_edges = {}
    for i, (idx, run_pts) in enumerate(runs):
        if idx is None:
            continue
        run_pts_d = _dedupe(run_pts)
        if len(run_pts_d) < 2:
            continue
        edge = _ellipse_arc_edge(run_pts_d)
        if edge is None:
            harray = TColgp_HArray1OfPnt(1, len(run_pts_d))
            for j, (z, r) in enumerate(run_pts_d):
                harray.SetValue(j + 1, gp_Pnt(r, 0.0, z))
            interp = GeomAPI_Interpolate(harray, False, 1e-7)
            interp.Perform()
            edge = BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()
        ad = BRepAdaptor_Curve(edge)
        p_start = ad.Value(ad.FirstParameter())
        p_end = ad.Value(ad.LastParameter())
        curve_edges[i] = (edge, (p_start.Z(), p_start.X()), (p_end.Z(), p_end.X()))

    def _actual_boundary(run_idx: int, want_start: bool):
        """The ACTUAL (z, r) of curve run `run_idx`'s start or end, oriented to match that
        run's own raw seed ordering (a run built `at_start=False`, i.e. descending z, has its
        seed's "first" point at the LARGER z -- `_ellipse_arc_edge`'s own `apex_at_first` logic
        does not care about seed order, only relative radius, so the built edge's geometric
        start/end need not line up with the seed's index order)."""
        edge_entry = curve_edges.get(run_idx)
        if edge_entry is None:
            return None
        _edge, actual_start, actual_end = edge_entry
        seed = runs[run_idx][1]
        seed_pt = seed[0] if want_start else seed[-1]
        # Whichever actual endpoint is closer (in z) to the requested seed point is the one that
        # corresponds to it -- robust regardless of the curve's own internal parametrization
        # direction.
        d_start = abs(actual_start[0] - seed_pt[0])
        d_end = abs(actual_end[0] - seed_pt[0])
        return actual_start if d_start <= d_end else actual_end

    profile_edges = []
    for i, (idx, run_pts) in enumerate(runs):
        if idx is None:
            # A straight (non-window) run only owns the points strictly between two windows
            # (or the profile's own ends) -- it does NOT include the window's own boundary
            # point, so without anchoring, the edge connecting the window's last point to this
            # run's first point would simply never be built (a silent gap in the wire, not a
            # wire-construction error -- MakeWire.IsDone() stays true on the remaining pieces).
            # Anchor to the neighboring run's shared boundary point (its ACTUAL built endpoint
            # when that neighbor is a curve run, see above) so RDP sees, and connects, the true
            # adjacency.
            pts_for_rdp = list(run_pts)
            if i > 0:
                prev_boundary = _actual_boundary(i - 1, want_start=False)
                pts_for_rdp = [prev_boundary if prev_boundary is not None
                              else runs[i - 1][1][-1]] + pts_for_rdp
            if i < len(runs) - 1:
                next_boundary = _actual_boundary(i + 1, want_start=True)
                pts_for_rdp = pts_for_rdp + [next_boundary if next_boundary is not None
                                             else runs[i + 1][1][0]]
            r_ref = max(r for _z, r in pts_for_rdp)
            simplified = _dedupe(rdp(pts_for_rdp, tol.rdp_profile_eps(chord_tol, r_ref)))
            for (za, ra), (zb, rb) in zip(simplified, simplified[1:]):
                profile_edges.append(
                    BRepBuilderAPI_MakeEdge(gp_Pnt(ra, 0.0, za), gp_Pnt(rb, 0.0, zb)).Edge())
        else:
            entry = curve_edges.get(i)
            if entry is None:
                continue
            profile_edges.append(entry[0])

    # The two axis-connecting radial edges use the profile's own true first/last (z, r) -- when
    # that end is itself a curve run, use its ACTUAL built endpoint for the same reason as above
    # (this is `edges[0]`/`edges[-1]` below meeting the curve edge's real start/end, not its raw
    # seed value).
    if 0 in curve_edges:
        z0, r0 = _actual_boundary(0, want_start=True)
    if (len(runs) - 1) in curve_edges:
        zn, rn = _actual_boundary(len(runs) - 1, want_start=False)

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


def build_filleted_wedge_solid(z_lo: float, z_hi: float, r_in: float, r_out: float,
                                f_lo: float, f_hi: float, theta_c: float,
                                theta_half: float) -> TopoDS_Shape:
    """An angular *wedge* cutter: the meridian rectangle [r_in, r_out] x [z_lo, z_hi] with its
    four corners rounded (radius `f_lo` at the z_lo end, `f_hi` at the z_hi end) revolved about Z
    through `2*theta_half`, centred on `theta_c`.

    This is the exact shape class of a radial slot whose axial ends are filleted: every section
    is an annular sector of the SAME angular span, and the radial extent follows a circular arc
    into each end plane. A constant-cross-section prism over the interior of such a chain leaves
    the whole fillet band unmodelled -- on M8 that is a 150 mm window at each end carrying up to
    43 mm of surface deviation, i.e. the entire deviation failure.

    Four arcs + four straight edges revolve into 8 lateral faces plus 2 planar flanks per wedge,
    so eight slots cost ~80 faces -- a densely sampled loft of the same profile costs several
    hundred and busts `face_count_max`.
    """
    def P(r, z):
        return gp_Pnt(r, 0.0, z)

    def arc_or_line(p0, p1, cr, cz, radius):
        """Circular edge p0->p1 about centre (cr, cz); a straight edge when the radius is nil."""
        if radius <= 1.0e-9:
            return BRepBuilderAPI_MakeEdge(p0, p1).Edge()
        u0 = np.array([p0.X() - cr, p0.Z() - cz], dtype=float)
        u1 = np.array([p1.X() - cr, p1.Z() - cz], dtype=float)
        bis = u0 / max(np.linalg.norm(u0), 1e-12) + u1 / max(np.linalg.norm(u1), 1e-12)
        n = np.linalg.norm(bis)
        if n < 1.0e-9:
            return BRepBuilderAPI_MakeEdge(p0, p1).Edge()
        bis = bis / n * radius
        pm = gp_Pnt(cr + bis[0], 0.0, cz + bis[1])
        return BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(p0, pm, p1).Value()).Edge()

    # A fillet clamped to *exactly* half the axial/radial span makes two meridian corners
    # coincide (e.g. p1 == p8 when f_lo == 0.5*(r_out-r_in)), and the unguarded MakeEdge on a
    # zero-length edge below throws Standard_Failure. `margin` keeps the clamp a hair short of
    # that boundary instead, mirroring the `radius <= 1.0e-9` epsilon already used in arc_or_line.
    margin = 1.0e-6
    f_lo = max(0.0, min(f_lo, 0.5 * (z_hi - z_lo) - margin, 0.5 * (r_out - r_in) - margin))
    f_hi = max(0.0, min(f_hi, 0.5 * (z_hi - z_lo) - margin, 0.5 * (r_out - r_in) - margin))

    p1, p2 = P(r_in + f_lo, z_lo), P(r_in, z_lo + f_lo)
    p3, p4 = P(r_in, z_hi - f_hi), P(r_in + f_hi, z_hi)
    p5, p6 = P(r_out - f_hi, z_hi), P(r_out, z_hi - f_hi)
    p7, p8 = P(r_out, z_lo + f_lo), P(r_out - f_lo, z_lo)

    edges = [
        arc_or_line(p1, p2, r_in + f_lo, z_lo + f_lo, f_lo),
        BRepBuilderAPI_MakeEdge(p2, p3).Edge(),
        arc_or_line(p3, p4, r_in + f_hi, z_hi - f_hi, f_hi),
        BRepBuilderAPI_MakeEdge(p4, p5).Edge(),
        arc_or_line(p5, p6, r_out - f_hi, z_hi - f_hi, f_hi),
        BRepBuilderAPI_MakeEdge(p6, p7).Edge(),
        arc_or_line(p7, p8, r_out - f_lo, z_lo + f_lo, f_lo),
        BRepBuilderAPI_MakeEdge(p8, p1).Edge(),
    ]
    mkwire = BRepBuilderAPI_MakeWire()
    for e in edges:
        mkwire.Add(e)
    if not mkwire.IsDone():
        raise RuntimeError("wedge meridian wire construction failed")
    face = BRepBuilderAPI_MakeFace(mkwire.Wire(), True).Face()

    axis = gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
    rot = gp_Trsf()
    rot.SetRotation(axis, theta_c - theta_half)
    placed = BRepBuilderAPI_Transform(face, rot, True).Shape()
    revol = BRepPrimAPI_MakeRevol(TopoDS.Face_s(placed), axis, 2.0 * theta_half)
    if not revol.IsDone():
        raise RuntimeError("wedge revolve failed")
    return revol.Shape()


def build_cylinder_solid(cx: float, cy: float, z_lo: float, z_hi: float, radius: float) -> TopoDS_Shape:
    """A straight circular cylinder off (or on) the main axis, from `z_lo` to `z_hi`, centered
    at `(cx, cy)` — the cutter for an M7 satellite perforation (constant radius, straight,
    off-axis, so `build_revolve_solid`'s Z-axis-only revolve doesn't apply)."""
    ax2 = gp_Ax2(gp_Pnt(cx, cy, z_lo), gp_Dir(0.0, 0.0, 1.0))
    return BRepPrimAPI_MakeCylinder(ax2, radius, z_hi - z_lo).Shape()


def build_prism_solid(xy_pts, z_lo: float, z_hi: float, r_fillet_thresh: float = None,
                       bore_radius: float = None) -> TopoDS_Shape:
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
    "straight" side (18 mm chord sagitta against a 1 mm deviation gate).

    `bore_radius`, if given, is the accurately-fitted (least-squares over a whole circular
    station, not 3 raw mesh points) radius of the axis-centered main-bore arc, as already used
    for the circular cutters on either side of this prism's seam. Each arc run's own 3-point
    exact fit (`GC_MakeArcOfCircle` through raw, chord-tessellated mesh points) is noisy at the
    ~1 mm level even though the true radius is constant; a run whose whole-run circle fit lands
    within 10% of `bore_radius` is the main-bore arc, and its 3 construction points are
    re-projected onto the exact circle (origin-centered, radius `bore_radius` — the bore is
    axis-centered by construction, so the origin is shared ground truth rather than each run's
    own noisy fitted center) before building the arc edge. This removes the near-tangent radius
    mismatch between this prism's bore arc and the
    circular cutters it seams against, which otherwise leaves a knife-edge sliver volume at the
    seam that gmsh can't tet cleanly (see PROGRESS.md M5 log, `gmsh_tet` failure)."""
    wire = _prism_wire_2d(xy_pts, z_lo, r_fillet_thresh, bore_radius)
    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, z_hi - z_lo))
    if not prism.IsDone():
        raise RuntimeError("prism extrusion failed")
    return prism.Shape()


def _prism_wire_2d(xy_pts, z: float, r_fillet_thresh: float = None, bore_radius: float = None):
    """The arc+line hybrid closed wire `build_prism_solid` extrudes, factored out so
    `prism_cross_section_area` can measure what that wire actually encloses WITHOUT extruding
    it — see that function's docstring for why this exists (validating `detect_arc_runs`'s own
    classification against the raw ring's own shoelace area, MISSION §6.2 M14)."""
    pts = [p for p in xy_pts]
    if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-9:
        pts = pts[:-1]
    # Dedupe near-duplicate CONSECUTIVE points before run-classification (not just at
    # construction time): denser/differently-distributed adaptive stations can make two
    # resampled ring points collapse to (near-)coincident, which otherwise reaches
    # `detect_arc_runs` as a degenerate 1-2-point run and forces the arc/edge-construction
    # fallback below to silently DROP a wire edge (open/malformed-wire risk) instead of never
    # seeing a degenerate run in the first place. This was the root cause of a volume_err_pct
    # regression (PROGRESS.md M8 iter 47): band-aiding at edge-construction time avoided the
    # crash but left the dropped-edge geometry defect; deduping the point list up front means
    # `detect_arc_runs` never gets handed a run that can only degenerate to p0==p1.
    if len(pts) > 3:
        deduped = [pts[0]]
        for p in pts[1:]:
            if math.hypot(p[0] - deduped[-1][0], p[1] - deduped[-1][1]) > 1e-6:
                deduped.append(p)
        if len(deduped) > 1 and math.hypot(deduped[0][0] - deduped[-1][0],
                                            deduped[0][1] - deduped[-1][1]) <= 1e-6:
            deduped.pop()
        if len(deduped) >= 3:
            pts = deduped
    if len(pts) < 3:
        raise ValueError("prism cross-section needs at least 3 distinct points")

    arcs = detect_arc_runs(pts, r_fillet_thresh)

    def P(idx):
        x, y = pts[idx]
        return gp_Pnt(x, y, z)

    mkwire = BRepBuilderAPI_MakeWire()
    if not arcs:
        n = len(pts)
        for i in range(n):
            mkwire.Add(BRepBuilderAPI_MakeEdge(P(i), P((i + 1) % n)).Edge())
    else:
        n_arcs = len(arcs)
        # Precompute each run's (possibly bore_radius-snapped) construction points up front,
        # rather than re-deriving the "next run's start point" via the raw, unsnapped `P()`
        # when connecting runs: the straight bridge edge from run i's end to run i+1's start
        # MUST land on the exact same point run i+1's own arc/straight edge starts from, or the
        # wire has a sub-tolerance-scale gap there and `BRepBuilderAPI_MakeWire.IsDone()` fails.
        # (A snap can move a bore-arc's endpoint by up to ~1 mm, far past the wire builder's
        # default confusion tolerance, so this must be exact, not "close enough".)
        run_endpoints = []
        for run in arcs:
            p0, pm, p1 = P(run[0]), P(run[len(run) // 2]), P(run[-1])
            if bore_radius is not None and len(run) >= 3:
                sub = np.asarray([pts[j] for j in run])
                cx, cy, rfit, _resid, _ = fit_circle(sub)
                # Snap to the ORIGIN, not this run's own noisy fitted center: the main bore is
                # axis-centered by construction (same invariant `_axis_centered` checks
                # elsewhere), so every bore-arc run should share one common center. Snapping
                # each run to its own ~0.4-0.9 mm-off fitted center only fixed the radius, not
                # the between-run centering noise, and left adjacent arc segments/faces subtly
                # inconsistent enough to produce a degenerate sliver face (measured: introduced
                # a NaN `surface_deviation_max_mm` at the seam instead of the pre-fix 0.358 mm —
                # a regression, reverted in favor of this origin-centered snap).
                if abs(rfit - bore_radius) < 0.1 * bore_radius:
                    def _snap(pt):
                        dx, dy = pt[0], pt[1]
                        d = math.hypot(dx, dy)
                        if d < 1e-9:
                            return pt
                        s = bore_radius / d
                        return (dx * s, dy * s)
                    sx0, sy0 = _snap(pts[run[0]])
                    sxm, sym = _snap(pts[run[len(run) // 2]])
                    sx1, sy1 = _snap(pts[run[-1]])
                    p0 = gp_Pnt(sx0, sy0, z)
                    pm = gp_Pnt(sxm, sym, z)
                    p1 = gp_Pnt(sx1, sy1, z)
            run_endpoints.append((p0, pm, p1))

        for i in range(n_arcs):
            run = arcs[i]
            p0, pm, p1 = run_endpoints[i]
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
                # p0/p1 themselves can be (near-)coincident when the classifier's own window
                # straddles a run that's collapsed to a single effective point (denser adaptive
                # station sampling makes this reachable where it wasn't before) --
                # BRepBuilderAPI_MakeEdge raises on a zero-length pair, so just drop that edge
                # rather than crash the whole cross-section.
                if p0.Distance(p1) > 1e-9:
                    mkwire.Add(BRepBuilderAPI_MakeEdge(p0, p1).Edge())
            next_p0 = run_endpoints[(i + 1) % n_arcs][0]
            if p1.Distance(next_p0) > 1e-9:
                mkwire.Add(BRepBuilderAPI_MakeEdge(p1, next_p0).Edge())

    if not mkwire.IsDone():
        raise RuntimeError("prism cross-section wire construction failed")
    wire = mkwire.Wire()
    if not wire.Closed():
        raise RuntimeError("prism cross-section wire is not closed")
    return wire


def prism_cross_section_area(xy_pts, r_fillet_thresh: float = None,
                              bore_radius: float = None) -> float:
    """Area of the SAME arc+line hybrid wire `build_prism_solid` would extrude, without actually
    extruding it — a cheap validity probe (MISSION §6.2 M14).

    Why this exists: `detect_arc_runs`'s single global elbow threshold assumes local radii split
    cleanly into "one curved cluster" vs "one straight cluster". M14's 6-point star has TWO real
    fillet radii close together (tip 40 mm, valley 50 mm, ratio only 1.25) ahead of the straight
    flanks' much larger local radii — on some raw stations (measured: the ring closest to
    mid-length among 270 candidates, right after the fore severed-run transition) the elbow
    search's largest RATIO gap lands inside the noise near the tail of the sorted-radius array
    instead of at the true fillet/flank boundary, merging most of the ring (4 runs instead of
    the true 12: 6 tip + 6 valley) into a few wildly ill-conditioned "arc" runs. Each run's exact
    3-point circle (`GC_MakeArcOfCircle` through first/mid/last) then fits SOME circle through
    mismatched points spanning a fillet AND its neighboring flank — geometrically meaningless,
    and measured to bulge the star bore's enclosed area by +30% (1,930,341 mm² vs the true
    1,480,339 mm², at z≈273 on the final M14 truth) even though `detect_arc_runs` itself never
    crashes and `_pick_best_ring`'s existing tier scoring (which only checks "are there >= 2
    runs, none degenerately short") scores this exact misclassification as its BEST tier — fewer,
    longer runs looks like a *cleaner* fit by that heuristic, not a broken one.

    `_pick_best_ring` calls this on its own top-ranked candidate(s) and compares the result to
    that candidate's raw shoelace-polygon area (already computed for the constant-cross-section
    check in `_build_bore_prism_or_loft`): a real fillet's own arc-vs-chord bulge is a few
    hundred mm² per fillet (order `r^2`, r=40-50 mm here) — well under 1% of a ~1e6 mm² star —
    so a mismatch far above that means the classification, not the geometry, is at fault, and
    the next-best-scoring candidate should be tried instead."""
    wire = _prism_wire_2d(xy_pts, 0.0, r_fillet_thresh, bore_radius)
    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return float(props.Mass())


def _arc_line_wire(pts, z, arcs):
    """Build a closed wire at axial position `z` from ring points `pts`, using a precomputed
    arc/line classification `arcs` (indices into `pts`, from `detect_arc_runs`) rather than
    reclassifying. Shared low-level piece of `build_prism_solid`'s wire construction, pulled out
    so `build_ruled_loft_solid` (below) can build two wires from the SAME `arcs` split -- required
    so `BRepOffsetAPI_ThruSections` sees identical edge count/order on both sections (see that
    function's docstring)."""
    def P(idx):
        x, y = pts[idx]
        return gp_Pnt(x, y, z)

    mkwire = BRepBuilderAPI_MakeWire()
    if not arcs:
        n = len(pts)
        for i in range(n):
            mkwire.Add(BRepBuilderAPI_MakeEdge(P(i), P((i + 1) % n)).Edge())
        if not mkwire.IsDone():
            raise RuntimeError("loft ring wire construction failed")
        return mkwire.Wire()

    n_arcs = len(arcs)
    endpoints = [(P(run[0]), P(run[len(run) // 2]), P(run[-1])) for run in arcs]
    for i in range(n_arcs):
        run = arcs[i]
        p0, pm, p1 = endpoints[i]
        try:
            if len(run) < 2:
                raise ValueError("degenerate arc run")
            arc = GC_MakeArcOfCircle(p0, pm, p1).Value()
            mkwire.Add(BRepBuilderAPI_MakeEdge(arc).Edge())
        except Exception:
            mkwire.Add(BRepBuilderAPI_MakeEdge(p0, p1).Edge())
        next_p0 = endpoints[(i + 1) % n_arcs][0]
        mkwire.Add(BRepBuilderAPI_MakeEdge(p1, next_p0).Edge())
    if not mkwire.IsDone():
        raise RuntimeError("loft ring wire construction failed")
    return mkwire.Wire()


def build_ruled_loft_solid(z0, pts0, z1, pts1, r_fillet_thresh: float = None) -> TopoDS_Shape:
    """Ruled loft cutter solid between two cross-sections that are the SAME shape uniformly
    scaled (MISSION §6.2 M6: a star bore whose profile scales linearly in z, `Cylinder minus a
    ruled loft`). `pts0`/`pts1` must be point-for-point correspondents -- same count, same order,
    e.g. one reference ring's raw points scaled by two different factors -- see
    `pipeline/cli.py::_build_bore_prism_or_loft`, which is the only caller and guarantees this.

    Unlike `build_prism_solid` (one cross-section, extruded -- correct only when the bore is
    axially *constant*, M3), this builds TWO wires and lofts between them with
    `BRepOffsetAPI_ThruSections(isSolid=True, isRuled=True)`, which is exactly the construction a
    linearly-scaling profile needs: straight-line generators between corresponding boundary
    points reproduce a true ruled surface (matching the closed-form frustum-generalization volume
    MISSION.md gives for M6), not a station-density-limited polyline approximation. The arc/line
    split (`detect_arc_runs`) is computed ONCE on `pts0` and reused verbatim for `pts1`
    (`_arc_line_wire`) so both wires have identical edge topology/order -- `docs/research/04`
    flags mismatched multi-edge wires across loft sections as a twist/correspondence trap;
    `CheckCompatibility(False)` then tells OCCT to trust that correspondence instead of guessing
    its own (which risks silently reordering/splitting edges and picking the wrong vertex
    pairing -- a twisted rather than straight ruled lateral surface)."""
    pts0 = [tuple(p) for p in pts0]
    pts1 = [tuple(p) for p in pts1]
    if len(pts0) > 1 and math.hypot(pts0[0][0] - pts0[-1][0], pts0[0][1] - pts0[-1][1]) < 1e-9:
        pts0 = pts0[:-1]
    if len(pts1) > 1 and math.hypot(pts1[0][0] - pts1[-1][0], pts1[0][1] - pts1[-1][1]) < 1e-9:
        pts1 = pts1[:-1]
    if len(pts0) != len(pts1):
        raise ValueError("loft ring point counts must match (same reference ring, scaled)")

    arcs = detect_arc_runs(pts0, r_fillet_thresh)
    wire0 = _arc_line_wire(pts0, z0, arcs)
    wire1 = _arc_line_wire(pts1, z1, arcs)

    loft = BRepOffsetAPI_ThruSections(True, True)
    loft.AddWire(wire0)
    loft.AddWire(wire1)
    loft.CheckCompatibility(False)
    loft.Build()
    if not loft.IsDone():
        raise RuntimeError("ruled loft failed")
    return loft.Shape()


def _fillet_ring_wire(fillets, z: float, scale: float):
    """Closed wire at height `z` from a tangent-fillet cross-section (`fitting.fit_fillet_ring`)
    scaled uniformly about the axis by `scale`: one exact `GC_MakeArcOfCircle` per fillet, one
    straight edge per flank, in ring order. 2*len(fillets) edges total, against the ~156 a
    tolerance-driven polygon of the same cross-section needs."""
    m = len(fillets)

    def P(xy):
        return gp_Pnt(xy[0] * scale, xy[1] * scale, z)

    mkwire = BRepBuilderAPI_MakeWire()
    for i, f in enumerate(fillets):
        t1, t2 = P(f["t1"]), P(f["t2"])
        arc = GC_MakeArcOfCircle(t1, P(f["mid"]), t2).Value()
        mkwire.Add(BRepBuilderAPI_MakeEdge(arc).Edge())
        nxt = P(fillets[(i + 1) % m]["t1"])
        if t2.Distance(nxt) > 1e-9:
            mkwire.Add(BRepBuilderAPI_MakeEdge(t2, nxt).Edge())
    if not mkwire.IsDone():
        raise RuntimeError("fillet ring wire construction failed")
    return mkwire.Wire()


def build_fillet_loft_solid(z0: float, s0: float, z1: float, s1: float,
                            fillets) -> TopoDS_Shape:
    """Ruled-loft cutter between the SAME tangent-fillet cross-section scaled by `s0` at `z0` and
    by `s1` at `z1` (MISSION §6.2 M6's linearly-scaling star bore).

    This is the arc/line counterpart of `build_ruled_loft_solid`, and the reason it exists is
    `gmsh_tet`, not accuracy: a polygon wire dense enough to hold the deviation gate puts
    142 of its 154 segments below gmsh's `MeshSizeMin` floor (hmax/10 = 10 mm on M6), and each of
    those becomes a ~3 mm-wide, 10 000 mm-long ribbon face that can only be meshed with slivers
    (measured min SICN 0.0077 against a 0.1 gate, 11 % of tets under gate, spread over the whole
    bore). Exact fillet arcs collapse each fillet's ~12 ribbons into ONE face 40-250 mm wide, so
    the face width stops fighting the mesh-size floor.

    Both wires are generated from one geometry description by pure scaling, so corresponding
    edges are exact scalings of each other. That makes every line->line pair an exactly planar
    face and every arc->arc pair an exact cone -- which is precisely how the truth solid is built
    (`ThruSections(isSolid, ruled)` between two exactly x1.5-scaled star wires) -- and it removes
    the cross-layer parametrization mismatch that killed the earlier attempt to build each layer's
    curves independently from its own points (PROGRESS.md iter 41, item 5)."""
    wire0 = _fillet_ring_wire(fillets, z0, s0)
    wire1 = _fillet_ring_wire(fillets, z1, s1)
    loft = BRepOffsetAPI_ThruSections(True, True)
    loft.AddWire(wire0)
    loft.AddWire(wire1)
    loft.CheckCompatibility(False)
    loft.Build()
    if not loft.IsDone():
        raise RuntimeError("fillet ruled loft failed")
    return loft.Shape()


def build_ring_loft_solid(sections, is_ruled: bool = False) -> TopoDS_Shape:
    """Loft cutter through a chain of ACTUAL, independently-measured, seam-aligned cross-
    section rings (tapered_bore_dome_pinch_and_surface_area.md §4.3) -- the "rung 3" fallback
    for a non-circular bore/fin zone whose shape doesn't just uniformly scale (lobes growing at
    different rates, e.g. M16's two-family star). Unlike `build_ruled_loft_solid`/
    `build_fillet_loft_solid` (both point-for-point correspondences of ONE reference shape
    scaled by a scalar), every section here carries its OWN measured ring.

    `sections`: `[(z, pts_Mx2), ...]`, at least 2, every `pts` the SAME length `M`, already
    seam-aligned (`pipeline/engine.py::_prepare_loft_rings` -- FFT-anchored winding/seam per
    `docs/research/04-pipeline-design-notes.md` §3) so `BRepOffsetAPI_ThruSections` sees matched
    vertex correspondence across sections without having to guess it itself
    (`CheckCompatibility(False)` tells it to trust that correspondence, not re-derive it --
    guessing wrong on morphing sections is exactly the twist/mismatch trap research-04 §3 flags).

    One closed PERIODIC B-spline interpolation edge per section (`GeomAPI_Interpolate(...,
    PeriodicFlag=True)`), not the `_arc_line_wire` multi-edge hybrid the other loft builders use:
    `build_fillet_loft_solid`'s own docstring records the measured failure of dense multi-edge
    wires under `ThruSections` (142/154 segments below gmsh's `MeshSizeMin`, min SICN 0.0077 on
    M6), and research-04 §3 separately flags multi-edge wires across morphing sections as a
    twist/correspondence trap of their own -- a single smooth face per side steps around both at
    once, and costs the same ONE edge regardless of how many points (`M`) went into fitting it.
    The tradeoff: a fillet arc is approximated by a spline at interpolation tolerance instead of
    an exact circle -- bounded by the loft's own degree/continuity fit below, and every section
    here is already built from mesh-derived (not exact-analytic) points, so this adds no error
    class that wasn't already present in the input.

    `SetParType(Approx_ChordLength)` + `SetMaxDegree(8)` + `SetContinuity(GeomAbs_C2)`
    (research-04 §8 bonus 12): `ThruSections`'s own defaults give a C0-continuous lateral
    surface (a visible kink at every section plane) that gmsh meshes badly; raising the
    degree/continuity budget lets it fit one smooth surface through the whole chain instead.

    `is_ruled` (default False, the plan's preferred smooth surface): when True, builds
    `BRepOffsetAPI_ThruSections(True, True)` instead (straight-line generators between
    corresponding section vertices, N-1 simpler faces, no degree/continuity fit) and skips
    `SetParType`/`SetMaxDegree`/`SetContinuity` (meaningless on a ruled surface). This is the
    plan's own recorded fallback rung (§4.4/§8 risk 5), and it is a REAL fallback, not just a
    gmsh-quality tweak: measured on M16's actual (noisy, real-circle-fit) station data, the
    smooth `isRuled=False` fit produced a topologically "valid" (`BRepCheck_Analyzer` passes)
    but numerically wild self-intersecting surface -- volumes of -1.4e24 and -2.7e14 mm^3
    measured across two otherwise-similar runs of the SAME geometry class, both orders of
    magnitude away from the true ~2.9e9 mm^3 -- while `isRuled=True` on the identical section
    stack gives a valid, correctly-scaled (2.87e9 mm^3) result immediately. The caller
    (`pipeline/engine.py::_build_tapered_bore_cutter`) tries `isRuled=False` first and falls
    back to `isRuled=True` only when a volume-sanity check rejects it (`BRepCheck_Analyzer`
    alone does not catch this failure mode).

    Raises (never silently returns an invalid shape) on interpolation/loft failure or a
    `BRepCheck_Analyzer`-invalid result -- the caller treats any exception here as "this rung
    unavailable" and falls back further, matching this module's existing fallback-ladder
    convention (`_build_slot_wedges`/`_build_slot_lobes`).

    KNOWN LIMIT -- the tessellation lottery (measured 2026-09-10, M16). The surface this builds
    is accurate; OCCT's tessellation of it intermittently is not. `BRepMesh_IncrementalMesh` at
    a 0.25 mm linear deflection emits isolated triangles 1.4-14 mm away from these ruled B-spline
    faces, always at the cross-section's tightest-curvature corners (on M16, always a multiple of
    18 deg -- a star tip or valley -- never the periodic seam). It is the MESH that is wrong, not
    the geometry: slicing the built solid at the worst point's own z put its section within
    0.12 mm of the section stack it was lofted from and ~0.5 mm of truth, while the tessellation
    there was 9.86 mm out.

    It behaves as a lottery in `M` (the caller's resample count) with no usable pattern -- clean
    at M=256/320/512, catastrophic at 288/384/448/576/640 (cutter-level max 3.91/3.87/1.98/9.86/
    4.98 mm) -- and the BOOLEAN RE-ROLLS IT: M=512 gives the cleanest cutter measured (max
    0.747 mm, zero points over 1 mm) and the worst final solid (max 8.33 mm). That last fact is
    what rules out the obvious mitigation: no screen applied to the cutter can predict the
    tessellation quality of the solid it will become, so `M` cannot be chosen by any measured
    pre-boolean criterion, and no curvature/chord-error rule for `M` is safe.

    Falsified as causes, each by direct measurement on M16's own section stack, so nobody
    re-derives them: per-section chord-length knot vectors needing unification across sections
    (rebuilt with identical uniform parameters -- same lottery, different M values); the periodic
    seam (failures sit 50-165 deg away from it; rebuilding the sections closed-but-not-periodic,
    with and without a supplied closing tangent, is equal or worse); near-zero-height sliver
    sections (raising the minimum section spacing 1 mm -> 10 mm reproduces every failure to four
    decimals); and section count (RDP compression in z cannot compress at all, because the
    per-station slice noise exceeds any sane tolerance).

    The structural fix -- stop emitting B-spline faces for this shape class and emit analytic
    arcs and lines instead, the way the truth solids themselves are built (`build_fillet_loft_
    solid`), since analytic faces tessellate exactly -- is now IMPLEMENTED as rung 3's preferred
    construction: `fitting.fit_tapered_fillet_model` (the joint per-zone arc+flank estimation)
    feeding `build_tapered_fillet_loft_solid` below, wired ahead of this builder in
    `pipeline/engine.py::_build_tapered_bore_cutter` (path `"loft_arcs"`). Measured on M16, it
    took the failing star_zone p99 from 0.4125 to 0.2787 (gate 0.4, input-STL floor 0.372) and
    max deviation from 0.894 to 0.521. This B-spline builder REMAINS the honest fallback for
    any tapered zone the tangent-fillet family cannot represent (a nonlinear-in-z taper, a
    non-star cross-section, a seam needing the `bore_radius` snap) -- for those the lottery
    above is still the operative limit, which is why this note stays."""
    sections = sorted(sections, key=lambda s: s[0])
    if len(sections) < 2:
        raise ValueError("ring loft needs at least 2 sections")
    m0 = len(sections[0][1])
    if m0 < 4 or any(len(pts) != m0 for _, pts in sections):
        raise ValueError("ring loft sections must all share the same point count (seam-aligned)")

    loft = BRepOffsetAPI_ThruSections(True, bool(is_ruled))
    for z, pts in sections:
        pts = [tuple(p) for p in pts]
        if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-9:
            pts = pts[:-1]
        harray = TColgp_HArray1OfPnt(1, len(pts))
        for i, (x, y) in enumerate(pts):
            harray.SetValue(i + 1, gp_Pnt(float(x), float(y), float(z)))
        interp = GeomAPI_Interpolate(harray, True, 1e-6)
        interp.Perform()
        if not interp.IsDone():
            raise RuntimeError(f"ring loft section interpolation failed at z={z}")
        mkwire = BRepBuilderAPI_MakeWire()
        mkwire.Add(BRepBuilderAPI_MakeEdge(interp.Curve()).Edge())
        if not mkwire.IsDone():
            raise RuntimeError(f"ring loft section wire construction failed at z={z}")
        loft.AddWire(mkwire.Wire())

    loft.CheckCompatibility(False)
    if not is_ruled:
        loft.SetParType(Approx_ChordLength)
        loft.SetMaxDegree(8)
        loft.SetContinuity(GeomAbs_C2)
    loft.Build()
    if not loft.IsDone():
        raise RuntimeError("ring loft failed")
    shape = loft.Shape()
    if not BRepCheck_Analyzer(shape).IsValid():
        raise RuntimeError("ring loft produced an invalid shape")
    return shape


def build_tapered_fillet_loft_solid(z0: float, fillets0, z1: float, fillets1) -> TopoDS_Shape:
    """Ruled loft between two DIFFERENT tangent-fillet cross-sections with matched topology
    (same corner count, same ring order) -- the analytic-face rung for a non-proportionally
    tapering star bore (M16), replacing `build_ring_loft_solid`'s periodic B-spline sections
    for this shape class.

    Why this exists: the B-spline ring loft is geometrically accurate but OCCT's tessellation
    of its degree-8 periodic faces is a lottery in the resample count `M` that the boolean
    re-rolls (see `build_ring_loft_solid`'s KNOWN LIMIT note -- isolated triangles 1.4-14 mm
    off the true surface, unpredictable, unscreenable). Analytic arc/line wires under a ruled
    `ThruSections` produce low-degree faces that tessellate exactly -- and this is precisely
    the construction class the truth solids themselves use (`build_fillet_loft_solid`), whose
    tessellation sets the milestone's own noise floor.

    Unlike `build_fillet_loft_solid` (ONE cross-section scaled by two scalars), the two
    sections here are independent tangent-fillet rings (each fillet's own center/radius,
    solved by `fitting.fit_tapered_fillet_model` at each end's z), so the lateral faces are
    general ruled surfaces rather than exact cones/planes -- still degree (2 x 1), still
    tessellated exactly. The caller guarantees matched topology: both fillet lists come from
    the same model in the same ring order, every flank has real length on both wires
    (`fillets_at`'s `min_flank` refusal), so `_fillet_ring_wire` emits identical edge
    count/order on both and `CheckCompatibility(False)` is an honest instruction.

    Raises on loft failure or a `BRepCheck_Analyzer`-invalid result -- the caller treats any
    exception as "this rung unavailable" and falls back to the B-spline ring loft."""
    if len(fillets0) != len(fillets1):
        raise ValueError("tapered fillet loft needs matched corner counts")
    wire0 = _fillet_ring_wire(fillets0, z0, 1.0)
    wire1 = _fillet_ring_wire(fillets1, z1, 1.0)
    loft = BRepOffsetAPI_ThruSections(True, True)
    loft.AddWire(wire0)
    loft.AddWire(wire1)
    loft.CheckCompatibility(False)
    loft.Build()
    if not loft.IsDone():
        raise RuntimeError("tapered fillet loft failed")
    shape = loft.Shape()
    if not BRepCheck_Analyzer(shape).IsValid():
        raise RuntimeError("tapered fillet loft produced an invalid shape")
    return shape
