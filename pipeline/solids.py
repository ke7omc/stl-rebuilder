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

from OCP.gp import gp_Pnt, gp_Ax1, gp_Ax2, gp_Dir, gp_Vec, gp_Trsf
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_Transform,
)
from OCP.TopoDS import TopoDS
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol, BRepPrimAPI_MakePrism, BRepPrimAPI_MakeCylinder
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.TColgp import TColgp_HArray1OfPnt
from OCP.TopoDS import TopoDS_Shape

from pipeline.fitting import rdp, detect_arc_runs, fit_circle


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

    f_lo = max(0.0, min(f_lo, 0.5 * (z_hi - z_lo), 0.5 * (r_out - r_in)))
    f_hi = max(0.0, min(f_hi, 0.5 * (z_hi - z_lo), 0.5 * (r_out - r_in)))

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
        return gp_Pnt(x, y, z_lo)

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
                    p0 = gp_Pnt(sx0, sy0, z_lo)
                    pm = gp_Pnt(sxm, sym, z_lo)
                    p1 = gp_Pnt(sx1, sy1, z_lo)
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

    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, z_hi - z_lo))
    if not prism.IsDone():
        raise RuntimeError("prism extrusion failed")
    return prism.Shape()


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
