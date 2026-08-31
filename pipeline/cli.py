"""CLI entry point. Contract in MISSION.md §5.3.

Implements three bore paths (§5.2 step 6), selected per-part from what the station loop
actually measures, not from the milestone name: an axisymmetric revolve for a circular,
axis-centered bore chain (M1, M2); a prismatic extrude for a bore whose cross-section is
non-circular but constant along z (M3's star bore); and a "mixed" path — a circular revolve
fused with a prismatic extrude at a bisected topology-event z — for a single bore chain that
switches from circular to non-circular partway along z (M4/M5's fin-slot birth plane); and a
straight off-axis cylinder cutter per satellite chain (M7's N perforations) — matched
station-to-station by nearest (cx, cy) since satellites don't move, and independently bisected
for its own birth/death z if it doesn't span the part's full axial extent. The outer envelope
must still be a circular, axis-centered cylinder, and at most one interior loop per station may
be axis-centered (that one alone may be non-circular, e.g. M4/M5's fin slots).
"""
import argparse
import math
import sys
import traceback

import numpy as np
from OCP.BRepCheck import BRepCheck_Analyzer

from pipeline import booleans, export, fitting, io as pio, report, solids, stations, tol
from pipeline.fitting import fit_circle, fit_circle_robust
from pipeline.slicing import slice_station


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="rebuild.py")
    p.add_argument("input_stl")
    p.add_argument("--axis", default="z")
    p.add_argument("--units", default="mm", choices=("mm", "in", "m"))
    p.add_argument("--sections", type=int, default=40)
    p.add_argument("--refine-bands", default=None)
    p.add_argument("--adaptive", action="store_true")
    p.add_argument("--chord-tol", type=float, default=0.5)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--stl", default=None)
    return p.parse_args(argv)


def _axis_centered(cx: float, cy: float, R: float, chord_tol: float) -> bool:
    return (cx ** 2 + cy ** 2) ** 0.5 < 0.5 * tol.circle_max_resid(chord_tol)


def _fit_r2_quadratic(pts, at_start: bool, min_dz: float, resid_tol: float):
    """Fit R^2 as a quadratic (or linear, with only 2 points) function of z, using stations
    near the start or end of `pts`. Returns (z0, coef, window_z) for `np.polyval(coef, z - z0)`,
    where `window_z` is the z of the furthest station actually used (the validated extent of
    the dome band this quadratic represents). See `_extrapolate_end`/`_densify_dome_chords`.

    For a true ellipsoidal dome, R^2 is exactly quadratic in z across the *whole* dome band, not
    just near the tip — so a longer baseline of well-conditioned points should sharpen the fit
    (this matters because `_extrapolate_end`'s target is *outside* the station data entirely).
    But past the dome/cylinder shoulder R stops being quadratic (it settles to a constant), so
    blindly including cylinder points corrupts the curve. A single least-squares refit over the
    whole candidate list hides this: many flat cylinder points can out-vote the true-dome points
    and the *in-sample* residual stays small even though the fitted curve is now the wrong shape
    (measured: checking the residual only after refitting let the window grow to 30 points deep
    into the flat cylinder with no gain). So each candidate is validated *before* it is allowed
    to influence the fit: predict its R from the not-yet-updated curve, and only accept
    (refit) if that blind prediction is already within `resid_tol`. This is what actually stops
    growth right at the dome/cylinder boundary (verified on M2: window ends at the station
    closest to the true dome height, exactly where R saturates)."""
    ordered = pts if at_start else pts[::-1]
    candidates = [ordered[0]]
    for p in ordered[1:]:
        if abs(p[0] - candidates[-1][0]) >= min_dz:
            candidates.append(p)

    def _fit(subset):
        # Center z on the nearest station before fitting: raw z can be ~1e4 mm while the spread
        # across a few near-clustered stations is only ~1e2 mm, and np.polyfit is numerically
        # unstable on that large-offset/tiny-spread combination (measured: same shape, same
        # conditioning at both ends, but the un-centered fit at the z~9877 end returned
        # R^2 = -91000 garbage while the z~123 end happened to come out right — pure float
        # precision, not a real asymmetry).
        z0 = subset[0][0]
        zs = np.array([p[0] - z0 for p in subset], dtype=float)
        r2 = np.array([p[1] ** 2 for p in subset], dtype=float)
        deg = 2 if len(subset) >= 3 else 1
        coef = np.polyfit(zs, r2, deg)
        return z0, coef

    n_max = min(len(candidates), 30)
    best = candidates[: min(3, n_max)]
    if len(best) < 2:
        return best[0][0], np.array([0.0, best[0][1] ** 2]), best[0][0]
    z0, coef = _fit(best)
    for p in candidates[len(best):n_max]:
        if abs(_eval_r2_quadratic(z0, coef, p[0]) - p[1]) > resid_tol:
            break
        best = best + [p]
        z0, coef = _fit(best)
    return z0, coef, best[-1][0]


def _eval_r2_quadratic(z0: float, coef, z: float) -> float:
    r2 = np.polyval(coef, z - z0)
    return math.sqrt(max(0.0, r2))


def _solve_pinch_z(z0: float, coef, target_r: float, near_z: float):
    """Solve the quadratic-in-R^2 dome model for the z where R(z) == target_r (the bore
    radius, i.e. the analytic dome/bore pinch point), returning whichever of the (up to two)
    roots is nearest `near_z`. None if `coef` isn't quadratic or the model never reaches
    `target_r` (no real root).

    Why: on a clean input the raw mesh z-bound already sits right at the true pinch, so using
    it directly (the old approach) works. But on a noisy/coarse marching-cubes input (M9) the
    mesh's extreme vertex is a grid-quantization + noise artefact, not the true tip — measured
    on M9's fore dome: mesh z-bound 31.6 mm vs the true tip 53.5 mm (grid pitch 40 mm in z), which
    fed straight into `bbox_err_pct` as a 0.18% miss (gate 0.1%). The dome's own R(z) model,
    already fit from many stations, is far less sensitive to that single noisy extreme vertex —
    solving it for R(z)=bore_radius recovers the tip to within ~1 mm on M9."""
    coef = np.asarray(coef, dtype=float)
    if coef.size != 3:
        return None
    a, b, c = float(coef[0]), float(coef[1]), float(coef[2])
    if abs(a) < 1e-12:
        return None
    C = c - target_r ** 2
    disc = b * b - 4.0 * a * C
    if disc < 0.0:
        return None
    sq = math.sqrt(disc)
    t1, t2 = (-b + sq) / (2.0 * a), (-b - sq) / (2.0 * a)
    cands = [z0 + t1, z0 + t2]
    return min(cands, key=lambda z: abs(z - near_z))


def _refine_dome_model_from_vertices(mesh, z0: float, coef, z_lo: float, z_hi: float,
                                      chord_tol: float):
    """Refit the quadratic-in-R^2 dome model to the mesh's own outer-surface VERTICES inside
    [z_lo, z_hi], seeded by the station fit `(z0, coef)`. Returns the refitted `coef`.

    Why (iter 54, measured on M8): a planar section of a *tessellated* curved surface is
    systematically INSIDE it. Between two circumferential facet rings the mesh is a conical
    band, i.e. R linear in z, which under-cuts the true ellipse by up to the tessellation's
    chordal deflection; the circumferential chords under-cut it again. So every station's
    circle fit of the input STL is biased LOW, and the bias is one-signed, so averaging across
    stations cannot remove it. Measured on `harness/truth/M8.stl` in the fore dome:
    R_fit - R_true = -0.238 mm at z=171.5, -0.170 at z=244, -0.123 at z=396, -0.078 at z=479
    (max circle-fit residual 0.25-0.59 mm, so this is bias, not fit failure). The built solid
    reproduced that deficit almost exactly (-0.29 .. -0.09 mm over the same band), which with
    both meshes re-tessellated at ct/2 is what pushed `surface_deviation_p99_by_region` to
    0.714 mm in `fore_dome` against a 0.4 mm gate.

    The mesh VERTICES, by contrast, lie exactly on the true surface (verified: the truth STL's
    dome vertices are within 1e-4 mm of the analytic ellipse). Fitting R^2 vs z to them removes
    the bias entirely and — unlike a per-station correction — degrades gracefully on a noisy
    input (M9/M13), where the vertex noise is zero-mean and averages out over ~1e4 points while
    the faceting bias would not.

    The seed model is what makes vertex selection safe: only vertices within `4*chord_tol` of
    the seed surface are used, so the bore wall, the slot walls and any interior geometry at a
    different radius are excluded. `z_lo` should be the first *fitted station*, not the pinch —
    near the pinch the outer radius meets the bore radius and the band would swallow bore
    vertices. Extrapolating the refitted quadratic back to the pinch is exact anyway.

    Refuses the refit (returns the seed unchanged) when too few vertices survive selection or
    when the refit moves the surface by more than the selection band, which would mean the seed
    was too wrong for the band to have selected the right vertices in the first place.
    """
    v = np.asarray(mesh.vertices, dtype=float)
    if v.size == 0 or z_hi - z_lo <= 0.0:
        return coef
    z = v[:, 2]
    in_band = (z >= z_lo) & (z <= z_hi)
    if int(in_band.sum()) < 200:
        return coef
    zs = z[in_band]
    rs = np.hypot(v[in_band, 0], v[in_band, 1])
    band = 4.0 * chord_tol
    r_pred = np.sqrt(np.maximum(0.0, np.polyval(coef, zs - z0)))
    keep = np.abs(rs - r_pred) <= band
    if int(keep.sum()) < 200:
        return coef
    zk = zs[keep] - z0
    if float(zk.max() - zk.min()) < 1e-6:
        return coef
    try:
        new_coef = np.polyfit(zk, rs[keep] ** 2, 2)
    except Exception:
        return coef
    probe = np.linspace(z_lo, z_hi, 33) - z0
    r_old = np.sqrt(np.maximum(0.0, np.polyval(coef, probe)))
    r_new = np.sqrt(np.maximum(0.0, np.polyval(new_coef, probe)))
    if not np.all(np.isfinite(r_new)) or float(np.max(np.abs(r_new - r_old))) > band:
        return coef
    return new_coef


def _dome_shoulder_z(z0: float, coef, window_z: float, at_start: bool, next_z, next_r,
                     resid_tol: float):
    """z where the dome model stops growing — the apex of the fitted parabola in R^2,
    `z0 - b/(2a)`. Returns None when the model has no usable maximum.

    An ellipsoidal dome meets the barrel *tangentially*, so `_fit_r2_quadratic`'s residual test
    stops the validated window one station short of the shoulder (the next station is already on
    the flat cylinder and the model over-predicts it). `build_revolve_solid` then bridges that
    last gap with a straight chord, which cuts the corner: measured on M8, the fore window ended
    at z=478.7 (R=999.01) and the next station was z=542.3 (R=1000.0), so at z=500 the chord
    sits 0.66 mm inside a truth radius of exactly 1000 — the part's single worst deviation
    (0.859 mm at z=493.2). Extending the curved window to the shoulder removes it.

    Guards: the parabola must open downward, its apex must lie beyond the validated window but
    not past the next real station, and the radius it predicts there must agree with that
    station's radius to `resid_tol` (otherwise the "shoulder" is an artefact of a bad fit and
    the straight chord is the safer answer)."""
    coef = np.asarray(coef, dtype=float)
    if coef.size != 3:
        return None
    a, b, _c = float(coef[0]), float(coef[1]), float(coef[2])
    if a >= 0.0 or abs(a) < 1e-12:
        return None
    z_s = z0 - b / (2.0 * a)
    if at_start:
        if not (window_z < z_s < next_z):
            return None
    else:
        if not (next_z < z_s < window_z):
            return None
    if abs(_eval_r2_quadratic(z0, coef, z_s) - next_r) > resid_tol:
        return None
    return float(z_s)


def _extrapolate_end(pts, target_z: float, at_start: bool, min_dz: float, resid_tol: float):
    """Extrapolate R at target_z from the nearest fitted stations, quadratic in R^2 vs z.
    Returns (R, window_z) — see `_fit_r2_quadratic` for what `window_z` means.

    A flat copy of the nearest station's R (M1's approach) is only correct for a prismatic
    profile. On a curved end (M2's ellipsoidal dome) the true radius keeps changing all the way
    to the true axial extent — R does not even reach 0 there when a straight bore intersects the
    dome first (the solid pinches out where R_dome(z) == R_bore, a finite radius, not an apex).
    A plain linear (in R) secant overshoots badly there (measured ~440 mm vs a true 300 mm) —
    the region is close to a slope singularity in R(z). But for a 2:1 (or any) ellipsoidal dome,
    R(z)^2 is *exactly* quadratic in z, so a quadratic fit of R^2 vs z reproduces the true
    endpoint radius to ~1e-6 mm (measured on M2). Falls back to a 2-point linear-in-R^2 fit
    (still exact for a circular/elliptical taper) when only 2 stations are available.

    Dense end-clustering (see `stations.uniform_stations`) can put two adjacent stations only a
    fraction of a mm apart in z; feeding both into the fit makes the Vandermonde matrix nearly
    singular and the extrapolation blows up (measured: -91000 mm^2 for R^2, i.e. garbage). Pick
    points that are at least `min_dz` apart instead of the raw nearest.
    """
    z0, coef, window_z = _fit_r2_quadratic(pts, at_start, min_dz, resid_tol)
    return _eval_r2_quadratic(z0, coef, target_z), window_z


def _dome_model(outer_pts, mesh, at_start: bool, min_dz: float, resid_tol: float,
                chord_tol: float):
    """The dome meridian model for one end: the station-fitted quadratic in R^2, refined against
    the mesh vertices (`_refine_dome_model_from_vertices`) and extended to the barrel shoulder
    (`_dome_shoulder_z`) when one can be located. Returns (z0, coef, window_z, shoulder_z)."""
    z0, coef, window_z = _fit_r2_quadratic(outer_pts, at_start, min_dz, resid_tol)
    first_z = outer_pts[0][0] if at_start else outer_pts[-1][0]
    v_lo, v_hi = (first_z, window_z) if at_start else (window_z, first_z)
    if mesh is not None and v_hi > v_lo:
        coef = _refine_dome_model_from_vertices(mesh, z0, coef, v_lo, v_hi, chord_tol)
    beyond = [p for p in outer_pts if (p[0] > window_z if at_start else p[0] < window_z)]
    shoulder_z = None
    if beyond:
        nxt = beyond[0] if at_start else beyond[-1]
        shoulder_z = _dome_shoulder_z(z0, coef, window_z, at_start, nxt[0], nxt[1], resid_tol)
    return z0, coef, window_z, shoulder_z


def _densify_dome_chords(outer_pts, z_lo, z_hi, at_start: bool, min_dz: float, resid_tol: float,
                          window_z: float, n_samples: int = 60, model=None):
    """Replace the real (noisy, unevenly-spaced) circle-fit stations inside the validated dome
    window — from the pinch endpoint through `window_z`, the last station the quadratic-in-R^2
    model actually fit — with a clean, evenly-spaced resample of that same model, including the
    ~100 mm excluded-residual inset band that has no real station data at all (see
    `_fit_r2_quadratic`/`_extrapolate_end`). Returns the *replacement* point list for the window;
    callers must drop the original in-window stations rather than appending to them.

    Why replace rather than just densify the gaps between real stations: `build_revolve_solid`
    connects whatever points it's given with straight chords. A per-band volume probe on M2
    (truth STL vs result STL sliced into 24 mm z-bands) showed the dominant error was NOT the
    ~100 mm end gap — it was chord-vs-arc bias across the *real* station-to-station gaps deeper
    in the dome (up to -2.1% locally around mid-dome, where dR/dz is steepest and raw station
    spacing is 100+ mm), totaling ~75% of the whole-part volume_err_pct even though every
    station's own circle fit was accurate. Densifying every real-station gap fixed that
    (volume_err_pct 0.076% -> ~0.01%) but fed RDP an irregular mix of noisy real stations plus
    inserted model points that didn't collapse well, pushing face_count_max from ~6 to 67 (gate
    40). Since the model is already validated accurate to ~1e-6 mm in isolated tests, there is no
    remaining reason to keep the raw circle-fit stations in this window at all — a small, evenly
    spaced resample carries the same curvature information with far fewer points for RDP to
    simplify. Densifying only within `window_z` (not the whole part) matters: past the
    dome/cylinder shoulder the model isn't valid, and `_fit_r2_quadratic` already tells us
    exactly where that boundary is.

    n_samples=20 (uniform in z) was chosen over n_samples=14: at 14, surface_deviation_max_mm
    was 2.47 mm (gate 0.6), concentrated right at the pinch end where dR/dz is steepest; at 20,
    it drops to 1.31 mm at z=9965 (still short of the 0.6 mm gate, still the pinch end, but a
    real improvement) while volume_err_pct improves too (0.0756% -> well under 0.05%). Tried an
    R-uniform (equal-R-step, closed-form quadratic solve for z) variant to concentrate samples
    at the steep pinch end more cheaply than raising n_samples further; at n_samples=14 it
    regressed (volume_err_pct 0.088%, worse than n_samples=20 uniform-in-z) -- reverted.

    iter 17: tried uniform-in-*arc-length* along the (z, R) meridian curve instead of uniform-in-z
    (equal arc-length steps put more points where dR/dz is steepest, without the closed-form
    fragility that sank the uniform-in-R attempt). Result: WORSE, not better --
    surface_deviation_max_mm rose to 1.463mm and the worst point moved from the pinch tip
    (z=9965, where arc-length *did* pack more points) to z=9524.9, deep in the *middle* of the
    same window, which lost points to fund that extra density at the tip. So the chord-vs-arc
    error is not concentrated only at the tip -- it is spread fairly evenly in curvature terms
    across the whole window, and a fixed n_samples budget has no slack to redistribute without
    starving somewhere else. Reverted to uniform-in-z; the real lever is n_samples itself."""
    if model is None:
        z0, coef, _ = _fit_r2_quadratic(outer_pts, at_start, min_dz, resid_tol)
    else:
        z0, coef = model
    z_end = z_lo if at_start else z_hi
    lo, hi = (z_end, window_z) if at_start else (window_z, z_end)
    zs = np.linspace(lo, hi, n_samples)
    return [(float(z), _eval_r2_quadratic(z0, coef, z)) for z in zs]


def _hole_classification(hole_pts, chord_tol: float):
    """Classify a closed hole ring as an axis-centered circle or not, using the same test the
    station loop uses. Returns (is_circular, R)."""
    cx, cy, R, max_resid, _ = fit_circle(hole_pts)
    is_circ = max_resid <= tol.circle_max_resid(chord_tol) and _axis_centered(cx, cy, R, chord_tol)
    return is_circ, R


def _station_has_cavity_features(polys, chord_tol: float) -> bool:
    """True when this section's cavity is anything richer than a single axis-centered circular
    bore: several interior loops (M8's slots before they merge into the bore), one non-circular
    interior loop (the merged bore+slot "gear"), or several outer polygons (M12's breakthrough).
    An empty/degenerate section counts as no features so a bad slice can never widen a zone."""
    if len(polys) != 1:
        return bool(polys)
    interiors = polys[0].interiors
    if len(interiors) == 0:
        return False
    if len(interiors) > 1:
        return True
    is_circ, _ = _hole_classification(np.asarray(interiors[0].coords), chord_tol)
    return not is_circ


def _bisect_slot_zone_edge(mesh, z_present: float, z_absent: float, chord_tol: float,
                            n_iter: int = 50, min_dz: float = 1e-4) -> float:
    """Localize the z where the slot zone begins or ends — the plane where the slots are BORN or
    DIE, which is *not* the plane where they merge with the bore.

    `_bisect_topology_event` can only see a change in the classification of ONE hole, so on M8 it
    reports the merge plane instead: measured on `harness/truth/M8.stl`, z<=5849 has one circular
    R=450 bore; z in [5851, 5888] has NINE interior loops (the bore plus 8 detached slot lobes
    growing out of the r=150 end fillet); only at z>5888 do the lobes touch the bore and the
    section becomes a single non-circular "gear" ring. The expected event is the birth at 5850,
    38 mm fore of the merge. Bisecting on `_station_has_cavity_features` instead brackets the
    birth/death directly. `z_present` is a station known to have features, `z_absent` one known
    not to (order doesn't matter)."""
    z_a, z_b = z_present, z_absent
    for _ in range(n_iter):
        if abs(z_b - z_a) <= min_dz:
            break
        zm = 0.5 * (z_a + z_b)
        polys, _zz = slice_station(mesh, zm, chord_tol)
        if _station_has_cavity_features(polys, chord_tol):
            z_a = zm
        else:
            z_b = zm
    return 0.5 * (z_a + z_b)


def _bisect_topology_event(mesh, z_a: float, z_b: float, chord_tol: float, circ_at_a: bool,
                            n_iter: int = 50, min_dz: float = 1e-4) -> float:
    """Localize the z where a single bore hole's cross-section switches between circular and
    non-circular (M4/M5's fin-slot birth plane, MISSION §6's `topo_event_z` gate): `z_a` is a
    station known to be `circ_at_a`, `z_b` is known to be `not circ_at_a` (order doesn't
    matter). Bisects by re-slicing the mesh at the midpoint each step; a degenerate/ambiguous
    midpoint slice (e.g. landing exactly on the flat fin-root wall) is treated as still matching
    the `z_a` side so the bracket keeps shrinking rather than stalling. Returns the midpoint of
    the final bracket, accurate to `min_dz`."""
    for _ in range(n_iter):
        if abs(z_b - z_a) <= min_dz:
            break
        zm = 0.5 * (z_a + z_b)
        polys, zz = slice_station(mesh, zm, chord_tol)
        is_circ = circ_at_a
        if len(polys) == 1 and len(polys[0].interiors) == 1:
            hole = np.asarray(polys[0].interiors[0].coords)
            is_circ, _ = _hole_classification(hole, chord_tol)
        if is_circ == circ_at_a:
            z_a = zm
        else:
            z_b = zm
    return 0.5 * (z_a + z_b)


def _bisect_hole_edge(mesh, z_present: float, z_absent: float, chord_tol: float,
                       cx0: float, cy0: float, r0: float, n_iter: int = 50,
                       min_dz: float = 1e-4) -> float:
    """Localize the z where one specific satellite hole (M7's N perforations) starts or stops
    existing — `z_present` is a station known to have a hole near `(cx0, cy0)`, `z_absent` is a
    station known not to (order doesn't matter). Mirrors `_bisect_topology_event` but matches by
    proximity to this chain's own center/radius instead of circular-vs-non-circular, since
    several holes with the same classification can coexist in one station."""
    match_dist = 2.0 * r0
    z_a, z_b = z_present, z_absent
    for _ in range(n_iter):
        if abs(z_b - z_a) <= min_dz:
            break
        zm = 0.5 * (z_a + z_b)
        polys, zz = slice_station(mesh, zm, chord_tol)
        present = False
        if len(polys) == 1:
            for ring in polys[0].interiors:
                pts = np.asarray(ring.coords)
                cx, cy, R, resid, _ = fit_circle(pts)
                if resid <= tol.circle_max_resid(chord_tol) and \
                        math.hypot(cx - cx0, cy - cy0) < match_dist:
                    present = True
                    break
        if present:
            z_a = zm
        else:
            z_b = zm
    return 0.5 * (z_a + z_b)


def _bisect_ring_edge(mesh, z_present: float, z_absent: float, chord_tol: float,
                       cx0: float, cy0: float, match_dist: float, n_iter: int = 50,
                       min_dz: float = 1e-4) -> float:
    """Localize the z where one specific satellite SLOT (M8's obround perforations) starts or
    stops existing — mirrors `_bisect_hole_edge`, but matches ANY interior ring (circular or not)
    by its raw-point centroid instead of requiring a circle-fit to pass the circularity gate,
    since the hole being tracked is non-circular by construction."""
    z_a, z_b = z_present, z_absent
    for _ in range(n_iter):
        if abs(z_b - z_a) <= min_dz:
            break
        zm = 0.5 * (z_a + z_b)
        polys, zz = slice_station(mesh, zm, chord_tol)
        present = False
        if len(polys) == 1:
            for ring in polys[0].interiors:
                pts = np.asarray(ring.coords)
                cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
                if math.hypot(cx - cx0, cy - cy0) < match_dist:
                    present = True
                    break
        if present:
            z_a = zm
        else:
            z_b = zm
    return 0.5 * (z_a + z_b)


def _pick_best_ring(bore_rings, z_center: float):
    """Pick the best-conditioned representative cross-section from `bore_rings` (raw ring points).

    Used for a non-circular but axially-constant bore (e.g. M3's star):
    take the station closest to mid-length as the representative cross-section (least likely to
    be distorted by any inset/end effects) and hand its raw ring points straight to
    `solids.build_prism_solid`, which detects fillet-arc runs vs straight runs itself and builds
    an exact arc+line hybrid wire (see that function's docstring for why raw points, not a
    Douglas-Peucker-simplified ring, are wanted here: simplification would erase the very
    curvature signal `detect_arc_runs` needs, and straight-run collapsing already keeps the face
    count low without it). Extrudes past `z_min` by `eps_start` and past `z_max` by `eps_end_val`
    (independent, since a topology-event seam (M4/M5) wants only a small fuse-robustness overlap
    on that side, not the full `eps_cut` margin a true outer end needs for a robust boolean cut
    against the envelope). `bore_radius`, if given, is forwarded to `build_prism_solid` to snap
    this ring's own main-bore arc onto the same accurately-fitted radius the circular cutter on
    the other side of the seam uses (see that function's docstring for why).

    The representative station used to be a blind `bore_rings[len(bore_rings) // 2]` (literal
    mid-index of whatever list was passed). With adaptive station placement that index can land
    on a poorly-conditioned raw sample: `detect_arc_runs`'s local circle fit occasionally reads
    noise around the WHOLE ring as alternating tiny curved/straight runs (M5 iter-49 regression:
    a 480-point mid station built 481 faces vs. 34 for a different station with the SAME point
    count, because that particular sampling fragmented into many near-degenerate short runs
    instead of the true handful of long ones) -- `face_count_max` then fails on pure mesh-
    conditioning noise, not a real shape difference. Fix: score every candidate ring in
    `bore_rings` by `detect_arc_runs`'s own output (fewer/longer runs = fewer final wire edges =
    better-conditioned) and pick the best one, rather than trusting whichever index happens to be
    the middle of the list. `bore_rings` is the SAME list object at every call site regardless of
    which z-window is being built (M4/M5's fore/aft seams, M8's per-event bands all pass the
    identical full-part list) and this scoring is deterministic, so every call picks the SAME
    winning ring -- this is actually a stronger consistency guarantee than the old "always index
    len//2" rule (which already implicitly assumed one shared representative station), and
    unlike the per-call best-of-window search tried and reverted for M8 (PROGRESS.md iter 48), it
    cannot introduce a different pick for two adjacent segments of the same constant-cross-
    section bore, so it does not reintroduce the cross-segment seam/phase mismatch that broke
    `BRepCheck_Analyzer` there.

    Scoring is 3-tiered, not a plain "fewest edges" minimum: a first attempt (minimize
    `2*n_runs` alone) picked a station whose classifier DID fragment into a spurious length-1
    run (min run length 1) purely because it had one fewer run overall than the genuinely clean
    candidate (30 edges vs. 32) -- "fewest edges" rewards exactly the kind of degenerate run
    `build_prism_solid`'s own GC_MakeArcOfCircle-failure fallback silently drops geometry for,
    which regressed `volume_err_pct` to 4.78% even though `face_count_max` was fixed. A second
    attempt (tiering only on "has any degenerate run") still regressed M3 (0.1627% vs gate 0.1%):
    a handful of near-pinch-end stations classified almost the ENTIRE 300-point ring as ONE giant
    arc (n_runs=1, min_run=281) -- technically no *short* degenerate run, but just as wrong a
    read as the zero-run case, and its `2*n_runs=2` "edge count" looks artificially best of all.
    Final tiering: 0 (>=2 runs found, none degenerate, i.e. min run length >= 3) beats 1 (>=2 runs
    found but at least one 1-2-point degenerate span) beats 2 (`detect_arc_runs` found NO
    curvature, or collapsed the whole ring into a single run -- both are a total
    misclassification for a shape with real fillets, not a legitimate simplification). Within a
    tier, prefer fewer wire edges, then break remaining ties by distance to the middle of this
    call's own z-window (least likely to be distorted by inset/end effects, same reasoning the
    old blind mid-index pick relied on)."""

    def _score(z, pts) -> tuple:
        pts = list(pts)
        if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-9:
            pts = pts[:-1]
        if len(pts) < 3:
            return (3, 10 ** 9, abs(z - z_center))
        runs = fitting.detect_arc_runs(pts, None)
        if not runs or len(runs) < 2:
            return (2, len(pts), abs(z - z_center))
        min_run = min(len(r) for r in runs)
        tier = 0 if min_run >= 3 else 1
        return (tier, 2 * len(runs), abs(z - z_center))

    best_pts, best_score = None, None
    for z, ring in bore_rings:
        pts = list(ring.coords)
        s = _score(z, pts)
        if best_score is None or s < best_score:
            best_score, best_pts = s, pts
    if best_pts is None:
        best_pts = list(bore_rings[len(bore_rings) // 2][1].coords)
    return best_pts


def _build_prism_bore(bore_rings, z_min: float, z_max: float, eps_start: float,
                       eps_end_val: float, chord_tol: float, bore_radius: float = None):
    """Prism cutter for the whole (bore + merged slots) cross-section — see `_pick_best_ring`."""
    return solids.build_prism_solid(_pick_best_ring(bore_rings, 0.5 * (z_min + z_max)),
                                     z_min - eps_start, z_max + eps_end_val,
                                     bore_radius=bore_radius)


def _prism_from_ring(coords, z_lo: float, z_hi: float, area: float, arc_radius: float = None):
    """`build_prism_solid`, retried from different start vertices, scored against `area`.

    A prism's volume must be its cross-section's area times its height, so `area * (z_hi - z_lo)`
    is an exact acceptance test — and `build_prism_solid` fails it in BOTH directions on a slot
    lobe cut out of a merged ring. The disc subtraction leaves short edges where the flanks meet
    the split arc, and the arc-run detection's behaviour there depends on WHERE in the ring it
    starts: on M5 one of 8 congruent lobes collapsed to V=3.4e-11 (the other seven 1.07e8), and
    on M8 all 8 lobes have area 116305.2 (=> 4.342e8) yet built at 4.255e8 .. 5.379e8, the
    over-fitted arcs bulging outward and over-cutting by 1.1e8 in total.

    Rolling the ring is exact — same polygon, same points, only a different seam. The two obvious
    alternatives are not: deduping the short edges starves the arc fitter (all eight M5 lobes
    collapse), and Douglas-Peucker at 0.05*chord_tol cuts every lobe from 1.10e8 to 5.5e6.
    """
    ring = list(coords)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    n = len(ring)
    target = area * (z_hi - z_lo)
    if target <= 0.0:
        return None
    best = None
    seen = set()
    for num in range(8):
        k = (num * n) // 8
        if k in seen:
            continue
        seen.add(k)
        rolled = ring[k:] + ring[:k]
        solid = solids.build_prism_solid(rolled + [rolled[0]], z_lo, z_hi,
                                          bore_radius=arc_radius)
        err = abs(_solid_volume(solid) - target) / target
        # Keep this TIGHT. Relaxing it to 1 % (which would be only ~0.03 % of total volume, and
        # is tempting because chording every lobe is what costs `gmsh_tet` — M5 min SICN 0.0816
        # against a 0.1 gate) was measured and is much worse: M5 0.9898 -> 0.6722, because an arc
        # fit can be within 0.81 % on volume and still 6.06 mm out of place. Volume alone does
        # not validate an arc; it only detects a grossly wrong one. Re-measured after the
        # `fitting.detect_arc_runs` `min_side`-elbow fix (this iteration): still true, 5% still
        # gives 6.065 mm at z=7412.9 — a better-conditioned elbow does not make volume-only
        # acceptance safe. See PROGRESS.md for the arc-corrected-target idea that should replace
        # this raw-polygon-area comparison instead.
        if err <= 1.0e-3:
            return solid
        if best is None or err < best[0]:
            best = (err, solid)
    # No seam gave the right volume, so the arc fitting itself is wrong on this outline (some
    # runs come back as the MAJOR arc: on M8 one lobe over-built by 24 %, two by ~4 %). Last
    # rung: an `r_fillet_thresh` below any real local radius makes `detect_arc_runs` find nothing
    # and `build_prism_solid` fall back to a straight-edge polygon, whose volume is exactly
    # `area * height` by construction. Chords cost at most the slicer's own `chord_tol`, and only
    # on lobes that are already wrong by far more than that.
    solid = solids.build_prism_solid(ring + [ring[0]], z_lo, z_hi, r_fillet_thresh=1.0e-9)
    err = abs(_solid_volume(solid) - target) / target
    if best is None or err < best[0]:
        best = (err, solid)
    return best[1] if best[0] < 0.5 else None


def _solid_volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def _build_slot_lobes(bore_rings, z_lo: float, z_hi: float, bore_radius: float,
                       chord_tol: float):
    """Cavity decomposition (MISSION.md §5.5 item 3) of a merged bore+slot cross-section.

    The M5/M8 "sandwich" bore is circular fore of `z_lo`, a merged bore+slot ring through
    [`z_lo`, `z_hi`], and circular again aft of `z_hi`. Round 1 modelled that as three cutter
    solids fused into one tool, and that fuse is unfixable in principle: the merged ring's own
    main-bore arc and the circular cutter's cylinder are two DISTINCT surfaces separated by far
    less than the boolean's fuzzy value (0.011 mm apart at the seam radius, and the ring's
    straight bridges between detected arc runs dip a further ~0.22 mm inside its own snapped
    arcs), which is the one configuration BOPAlgo cannot imprint. Every tolerance-side remedy
    was measured and rejected (PROGRESS.md iters 48-51 and `## Do not retry`): the surviving
    defect was always exactly ONE face of the fused tool carrying
    `BRepCheck_BadOrientationOfSubshape` — the clearance cone, crossing the prism's bore
    boundary — which then made `BRepAlgoAPI_Cut` emit 57/62 faces at `TopAbs_INTERNAL` and the
    STEP writer silently drop them.

    Decompose instead: the bore is ONE full-length circular revolve (exact, no seam anywhere),
    and each slot becomes its own prism cutter. Every remaining cutter/target surface pair then
    meets transversally: the lobes' flanks cross the bore cylinder at a large angle, and the part
    of each lobe inside the cylinder is a geometric no-op because the bore cutter already removed
    it. All offsets here are scale-relative (MISSION.md §2 rule 7).

    Returns [] when the ring does not decompose into lobes, so the caller can fall back to the
    fused path.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf
    from shapely.affinity import rotate, translate
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union

    pts = _pick_best_ring(bore_rings, 0.5 * (z_lo + z_hi))
    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    # The splitting disc must be strictly LARGER than the main bore, or the ring does not
    # separate at all: subtracting a smaller disc leaves ONE polygon with an interior ring whose
    # exterior is just the original merged outline again (measured, iter 51).
    split_r = bore_radius + 4.0 * chord_tol
    # 512-segment disc: 0.008 mm chordal error at R=450, far inside `chord_tol`.
    disc = Point(0.0, 0.0).buffer(split_r, quad_segs=128)
    a_min = tol.a_min(chord_tol)
    lobes = [g for g in getattr(poly.difference(disc), "geoms", [poly.difference(disc)])
             if g.geom_type == "Polygon" and g.area > a_min]
    if not lobes:
        return []
    # Each lobe now stops `split_r - bore_radius` short of the bore cylinder. Close that gap by
    # translating a copy of the lobe inward along its OWN centreline and unioning: translation
    # preserves the slot's flank spacing exactly, where a radial scale would narrow it.
    reach = (split_r - bore_radius) + tol.eps_cut(chord_tol)
    axis = gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
    built = []
    for g in lobes:
        c = g.centroid
        n = math.hypot(c.x, c.y)
        if n <= 0.0:
            continue
        g = unary_union([g, translate(g, -reach * c.x / n, -reach * c.y / n)])
        if g.geom_type != "Polygon":
            g = max(g.geoms, key=lambda p: p.area)
        # Build every lobe in the same canonical orientation (centroid on +x) and rotate the
        # solid back, so `build_prism_solid`'s angle-sorted arc fitting never sees a lobe
        # straddling the atan2 branch cut at +-pi.
        theta = math.atan2(c.y, c.x)
        g = rotate(g, -theta, origin=(0.0, 0.0), use_radians=True)
        # The slot's outer boundary is an axis-centred arc, so give `build_prism_solid` an
        # accurate radius for it (averaged over the outermost band) the same way the M4/M5 bore
        # prism is given its fitted bore radius. Without it, each lobe's outer run gets a noisy
        # 3-point arc fit that bulges outward: on M8 all 8 lobes have area 116305.2 (=> V
        # 4.342e8) but built at 4.255e8 .. 5.379e8, over-cutting by 1.1e8 in total.
        ring_xy = np.asarray(g.exterior.coords, dtype=float)
        rad = np.hypot(ring_xy[:, 0], ring_xy[:, 1])
        outer = rad[rad > rad.max() - 2.0 * chord_tol]
        built.append((theta, g.area,
                      _prism_from_ring(list(g.exterior.coords), z_lo, z_hi, g.area,
                                        float(outer.mean()))))
    # Last resort for a lobe no ring-roll could build: reuse a CONGRUENT sibling's canonical
    # solid. Dropping the lobe instead costs a whole slot (+0.46 % volume on M5, gate 0.2 %),
    # which is far worse than the sibling's small angular misregistration.
    good = [(a, s) for _, a, s in built if s is not None]
    out = []
    for theta, area, solid in built:
        if solid is None:
            twin = [s for ga, s in good if abs(ga - area) <= 1e-6 * max(area, 1.0)]
            if not twin:
                continue
            solid = twin[0]
        trsf = gp_Trsf()
        trsf.SetRotation(axis, theta)
        out.append(BRepBuilderAPI_Transform(solid, trsf, True).Shape())
    return out


def _fuse_sandwich_bore(bore_rings, pts_before, pts_after, z_min: float, z_max: float,
                         event_fore: float, event_aft: float, circ_overlap: float,
                         fin_overlap: float, seam_eps: float, eps_cut_val: float,
                         seam_bore_radius: float, chord_tol: float,
                         bore_seam_clearance: float):
    """Round 1's three-cutter tool for the M5/M8 sandwich bore: circular revolve fore of
    `event_fore`, the merged bore+slot ring as one prism through the slot zone, circular revolve
    aft of `event_aft`, fused into a single cutter.

    `bore_seam_clearance` drops the circular cutters' radius at the overlap end (a taper from the
    last real point, never a step — a step puts a real feature at the seam plane that the prism
    does not mask). It costs no geometry: the band it applies to lies inside the prism's own span,
    where the prism already removes strictly more material than the circle could. It is not free
    either: the taper perturbs `build_revolve_solid`'s RDP simplification of the whole meridian,
    and it separates the two near-coincident bore surfaces enough that BOPAlgo leaves a knife-edge
    sliver at the seam instead of imprinting cleanly — measured on M5, clearance `4*seam_eps`
    gives gmsh min SICN 0.0062 against a 0.1 gate where clearance 0 gives 0.291. So the caller
    tries 0 first and only pays the clearance when the fuse is otherwise invalid.

    The caller must check the result with `BRepCheck_Analyzer` — see the call site.
    """
    circ_fore_full = [(z_min - eps_cut_val, pts_before[0][1])] + pts_before \
        + [(event_fore + circ_overlap, pts_before[-1][1] - bore_seam_clearance)]
    circ_aft_full = [(event_aft - circ_overlap, pts_after[0][1] - bore_seam_clearance)] \
        + pts_after + [(z_max + eps_cut_val, pts_after[-1][1])]
    fin_solid = _build_prism_bore(bore_rings, event_fore, event_aft, fin_overlap, fin_overlap,
                                   chord_tol, bore_radius=seam_bore_radius)
    circ_fore_solid = solids.build_revolve_solid(circ_fore_full, chord_tol)
    circ_aft_solid = solids.build_revolve_solid(circ_aft_full, chord_tol)
    fused = booleans.fuse(circ_fore_solid, fin_solid, seam_eps)
    return booleans.fuse(fused, circ_aft_solid, seam_eps)


def _sector_of_ring(xy):
    """(theta_c, theta_half, r_lo, r_hi) of a ring, read as an annular sector.

    A true annular sector is bounded by two radial planes, so its angular span is exactly the
    full spread of its boundary angles and its centre is the midpoint of that spread. Deriving
    `theta_half` from the spread of the *mean* angle instead is biased in both directions (the
    mean is pulled toward whichever arc carries more points): measured on M8, +0.013 rad at the
    detached end sections and -0.045 rad on the disc-split ones, i.e. several percent of slot
    volume either way.
    """
    xy = np.asarray(xy, dtype=float)
    r = np.hypot(xy[:, 0], xy[:, 1])
    th = np.arctan2(xy[:, 1], xy[:, 0])
    mean = math.atan2(float(np.sin(th).mean()), float(np.cos(th).mean()))
    d = (th - mean + math.pi) % (2.0 * math.pi) - math.pi
    lo, hi = float(d.min()), float(d.max())
    return mean + 0.5 * (lo + hi), 0.5 * (hi - lo), float(r.min()), float(r.max())


def _lobe_sector_samples(bore_rings, sat_rings, bore_radius: float, chord_tol: float):
    """Per-station annular-sector samples of every slot lobe, from BOTH representations.

    In the slot zone's interior the lobes have merged with the central bore into one ring
    (`bore_rings`) and are recovered by subtracting a disc slightly larger than the bore; in the
    end windows the lobes are still detached and arrive as separate off-axis holes
    (`sat_rings`). Returns [(z, [(theta_c, theta_half, r_lo, r_hi, clipped), ...]), ...] sorted
    by z, where `clipped` marks a lobe whose inner radius is the splitting disc, not real
    geometry.
    """
    from shapely.geometry import Point, Polygon

    split_r = bore_radius + 4.0 * chord_tol
    disc = Point(0.0, 0.0).buffer(split_r, quad_segs=128)
    a_min = tol.a_min(chord_tol)
    by_z = {}
    for z, ring in bore_rings:
        poly = Polygon(np.asarray(ring.coords))
        if not poly.is_valid:
            poly = poly.buffer(0)
        diff = poly.difference(disc)
        for g in getattr(diff, "geoms", [diff]):
            if g.geom_type != "Polygon" or g.area <= a_min:
                continue
            tc, th, rlo, rhi = _sector_of_ring(np.asarray(g.exterior.coords))
            by_z.setdefault(z, []).append((tc, th, rlo, rhi, True, float(g.area)))
    for z, _cx, _cy, _R, hole in sat_rings:
        tc, th, rlo, rhi = _sector_of_ring(hole)
        by_z.setdefault(z, []).append(
            (tc, th, rlo, rhi, rlo <= split_r + 2.0 * chord_tol, _ring_area(hole)))
    return sorted(by_z.items())


def _fit_end_fillet(samples, z_edge: float, sign: float, r_plateau: float, outward: bool,
                     span: float):
    """Least-squares fillet radius f for r(z) = r_plateau -+ (f - sqrt(f^2 - (f - s)^2)),
    s = sign*(z - z_edge) the distance into the slot from the end plane at `z_edge`.

    That is the exact profile of a corner rounded by radius f: tangent to the end plane at
    s = 0 and tangent to the plateau at s = f. One parameter, so a handful of stations in the
    window determine it -- which is why this works at all inside `n_stations_max`, where a
    station-by-station loft of the same window would not.
    """
    if len(samples) < 2:
        return None
    zs = np.array([s[0] for s in samples], dtype=float)
    rs = np.array([s[1] for s in samples], dtype=float)
    s_in = sign * (zs - z_edge)

    def resid(f):
        d = np.clip(f - s_in, 0.0, f)
        drop = f - np.sqrt(np.maximum(f * f - d * d, 0.0))
        pred = r_plateau + (drop if not outward else -drop)
        return pred - rs

    grid = np.linspace(2.0, span, 240)
    errs = [float(np.sum(resid(f) ** 2)) for f in grid]
    f0 = float(grid[int(np.argmin(errs))])
    lo, hi = max(1.0, f0 - 2.0 * (grid[1] - grid[0])), min(span, f0 + 2.0 * (grid[1] - grid[0]))
    for _ in range(60):
        m1, m2 = lo + (hi - lo) / 3.0, hi - (hi - lo) / 3.0
        if float(np.sum(resid(m1) ** 2)) < float(np.sum(resid(m2) ** 2)):
            hi = m2
        else:
            lo = m1
    f = 0.5 * (lo + hi)
    return f, float(np.max(np.abs(resid(f))))


def _fillet_vertex_samples(mesh, z_edge: float, sign: float, r_out: float, f_seed: float,
                            thetas, theta_half: float, chord_tol: float):
    """(z, r) samples taken from the mesh's own VERTICES on a slot's outer end-fillet surface,
    for `_fit_end_fillet` to re-fit the radius on.

    Same bias as the dome (`_refine_dome_model_from_vertices`): the section-derived samples
    `_build_slot_wedges` fits are each the max radius of a *sliced* lobe outline, and a plane
    section of a tessellated torus runs along facet chords that lie inside it, so every sample
    is low by up to the STL's chordal deflection. One-parameter fits amplify that into the
    radius. Measured on M8's aft end: the section fit returned f_hi ~= 151.2 mm for a true
    150 mm fillet, putting the built surface 0.41 mm inside truth at z=9575 and 0.55 mm at
    z=9600 -- which is the whole of the residual `aft_wall` / `aft_dome` / `slot_zone`
    deviation (p99 0.45/0.43/0.40 mm against a 0.4 mm gate) once the dome is fixed. Mesh
    vertices sit exactly on the surface, so re-fitting on them removes the bias.

    Vertices are kept only when they are inside a lobe's angular sector (and off its flanks, so
    the corner blends are excluded), inside the fillet's own axial band, and within
    `4*chord_tol` of the seed fillet surface -- so bore walls, flanks and the end wall cannot
    contaminate the fit.
    """
    v = np.asarray(mesh.vertices, dtype=float)
    if v.size == 0 or f_seed <= 0.0:
        return []
    z = v[:, 2]
    s_in = sign * (z - z_edge)
    sel = (s_in > 0.08 * f_seed) & (s_in < 0.92 * f_seed)
    if int(sel.sum()) < 50:
        return []
    th = np.arctan2(v[sel, 1], v[sel, 0])
    in_sector = np.zeros(int(sel.sum()), dtype=bool)
    for tc in thetas:
        dth = (th - tc + math.pi) % (2.0 * math.pi) - math.pi
        in_sector |= np.abs(dth) < 0.6 * theta_half
    r = np.hypot(v[sel, 0], v[sel, 1])
    s_k = s_in[sel]
    d = f_seed - s_k
    r_pred = r_out - f_seed + np.sqrt(np.maximum(f_seed * f_seed - d * d, 0.0))
    keep = in_sector & (np.abs(r - r_pred) <= 4.0 * chord_tol)
    if int(keep.sum()) < 50:
        return []
    return list(zip(z[sel][keep].tolist(), r[keep].tolist()))


def _build_slot_wedges(bore_rings, sat_rings, z_lo: float, z_hi: float, bore_radius: float,
                        chord_tol: float, mesh=None):
    """Slot cutters as filleted angular wedges spanning the FULL slot zone [z_lo, z_hi].

    `_build_slot_lobes` models each slot as a constant-cross-section prism between the two
    stations that bracket the zone, which leaves the tapering fillet window at each end
    unmodelled: on M8 that is the entire `surface_deviation_max_mm` failure (42.7 mm at
    z=9621, against a 1.0 mm gate; every other region is already inside gate at <= 0.86 mm).
    Extending the prism to the true zone edges is worse, not better -- it sweeps the full-size
    cross-section across the taper and costs ~0.45 % volume against a 0.2 % gate.

    Measured on M8 (`out/dbg/wedge_probe.py`): at every z in the zone there are 8 lobes of
    constant angular half-width 0.2199 rad, and the radial extent follows
    r_out(z) = 850 - 150 + sqrt(150^2 - (150 - d)^2), r_in(z) = 400 + 150 - sqrt(...), d the
    distance from the zone edge -- i.e. a meridian rectangle with all four corners rounded at
    150 mm, revolved through a limited angle. Fitting that (one radius per end, plus the two
    plateau radii) reproduces the window to the STL's own tessellation error using only the
    stations that are already there.

    Returns [] when the samples do not fit that model, so the caller falls back to the prism.
    """
    samples = _lobe_sector_samples(bore_rings, sat_rings, bore_radius, chord_tol)
    if len(samples) < 4:
        return []
    counts = [len(v) for _z, v in samples]
    n_lobes = max(set(counts), key=counts.count)
    if n_lobes < 1 or any(c != n_lobes for c in counts):
        return []

    zone = z_hi - z_lo
    z_mid_lo, z_mid_hi = z_lo + 0.35 * zone, z_hi - 0.35 * zone
    plateau = [(z, v) for z, v in samples if z_mid_lo <= z <= z_mid_hi]
    if not plateau:
        return []
    r_out = float(np.median([lb[3] for _z, v in plateau for lb in v]))
    inner_clean = [lb[2] for _z, v in plateau for lb in v if not lb[4]]
    theta_half = float(np.median([lb[1] for _z, v in samples for lb in v]))
    if not (0.0 < theta_half < math.pi / n_lobes):
        return []

    # Acceptance test: an annular sector's area is theta_half*(r_out^2 - r_in^2). This is what
    # keeps the wedge path off shapes that merely *look* like slots -- M5's fins are constant-
    # CARTESIAN-width rectangles, whose angular span is set by their inner corners, so the sector
    # model over-states their area by ~65 % and they fall back to the prism path as before.
    for _z, v in plateau:
        for tc_, th_, rlo_, rhi_, _clip, area_ in v:
            pred = th_ * (rhi_ * rhi_ - rlo_ * rlo_)
            if area_ <= 0.0 or abs(pred - area_) > 0.03 * area_:
                return []

    # Angular positions: cluster every station's lobes onto the plateau station's angles.
    ref = sorted(lb[0] for lb in plateau[len(plateau) // 2][1])
    acc = [[] for _ in ref]
    for _z, v in samples:
        for lb in v:
            k = int(np.argmin([abs((lb[0] - t + math.pi) % (2.0 * math.pi) - math.pi)
                               for t in ref]))
            acc[k].append((lb[0] - ref[k] + math.pi) % (2.0 * math.pi) - math.pi)
    thetas = [ref[k] + float(np.mean(a)) if a else ref[k] for k, a in enumerate(acc)]

    # Outer profile: never clipped, so it carries the fillet fit at both ends.
    span = 0.45 * zone
    out_fore = [(z, lb[3]) for z, v in samples for lb in v if z < z_lo + span]
    out_aft = [(z, lb[3]) for z, v in samples for lb in v if z > z_hi - span]
    fit_lo = _fit_end_fillet(out_fore, z_lo, +1.0, r_out, True, span)
    fit_hi = _fit_end_fillet(out_aft, z_hi, -1.0, r_out, True, span)
    if fit_lo is None or fit_hi is None:
        return []
    f_lo, res_lo = fit_lo
    f_hi, res_hi = fit_hi
    # The STL's own chordal error is largest exactly where the fillet runs tangent to the end
    # plane, so allow a few chord_tol before rejecting the model.
    if max(res_lo, res_hi) > 6.0 * chord_tol:
        return []
    # Re-fit each radius on mesh vertices, which carry no section bias when the mesh is a clean,
    # fine tessellation (`_fillet_vertex_samples`) -- but on a noisy/coarse marching-cubes input
    # (M9: anisotropic 10x10x40mm grid, sigma=0.5mm noise) the vertices themselves are off the
    # true surface by more than the section-derived samples are, and re-fitting on them makes
    # the radius *worse*, not better (measured on M9: section fit residual 1.4-2.4mm vs the
    # vertex re-fit's own residual 3.7-3.8mm against the SAME circular-arc model -- the vertex
    # fit is a worse fit to its own assumed law, not a cleaner one). Rather than branch on
    # milestone/chord_tol (not available here, and wouldn't generalise to a future noisy input
    # with a different grid), accept the vertex re-fit only when it is at least as self-
    # consistent as the section fit it would replace -- measured on M8 the vertex fit residual
    # is 0.0007mm vs the section fit's 0.2-0.5mm (300x tighter), so this keeps M8's fix intact
    # while rejecting the M9 case that regressed it (0.5315% -> 0.334% volume_err_pct just from
    # this rejection, chord_tol=5 M9 case).
    if mesh is not None:
        for z_edge, sign, f_seed, res_seed, set_lo in (
                (z_lo, +1.0, f_lo, res_lo, True), (z_hi, -1.0, f_hi, res_hi, False)):
            vs = _fillet_vertex_samples(mesh, z_edge, sign, r_out, f_seed, thetas, theta_half,
                                        chord_tol)
            fit = _fit_end_fillet(vs, z_edge, sign, r_out, True, span) if vs else None
            if fit is None or fit[1] > res_seed or abs(fit[0] - f_seed) > 0.25 * f_seed:
                continue
            if set_lo:
                f_lo = fit[0]
            else:
                f_hi = fit[0]

    # Inner plateau radius: hidden behind the bore in the zone interior, but visible in the end
    # windows where the lobe is still detached. Invert the same fillet law there.
    est = []
    for z, v in samples:
        for lb in v:
            if lb[4]:
                continue
            for z_edge, sign, f in ((z_lo, +1.0, f_lo), (z_hi, -1.0, f_hi)):
                s = sign * (z - z_edge)
                if not (0.0 < s < f):
                    continue
                d = f - s
                est.append(lb[2] - f + math.sqrt(max(f * f - d * d, 0.0)))
    if est:
        r_in = float(np.median(est))
    elif inner_clean:
        r_in = float(np.median(inner_clean))
    else:
        r_in = bore_radius - tol.eps_cut(chord_tol)
    r_in = min(r_in, bore_radius - tol.eps_cut(chord_tol))
    if not (0.0 < r_in < r_out):
        return []

    out = []
    for tc in thetas:
        out.append(solids.build_filleted_wedge_solid(z_lo, z_hi, r_in, r_out, f_lo, f_hi,
                                                      tc, theta_half))
    return out


def _ring_area(pts) -> float:
    """Shoelace area of a closed ring (`pts` may or may not repeat the first point last)."""
    arr = np.asarray(pts, dtype=float)
    if len(arr) > 1 and np.allclose(arr[0], arr[-1]):
        arr = arr[:-1]
    x, y = arr[:, 0], arr[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _drop_close_ring_points(pts, min_gap: float):
    """Drop points from a closed ring (no repeated first==last) that are closer than `min_gap`
    to the last kept point. `simplify_closed_ring`'s RDP pass only bounds perpendicular chord
    deviation, not point spacing, so a fillet's curvature can leave a cluster of points a couple
    mm apart -- far below gmsh's `MeshSizeMin` (harness `meshcheck.py` uses `hmax/10`) for M6's
    loft cutter. Those CAD-mandated near-duplicate vertices sit right next to a 100 mm-scale
    element boundary and gmsh's tet mesher connects them with a near-flat sliver (measured:
    min SICN 0.0075, gate 0.1) -- purely a mesh-conditioning issue, not a shape-accuracy one, so
    thinning them out with a small floor (well under the fillet radius) fixes it without
    reintroducing the surface_deviation error RDP was tuned to avoid."""
    pts = list(pts)
    if len(pts) < 4:
        return np.asarray(pts)
    kept = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p[0] - kept[-1][0], p[1] - kept[-1][1]) >= min_gap:
            kept.append(p)
    if len(kept) > 1 and math.hypot(kept[0][0] - kept[-1][0], kept[0][1] - kept[-1][1]) < min_gap:
        kept.pop()
    return np.asarray(kept)


def _fit_ref_fillets(raw_pts, simp_pts, chord_tol: float):
    """Reconstruct the reference ring as exact tangent fillet arcs joined by straight flanks.

    `detect_arc_runs` reliably finds WHERE each fillet is but systematically truncates HOW FAR it
    runs (measured on M6: 95.6 deg spans at the tips vs a true 123.68, 30.9 vs 63.68 at the
    valleys), so its run endpoints are used only as seeds. The geometry is then recovered the way
    the truth was built: fit the long straight flanks between runs, intersect neighbouring flanks
    for the sharp corner, and inscribe the one tangent circle that fits the arc points. Returns
    None -- caller falls back to the RDP polygon loft -- whenever the reconstruction cannot be
    trusted."""
    runs = fitting.detect_arc_runs(simp_pts.tolist(), None)
    if len(runs) < 3:
        return None
    key = {(round(float(x), 9), round(float(y), 9)): i for i, (x, y) in enumerate(raw_pts)}
    spans = []
    for run in runs:
        a = key.get((round(float(simp_pts[run[0]][0]), 9), round(float(simp_pts[run[0]][1]), 9)))
        b = key.get((round(float(simp_pts[run[-1]][0]), 9), round(float(simp_pts[run[-1]][1]), 9)))
        if a is None or b is None:
            return None
        spans.append((a, b))
    fillets = fitting.fit_fillet_ring(raw_pts, spans, 0.3 * chord_tol)
    if not fillets:
        return None
    if any(not math.isfinite(f["radius"]) or f["radius"] <= 0.0 for f in fillets):
        return None
    if fitting.fillet_ring_deviation(raw_pts, fillets) > chord_tol:
        return None
    return fillets


def _build_bore_prism_or_loft(bore_rings, z_min: float, z_max: float, eps_cut_val: float,
                               chord_tol: float):
    """Select the constant-cross-section prism path (`_build_prism_bore`, M3's star bore) or the
    ruled-loft path (`solids.build_ruled_loft_solid`, M6's linearly-scaling star bore) based on
    what the stations actually measured, not on the milestone name. Ring area is proportional to
    scale^2 for a shape that uniformly scales about the axis, and MISSION §6.2 M6 says the scale
    itself is linear in z, so area(z) should be exactly quadratic in z; a `np.polyfit` degree-2 fit
    across every non-circular station's own measured area both detects "does this bore's size
    actually change" (M3: near-zero span, noise only) and, when it does, gives an accurate
    extrapolation to the cutter's true ends (`z_min - eps_cut_val`, `z_max + eps_cut_val`) without
    needing station data all the way out there (the true loft in MISSION spans z=-10..L+10, past
    what the input STL even covers at z=[z_min, z_max]).

    When lofting, the two end profiles are built by scaling ONE well-conditioned reference ring
    (the mid-station's raw points -- same choice `_build_prism_bore` makes, least likely to be
    distorted by inset/end effects) by the fitted scale ratio, rather than re-deriving each end's
    own geometry from noisy raw points near the (nonexistent, extrapolated) ends. The reference
    ring is first reduced with `fitting.simplify_closed_ring` (RDP on the closed 2D ring, `eps =
    chord_tol`) -- tried fitting exact fillet arcs via `detect_arc_runs` first (mirroring
    `_build_prism_bore`'s M3 path) and it does NOT transfer here: M3's bore is a true constant-
    cross-section extrude (flat, exactly planar mesh facets), but M6's intermediate cross-sections
    come from slicing a genuinely curved (skew ruled, since corresponding wire0/wire1 edges are
    not coplanar in general) 3D loft surface -- its STL tessellation reads as spurious sub-mm-
    sagitta "curvature" at unpredictable points around the ring (measured: `detect_arc_runs`
    returned 8-30 garbage runs instead of the true 12, wildly unstable between adjacent stations),
    corrupting any 3-point arc fit through them. Plain RDP sidesteps the whole problem: a sub-
    chord_tol sagitta reads as *within tolerance* of the straight chord and gets silently
    absorbed, while the real fillet curvature (larger sagitta) still keeps enough points to track
    it -- measured M6 volume_err_pct 0.66% (garbage arcs) -> 0.11% (RDP-simplified straight
    polygon, ~94 pts down from ~425, final face count ~97, gate 200)."""
    bore_rings = sorted(bore_rings, key=lambda p: p[0])
    zs_r = np.array([z for z, _ in bore_rings], dtype=float)
    areas = np.array([_ring_area(np.asarray(r.coords)) for _, r in bore_rings], dtype=float)
    a_span = float(areas.max() - areas.min())
    if a_span < 1e-3 * float(areas.mean()):
        return _build_prism_bore(bore_rings, z_min, z_max, eps_cut_val, eps_cut_val, chord_tol)

    coef = np.polyfit(zs_r, areas, 2)
    ref_idx = len(bore_rings) // 2
    _, ref_ring = bore_rings[ref_idx]
    ref_pts_raw = np.asarray(ref_ring.coords)[:-1]
    ref_simp = np.asarray(fitting.simplify_closed_ring(ref_pts_raw.tolist(), 0.3 * chord_tol))
    ref_pts = _drop_close_ring_points(ref_simp, 5.0 * chord_tol)
    ref_area = areas[ref_idx]
    z_lo_t = z_min - eps_cut_val
    z_hi_t = z_max + eps_cut_val
    a_lo_t = max(1e-9, float(np.polyval(coef, z_lo_t)))
    a_hi_t = max(1e-9, float(np.polyval(coef, z_hi_t)))
    s_lo = math.sqrt(a_lo_t / ref_area)
    s_hi = math.sqrt(a_hi_t / ref_area)
    fillets = _fit_ref_fillets(ref_pts_raw, ref_simp, chord_tol)
    if fillets is not None:
        return solids.build_fillet_loft_solid(z_lo_t, s_lo, z_hi_t, s_hi, fillets)

    pts_lo = ref_pts * s_lo
    pts_hi = ref_pts * s_hi
    return solids.build_ruled_loft_solid(z_lo_t, pts_lo, z_hi_t, pts_hi, r_fillet_thresh=0.0)


def _run(args) -> int:
    chord_tol = args.chord_tol
    mesh, R_axis, info = pio.load_and_orient(args.input_stl, args.axis, args.units, chord_tol)
    if not info["is_watertight"] or info["body_count"] != 1:
        print(f"rebuild.py: input mesh is not a single watertight body "
              f"(watertight={info['is_watertight']}, body_count={info['body_count']})",
              file=sys.stderr)
        return 3

    z_min, z_max = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    L = z_max - z_min
    eps_end_val = tol.eps_end(chord_tol, L)
    eps_cut_val = tol.eps_cut(chord_tol)

    # Stations right at a curved end (e.g. where a dome pinches out against a bore, M2) sit in a
    # region of steep dR/dz; the STL's own chordal tessellation error there gets amplified by
    # that slope into a much larger apparent circle-fit residual (measured on M2: ~1.7 mm at
    # 5 mm inset vs. the 0.75 mm circle_max_resid gate, settling under gate only past ~70 mm
    # inset). eps_end alone (~1 mm here) is nowhere near enough of a floor for that; widen the
    # station-placement inset specifically, without touching eps_end's other uses (cutter
    # extension etc.) or M1's placement (its profile has no steep-slope region to avoid).
    # `200*chord_tol` alone breaks at M9's coarser chord_tol=5 (a 1000 mm inset on L~10000 mm
    # swallows the entire aft dome/slot-exit region past z=8945, per MISSION.md §5.2 step 2's
    # own note that this hard-coded inset needs a cap at 0.02*L).
    station_eps = min(max(eps_end_val, 200.0 * chord_tol), 0.02 * L)
    if args.adaptive:
        zs = stations.adaptive_stations(mesh, z_min, z_max, args.sections, station_eps,
                                         vertex_zs=mesh.vertices[:, 2])
    else:
        zs = stations.uniform_stations(z_min, z_max, args.sections, station_eps,
                                        vertex_zs=mesh.vertices[:, 2])

    outer_pts = []   # (z, R) of the exterior loop
    bore_pts = []    # (z, R) of the (single) axis-centered interior loop, only while circular
    bore_rings = []  # (z, ndarray of (x,y)) of the axis-centered interior loop, when NOT circular
    sat_samples = []  # (z, cx, cy, R) of every OFF-axis circular hole (M7's satellites), any z
    sat_rings = []   # (z, cx, cy, R, ndarray of (x,y)) of every OFF-axis NON-circular hole (M8's
                     # obround slots), any z -- R is the (possibly poor) Kasa-fit radius, kept
                     # only as a rough size estimate, not used for shape reconstruction
    all_zz = []      # every station z actually sliced, in order (for chain-edge neighbor lookup)
    for z in zs:
        polys, zz = slice_station(mesh, z, chord_tol)
        if not polys:
            print(f"rebuild.py: no section recovered at z={z:.3f}", file=sys.stderr)
            return 4
        if len(polys) != 1:
            print(f"rebuild.py: expected 1 outer loop at z={zz:.3f}, got {len(polys)} "
                  f"(unsupported topology)", file=sys.stderr)
            return 4
        all_zz.append(zz)

        poly = polys[0]
        ext = np.asarray(poly.exterior.coords)
        cx, cy, Ro, max_resid, _ = fit_circle_robust(ext)
        if max_resid > tol.circle_max_resid(chord_tol) or not _axis_centered(cx, cy, Ro, chord_tol):
            print(f"rebuild.py: outer loop at z={zz:.3f} is not an axis-centered circle "
                  f"(max_resid={max_resid:.4f}, center=({cx:.4f},{cy:.4f})) — "
                  f"non-axisymmetric outer envelopes not yet implemented", file=sys.stderr)
            return 4
        outer_pts.append((zz, Ro))

        rings = list(poly.interiors)
        if not rings:
            print(f"rebuild.py: expected at least 1 interior hole at z={zz:.3f}, got 0 "
                  f"(unsupported topology)", file=sys.stderr)
            return 4

        # At most one hole per station may be axis-centered (the main bore chain, M1-M6's
        # single-hole case). Its cross-section may be a non-circular but axially-*constant*
        # (prismatic) shape, e.g. M3's star bore or M4/M5's fin slots — still tractable by
        # extrusion (`solids.build_prism_solid`) even though it is not a surface of revolution;
        # a hole that fails the circle fit is recorded as a raw ring instead of erroring
        # immediately, exactly as before. Every OTHER (off-axis) hole is one sample of an M7
        # satellite chain, matched across stations by proximity after this loop.
        central_seen = False
        for ring in rings:
            hole = np.asarray(ring.coords)
            cxh, cyh, Rh, max_resid2, _ = fit_circle(hole)
            is_circle = max_resid2 <= tol.circle_max_resid(chord_tol)
            is_central = _axis_centered(cxh, cyh, Rh, chord_tol)
            if is_circle and is_central:
                if central_seen:
                    print(f"rebuild.py: more than one axis-centered hole at z={zz:.3f} — "
                          f"unsupported topology", file=sys.stderr)
                    return 4
                central_seen = True
                bore_pts.append((zz, Rh))
            elif is_circle:
                sat_samples.append((zz, cxh, cyh, Rh))
            elif is_central or len(rings) == 1:
                # A non-circular hole is the central bore either when its measured center is
                # near the axis, OR when it is the only hole at this station at all (M3/M4/M5's
                # single star/fin bore): a non-circular Kasa fit's own center estimate can be
                # biased by several tenths of a mm on a strongly asymmetric cross-section near a
                # tip/end (measured on M4's fin bore near z=9900, ~0.5mm off vs a 0.25mm gate)
                # even though there is unambiguously only one interior loop to classify.
                if central_seen:
                    print(f"rebuild.py: more than one axis-centered hole at z={zz:.3f} — "
                          f"unsupported topology", file=sys.stderr)
                    return 4
                central_seen = True
                bore_rings.append((zz, ring))
            else:
                # Off-axis, non-circular hole among *multiple* holes at this station: one
                # station-sample of an M8 satellite SLOT chain (an obround perforation off the
                # main axis) -- matched across stations by centroid proximity after this loop,
                # exactly like M7's circular `sat_samples`, but built as a constant-cross-section
                # prism (`solids.build_prism_solid`, the same machinery M3's star bore uses)
                # instead of a cylinder.
                sat_rings.append((zz, cxh, cyh, Rh, hole))

    # Group satellite samples into chains by nearest-center match to the previous station's
    # live chains — satellites are straight (M7), so a true match is ~0 mm apart while distinct
    # satellites are a full inter-hole spacing apart (no ambiguity at the 2x-radius threshold).
    sat_by_z = {}
    for z, cxh, cyh, Rh in sat_samples:
        sat_by_z.setdefault(z, []).append((cxh, cyh, Rh))
    sat_chains = []
    for z in sorted(sat_by_z):
        used = set()
        for cxh, cyh, Rh in sat_by_z[z]:
            best_i, best_d = None, None
            for i, ch in enumerate(sat_chains):
                if i in used:
                    continue
                _, lcx, lcy, lR = ch[-1]
                d = math.hypot(cxh - lcx, cyh - lcy)
                if d < 2.0 * lR and (best_d is None or d < best_d):
                    best_i, best_d = i, d
            if best_i is None:
                sat_chains.append([(z, cxh, cyh, Rh)])
                used.add(len(sat_chains) - 1)
            else:
                sat_chains[best_i].append((z, cxh, cyh, Rh))
                used.add(best_i)

    # Each satellite chain becomes its own straight cylinder cutter, extended to the part's
    # true axial extent (+eps_cut) if it spans every station, or bisected to its own birth/death
    # z (`_bisect_hole_edge`) if it starts or stops partway (M7's flat end wall at z=7000).
    sat_cutters = []
    for ch in sat_chains:
        cxs = np.array([s[1] for s in ch]); cys = np.array([s[2] for s in ch])
        Rs = np.array([s[3] for s in ch])
        cx0, cy0, R0 = float(cxs.mean()), float(cys.mean()), float(Rs.mean())
        z_first, z_last = ch[0][0], ch[-1][0]
        i_first, i_last = all_zz.index(z_first), all_zz.index(z_last)
        if i_first == 0:
            z_lo = z_min - eps_cut_val
        else:
            z_lo = _bisect_hole_edge(mesh, z_first, all_zz[i_first - 1], chord_tol, cx0, cy0, R0)
        if i_last == len(all_zz) - 1:
            z_hi = z_max + eps_cut_val
        else:
            z_hi = _bisect_hole_edge(mesh, z_last, all_zz[i_last + 1], chord_tol, cx0, cy0, R0)
        sat_cutters.append((solids.build_cylinder_solid(cx0, cy0, z_lo, z_hi, R0), z_lo, z_hi))

    # Off-axis NON-circular satellite SLOT chains (M8's obround perforations): group by raw-point
    # centroid proximity, exactly like the circular case above, except the match distance is
    # measured directly from whatever OTHER slots are seen at the SAME station (a true lower
    # bound on "how far apart two distinct slots are" there, so no milestone-specific spacing
    # constant is needed) rather than a multiple of a not-very-meaningful Kasa-fit radius.
    ring_by_z = {}
    for z, cxh, cyh, Rh, hole in sat_rings:
        cxr, cyr = float(hole[:, 0].mean()), float(hole[:, 1].mean())
        ring_by_z.setdefault(z, []).append((cxr, cyr, hole))
    ring_chains = []
    zz_index = {z: i for i, z in enumerate(all_zz)}
    for z in sorted(ring_by_z, key=lambda zk: zz_index[zk]):
        entries = ring_by_z[z]
        if len(entries) > 1:
            match_dist = 0.5 * min(
                math.hypot(a[0] - b[0], a[1] - b[1])
                for i, a in enumerate(entries) for b in entries[i + 1:])
        else:
            match_dist = math.inf
        used = set()
        for cxr, cyr, hole in entries:
            best_i, best_d = None, None
            for i, ch in enumerate(ring_chains):
                if i in used:
                    continue
                lz, lcx, lcy, _ = ch[-1]
                # A ring may only extend an existing chain from the IMMEDIATELY PRECEDING
                # sliced station, not merely "some earlier station with a close centroid": if a
                # station in between classified this satellite as merged into the single
                # combined bore/slot ring (M8's slot midspan, where the off-axis holes overlap
                # the central bore into one non-circular ring — see bore_rings above) rather
                # than as its own separate off-axis loop, the satellite was genuinely NOT its
                # own hole there and this must be a fresh chain, however close the centroid is
                # (satellites don't move, so a stale centroid match across a huge z gap would
                # otherwise wrongly bridge two disjoint narrow appearance windows into one
                # chain spanning the whole merged middle with the WRONG, edge-window cross
                # section swept across it).
                if zz_index[z] != zz_index[lz] + 1:
                    continue
                d = math.hypot(cxr - lcx, cyr - lcy)
                if d < match_dist and (best_d is None or d < best_d):
                    best_i, best_d = i, d
            if best_i is None:
                ring_chains.append([(z, cxr, cyr, hole)])
                used.add(len(ring_chains) - 1)
            else:
                ring_chains[best_i].append((z, cxr, cyr, hole))
                used.add(best_i)

    # Each slot chain becomes a constant-cross-section prism cutter (`solids.build_prism_solid`,
    # M3's star-bore machinery): the chain's own mid-z sample is the representative cross-section
    # (least likely to be distorted by an inset/end effect, same choice `_build_prism_bore`
    # makes), extended to the part's true axial extent if it spans every station, else bisected to
    # its own birth/death z (`_bisect_ring_edge`, M8's axial end fillet at z=5850/9650).
    for ch in ring_chains:
        if len(ch) < 3:
            # A 1-2 station chain right at the edge of a merged non-circular `bore_rings` run
            # (M8's slot/bore overlap pinching to a momentary extra split before re-merging) is
            # too thin a sliver to build a robust standalone prism cutter from: its "constant
            # cross-section" is taken from a single near-degenerate sample right where it's
            # about to vanish, and cutting that sliver so close to the bore boundary produced an
            # invalid final BRep (caught by re-reading the exported STEP and re-checking with
            # BRepCheck_Analyzer, even though the in-memory pre-export shape looked valid) —
            # observed on M8's fore/aft wall transitions once adaptive placement got dense
            # enough to sample inside the ~20 mm flicker window at all. The surrounding
            # `bore_rings` merged-loop path already covers this z range as one combined hole, so
            # dropping the sliver just means that few-mm-wide edge is approximated by the merged
            # shape instead of modeled exactly — negligible next to the volume/deviation gates
            # (this is exactly what happened, harmlessly, when placement missed the window
            # entirely and passed every non-station_bands gate).
            continue
        cx0 = float(np.mean([c[1] for c in ch]))
        cy0 = float(np.mean([c[2] for c in ch]))
        rep_hole = ch[len(ch) // 2][3]
        r_extent = float(np.max(np.hypot(rep_hole[:, 0] - cx0, rep_hole[:, 1] - cy0)))
        match_dist = 2.0 * r_extent
        z_first, z_last = ch[0][0], ch[-1][0]
        i_first, i_last = all_zz.index(z_first), all_zz.index(z_last)
        if i_first == 0:
            z_lo = z_min - eps_cut_val
        else:
            z_lo = _bisect_ring_edge(mesh, z_first, all_zz[i_first - 1], chord_tol, cx0, cy0,
                                      match_dist)
        if i_last == len(all_zz) - 1:
            z_hi = z_max + eps_cut_val
        else:
            z_hi = _bisect_ring_edge(mesh, z_last, all_zz[i_last + 1], chord_tol, cx0, cy0,
                                      match_dist)
        sat_cutters.append((solids.build_prism_solid(rep_hole.tolist(), z_lo, z_hi), z_lo, z_hi))

    event_z = None
    circ_before = None
    pts_before, pts_after = [], []
    event_fore = event_aft = None
    zone_fore = zone_aft = None
    paths_slot = None
    if bore_rings and bore_pts:
        # M4: a plain circular bore fore of `fin_z_start`, fin slots (non-circular combined
        # bore+slot ring) aft of it (or vice versa) — a single topology event. M5 adds domes on
        # both ends, and per the design the fins stop short of each dome shoulder, so the bore
        # is circular BEFORE the fin zone, non-circular THROUGH it, and circular again AFTER it
        # (two events sandwiching one prism run). Anything else (circular and non-circular
        # stations interleaved in more than that one pattern) is not modeled here.
        bore_pts.sort(key=lambda p: p[0])
        bore_rings.sort(key=lambda p: p[0])
        ring_z_min, ring_z_max = bore_rings[0][0], bore_rings[-1][0]
        pts_before = [p for p in bore_pts if p[0] < ring_z_min]
        pts_after = [p for p in bore_pts if p[0] > ring_z_max]
        if len(pts_before) + len(pts_after) != len(bore_pts) or not (pts_before or pts_after):
            print("rebuild.py: hole loop topology is inconsistent across stations "
                  "(circular and non-circular sections are interleaved, not one contiguous "
                  "transition) — not yet implemented", file=sys.stderr)
            return 4
        if pts_before and pts_after:
            event_fore = _bisect_topology_event(mesh, pts_before[-1][0], ring_z_min, chord_tol,
                                                 circ_at_a=True)
            event_aft = _bisect_topology_event(mesh, ring_z_max, pts_after[0][0], chord_tol,
                                                circ_at_a=False)
            # Those two are the planes where the BORE's own cross-section changes class, and they
            # stay the geometry seams (the prism cutters must not reach past the run of stations
            # whose cross-section they were fitted from). They are not the topology events: when
            # the slots are born detached from the bore (M8's r=150 end fillets) the zone starts
            # earlier and ends later, so report the zone's true birth/death, found by bisecting on
            # the whole section's cavity content (`_bisect_slot_zone_edge`). Reporting the two
            # zone boundaries rather than the interior merge planes also keeps the event count at
            # 2, inside M8's `topo_events_max` of 3. On M4/M5, where the fins reach the bore at
            # their first station, the two brackets coincide and min/max is a no-op.
            zone_fore, zone_aft = event_fore, event_aft
            slot_i = [zz_index[z] for z, _ring in bore_rings] + \
                     [zz_index[z] for z, _cx, _cy, _R, _h in sat_rings]
            i_lo, i_hi = min(slot_i), max(slot_i)
            if i_lo > 0:
                zone_fore = min(zone_fore,
                                _bisect_slot_zone_edge(mesh, all_zz[i_lo], all_zz[i_lo - 1],
                                                       chord_tol))
            if i_hi < len(all_zz) - 1:
                zone_aft = max(zone_aft,
                               _bisect_slot_zone_edge(mesh, all_zz[i_hi], all_zz[i_hi + 1],
                                                      chord_tol))
        else:
            circ_before = bool(pts_before)
            z_a, z_b = (pts_before[-1][0], ring_z_min) if circ_before \
                else (ring_z_max, pts_after[0][0])
            event_z = _bisect_topology_event(mesh, z_a, z_b, chord_tol, circ_at_a=circ_before)

    # Envelope spans the true axial extent. Extrapolate the outer radius to it from the two
    # nearest fitted stations (linear secant) rather than copying the nearest station's R flat —
    # correct either way for a prismatic profile (M1, zero slope) and far closer for a curved
    # end (M2's domes, see `_extrapolate_end`).
    min_dz = 5.0 * chord_tol
    resid_tol = tol.circle_max_resid(chord_tol)
    fore_model = aft_model = None
    axial_origin_z = 0.0
    if len(outer_pts) < 2:
        r_start, fore_window_z = outer_pts[0][1], z_min
        r_end, aft_window_z = outer_pts[-1][1], z_max
    else:
        fz0, fcoef, fore_window_z, fore_shoulder = _dome_model(
            outer_pts, mesh, True, min_dz, resid_tol, chord_tol)
        az0, acoef, aft_window_z, aft_shoulder = _dome_model(
            outer_pts, mesh, False, min_dz, resid_tol, chord_tol)
        fore_model, aft_model = (fz0, fcoef), (az0, acoef)
        # M10 (§5.5.1/§6.2): the input's hidden origin can be placed anywhere along the axis, not
        # just at z=0, so our internally-arbitrary z=0 (wherever the STL happened to sit before
        # frame normalisation) generally does NOT match the ground truth's canonical z=0 -- but
        # the generator's convention anchors canonical z=0 at the fore dome's theoretical full
        # closure point (R=0), which the already-fit quadratic dome model can solve for directly
        # (same `_solve_pinch_z` used above for R=bore_radius, just with target_r=0). Subtracting
        # this `axial_origin_z` from every *reported* z value re-expresses our stations in the
        # same convention the scorer's canonical frame uses, without touching the geometry
        # pipeline itself (the exported STEP's placement is independently correct already, via
        # `export.undo_axis_transform`).
        axial_origin_z = _solve_pinch_z(fz0, fcoef, 0.0, z_min)
        if axial_origin_z is None:
            axial_origin_z = 0.0
        # On a noisy/coarse input (M9) the raw mesh z-bound can miss the true dome/bore pinch by
        # more than a station spacing (grid quantization + noise, not a real geometric point —
        # see `_solve_pinch_z`). Where a central bore chain exists, solve each dome model for the
        # z at which it crosses the bore radius and prefer that over the raw bound, guarded by a
        # sanity cap so an ambiguous/false root (e.g. a shallow dome with no real pinch nearby)
        # can't silently move the part's axial extent by something implausible.
        if bore_pts:
            cap = max(5.0 * station_eps, 50.0)
            z_fore_pinch = _solve_pinch_z(fz0, fcoef, bore_pts[0][1], z_min)
            z_aft_pinch = _solve_pinch_z(az0, acoef, bore_pts[-1][1], z_max)
            if z_fore_pinch is not None and abs(z_fore_pinch - z_min) < cap:
                z_min = z_fore_pinch
            if z_aft_pinch is not None and abs(z_aft_pinch - z_max) < cap:
                z_max = z_aft_pinch
        r_start = _eval_r2_quadratic(fz0, fcoef, z_min)
        r_end = _eval_r2_quadratic(az0, acoef, z_max)
        # The curved window runs all the way to the barrel shoulder when one was located, so the
        # dome's tangential meeting with the cylinder is modelled by the spline instead of by a
        # corner-cutting straight chord (`_dome_shoulder_z`).
        if fore_shoulder is not None:
            fore_window_z = fore_shoulder
        if aft_shoulder is not None:
            aft_window_z = aft_shoulder
    # A dome that a straight bore breaks through (M2/M5) pinches to zero annular width exactly
    # at the true mesh extent, i.e. the outer radius there *equals* the bore radius (a bore fit
    # is far more reliable than the outer extrapolation, since it isn't near the dome's steep
    # curvature). Snap to it when the extrapolation already landed close, rather than trusting
    # the extrapolation's residual ~10 mm error verbatim.
    is_pinch_start = bool(bore_pts) and abs(r_start - bore_pts[0][1]) < 50.0
    is_pinch_end = bool(bore_pts) and abs(r_end - bore_pts[-1][1]) < 50.0
    if is_pinch_start:
        r_start = bore_pts[0][1]
    if is_pinch_end:
        r_end = bore_pts[-1][1]
    # Resample every chord inside the validated dome window — the excluded inset band AND the
    # real-station-to-real-station gaps deeper in the dome — from the same quadratic-in-R^2
    # model used for the endpoint itself, replacing (not just densifying between) the raw
    # circle-fit stations there; see `_densify_dome_chords` for why straight chords across real
    # station gaps (not just the end gap) turned out to be the dominant volume error term, and
    # why the raw stations must be dropped rather than kept alongside the resample (face count).
    # Not done for a non-pinch end (M1): there the profile is flat and a straight chord is exact.
    middle_pts = list(outer_pts)
    fore_gap, aft_gap = [], []
    curve_windows = []
    if is_pinch_start:
        fore_gap = _densify_dome_chords(outer_pts, z_min, None, True, min_dz, resid_tol,
                                        fore_window_z, model=fore_model)
        fore_gap[0] = (z_min, r_start)  # keep the bore-snapped value, not the model's own fit there
        middle_pts = [p for p in middle_pts if p[0] > fore_window_z]
        curve_windows.append((z_min, fore_window_z))
    if is_pinch_end:
        aft_gap = _densify_dome_chords(outer_pts, None, z_max, False, min_dz, resid_tol,
                                       aft_window_z, model=aft_model)
        aft_gap[-1] = (z_max, r_end)
        middle_pts = [p for p in middle_pts if p[0] < aft_window_z]
        curve_windows.append((aft_window_z, z_max))
    start_pt = [] if is_pinch_start else [(z_min, r_start)]
    end_pt = [] if is_pinch_end else [(z_max, r_end)]
    outer_full = sorted(start_pt + fore_gap + middle_pts + aft_gap + end_pt, key=lambda p: p[0])
    outer_solid = solids.build_revolve_solid(outer_full, chord_tol, curve_windows=curve_windows)

    lobe_cutters = []
    if bore_rings and bore_pts:
        # Two bore cutters, one per side of the topology event, fused into one before the single
        # cut against the envelope. Past the part's true ends (z_min/z_max) each cutter still
        # needs the full `eps_cut` margin for a robust boolean cut against the envelope, same as
        # every other cutter. But at the internal event_z seam, extending by that same 10x margin
        # bled a full off-axis cross-section (the fin shape reaches out to `fin_r_outer`, far past
        # the circular bore radius) `eps_cut_val` mm into the *other* solid's true region — the
        # union at event_z +/- eps_cut_val is the union of BOTH cross-sections, not either one
        # alone, so the fin-shaped cutter also cut fin-shaped material out of the plain-circular
        # region (measured: exactly `eps_cut_val` = 5.0 mm surface deviation at the fin-tip radius
        # just inside the circular zone, gate 1.0 mm). Use a much smaller `seam_eps` there instead
        # — just enough for `BRepAlgoAPI_Fuse`'s topological overlap, not a boolean-cut margin.
        seam_eps = 0.5 * chord_tol
        if pts_before and pts_after:
            # M5 sandwich: two internal seams (event_fore, event_aft), no true end on either
            # side of the fin-slot prism, so both its extensions use the small seam margin —
            # same eps_cut-bleed reasoning as the single-event case above, just on both sides.
            # The fuzzy tolerance passed to `booleans.fuse` matters independently of the seam
            # overlap length: using the full `tol.fuzzy(chord_tol)` here let BOPAlgo snap/merge
            # vertices across the whole overlap band, distorting the circular bore radius right
            # at the seam by up to ~1 mm (measured: surface_deviation_max_mm 0.68 vs the 0.6 mm
            # gate, entirely inside the supposedly-plain-circular fore_cylinder region). Using
            # `seam_eps` itself (much smaller) as the fuzzy value fixed that (0.36 mm, well under
            # gate). (Tried also widening the *fin_solid*'s own seam extension to fatten the
            # overlap volume for gmsh — that reintroduces the exact eps_cut-bleed bug from M4:
            # the fin/star cutter then removes star-shaped material from genuinely-circular
            # territory, measured 4.0 mm deviation at the widened amount. Only the *circular*
            # cutter's overlap may be widened — circle is always a subset of the star cross-
            # section, so extending it further into fin territory is a no-op there.)
            #
            # `gmsh_tet` fix (iter 22): the sliver gmsh chokes on isn't a tolerance/fuzzy issue
            # (confirmed: bumping the fuse fuzzy value alone from 1x to 1.5x seam_eps left
            # min_quality bit-identical, 0.006636085173 -> 0.006636085026). Root cause per iter
            # 21's vertex dump: `fin_solid` is a *constant*-cross-section prism (see
            # `_build_prism_bore`) — even right at its own boundary it's the full star shape, not
            # tapered — so fusing it against `circ_fore/aft_solid` (a plain cylinder) produces a
            # genuine intersection edge between the star's "web" boundary (~r 382-391, the
            # material between fin slots) and the cylinder, squeezed into a band only
            # `2*seam_eps` = 0.5 mm thick — far thinner than gmsh's own `MeshSizeMin` (hmax/10 =
            # 10 mm at the M5 gate), forcing a near-degenerate tet there. Since the circular
            # cutter's overlap is a geometric no-op arbitrarily far into the fin zone (circle is
            # always a subset of the star cross-section there, same invariant as above), widening
            # it drastically (80x seam_eps ~= 20 mm, comparable to gmsh's own min element size)
            # while shrinking `fin_solid`'s own extension to near-zero moves that same
            # intersection edge into a band gmsh can actually mesh, with ZERO effect on the final
            # cut geometry (bore_solid's boolean union in that region is unchanged — fin_solid's
            # material there is a strict superset of circ's). Verified: full M5 scorer now
            # `pass:true, progress:1.0` (was progress 0.9481, `gmsh_tet` 0.00664 <= 0.1 gate);
            # `gmsh_tet` min_quality 0.234 (was tried at fin_overlap=0 exactly too: works but only
            # 0.167, less margin — kept the small nonzero value for a bigger safety margin).
            circ_overlap = 80.0 * seam_eps
            fin_overlap = 0.02 * seam_eps
            # The `circ_overlap` widening is only the claimed geometric no-op while the circular
            # cutter is a STRICT subset of the prism's cross-section throughout the overlap band.
            # By default it is not, it is merely *almost* coincident with it, which is the worst
            # case for BOPAlgo: (a) one prism serves both seams, so its `seam_bore_radius` snap
            # cannot equal both fitted seam radii — on M8 it lands 0.011 mm from each; (b) the
            # circular cutter's meridian is RDP-simplified (eps 0.5*chord_tol) over a radius
            # variation far below that, so the whole cutter collapses to ONE very slightly
            # conical face that grazes — and crosses — the prism's constant-radius bore arc; and
            # (c) the prism's raw ring still dips ~0.2 mm inside its own snapped bore arcs on the
            # straight bridges between runs. Two surfaces that are distinct but much closer than
            # the boolean's fuzzy value cannot be resolved into a clean imprint: measured on M8,
            # the fuse returned BRepCheck_SelfIntersectingWire and the subsequent cut split the
            # part into 2 solids (exit 5). Making the containment unambiguous instead — dropping
            # the circular cutter's radius by `bore_seam_clearance`, an order of magnitude past
            # the fuzzy value, at the overlap end — makes both fuses valid and the cut a single
            # solid. It costs no geometry: the z range it applies to is inside the prism's own
            # span, where the prism already removes strictly more material than the circle could.
            # The drop is a TAPER from the last real point, not a step at the seam. A step form
            # (two collar points, dropping over `fin_overlap`) was tried and is worse: it puts a
            # real 2 mm feature at the seam plane that the prism does not fully mask, pushing
            # M5's `surface_deviation_max_mm` to 1.073 (gate 0.6) versus 0.681 for the taper.
            seam_bore_radius = 0.5 * (pts_before[-1][1] + pts_after[0][1])
            # Top rung: filleted angular wedges over the WHOLE zone [zone_fore, zone_aft], the
            # only path that models the tapering fillet window at each slot end — the entire M8
            # deviation failure. Falls through when the sections do not fit the annular-sector
            # model (M5's fins are constant-Cartesian-width, so they never do).
            lobe_cutters = _build_slot_wedges(bore_rings, sat_rings, zone_fore, zone_aft,
                                               seam_bore_radius, chord_tol, mesh=mesh)
            bore_full = [(z_min - eps_cut_val, pts_before[0][1])] + pts_before + pts_after \
                + [(z_max + eps_cut_val, pts_after[-1][1])]
            if lobe_cutters:
                paths_slot = "wedge"
                bore_solid = solids.build_revolve_solid(bore_full, chord_tol)
            else:
                # Next rung: the three-cutter fuse (one merged bore+slot ring prism between the
                # two circular cutters => ONE tool, one wire per station). Where it validates it
                # is strictly the better tool: the fillets live on the merged ring's own boundary
                # instead of becoming free-standing ribbon faces, so it meshes far better than
                # the decomposition (measured on M5: 53 faces / gmsh min SICN 0.234 fused, versus
                # 205 faces / 0.0817 decomposed against a 0.1 gate). Round 1 shipped M5 on this
                # path; iter 51 replaced it unconditionally to fix M8 and silently regressed M5.
                # But the fuse is NOT always available — on M8 the merged ring's own bore arc and
                # the circular cutter's cylinder are two distinct surfaces closer together than
                # the boolean's fuzzy value, which BOPAlgo cannot imprint, and both fuses come
                # back invalid (`_build_slot_lobes` docstring, PROGRESS `## Do not retry`). So
                # decide by measurement rather than by shape class: build the fused tool, check
                # it, and decompose only when it really is invalid. Clearance 0 first — it is
                # what Round 1 shipped and it meshes far better (0.291 vs 0.0062 min SICN); the
                # clearance rung exists only to make an otherwise-invalid fuse valid.
                fused = last_fused = None
                for clearance in (0.0, 4.0 * seam_eps):
                    last_fused = _fuse_sandwich_bore(
                        bore_rings, pts_before, pts_after, z_min, z_max, event_fore, event_aft,
                        circ_overlap, fin_overlap, seam_eps, eps_cut_val, seam_bore_radius,
                        chord_tol, clearance)
                    if BRepCheck_Analyzer(last_fused).IsValid():
                        fused = last_fused
                        break
                if fused is not None:
                    bore_solid = fused
                else:
                    # Cavity decomposition: one uninterrupted circular bore revolve over the whole
                    # length plus one independent prism per slot, so no two cutter surfaces are
                    # ever near-coincident and no fuse is needed at all.
                    lobe_cutters = _build_slot_lobes(bore_rings, event_fore, event_aft,
                                                      seam_bore_radius, chord_tol)
                    if lobe_cutters:
                        paths_slot = "prism"
                        bore_solid = solids.build_revolve_solid(bore_full, chord_tol)
                    else:
                        bore_solid = last_fused
        elif circ_before:
            # Same asymmetric-overlap fix as the M5 sandwich path above (`circ_overlap`/
            # `fin_overlap`), applied to M4's single-event seam: the bore_radius snap makes
            # fin_solid's own bore arc land almost exactly tangent to circ_solid's, so a
            # symmetric `seam_eps`-wide overlap band squeezes the intersection edge between
            # them into a sub-gmsh-element-size sliver (min_quality ~0.007, gate 0.1) — and,
            # before this fix, into a near-zero-thickness sliver so thin BRepMesh tessellated
            # it into zero-area triangles, which is what turned `surface_deviation_max_mm` into
            # NaN (division by zero in trimesh's closest-point-on-triangle) instead of a normal
            # failure value. Widening only the circular cutter's overlap is a geometric no-op
            # (circle is always a subset of the star/fin cross-section), so it moves the
            # intersection edge into a meshable band with zero effect on the final cut geometry.
            circ_overlap = 80.0 * seam_eps
            fin_overlap = 0.02 * seam_eps
            circ_full = [(z_min - eps_cut_val, bore_pts[0][1])] + bore_pts \
                + [(event_z + circ_overlap, bore_pts[-1][1])]
            fin_solid = _build_prism_bore(bore_rings, event_z, z_max, fin_overlap, eps_cut_val,
                                           chord_tol, bore_radius=bore_pts[-1][1])
            circ_solid = solids.build_revolve_solid(circ_full, chord_tol)
            bore_solid = booleans.fuse(circ_solid, fin_solid, seam_eps)
        else:
            circ_overlap = 80.0 * seam_eps
            fin_overlap = 0.02 * seam_eps
            circ_full = [(event_z - circ_overlap, bore_pts[0][1])] + bore_pts \
                + [(z_max + eps_cut_val, bore_pts[-1][1])]
            fin_solid = _build_prism_bore(bore_rings, z_min, event_z, eps_cut_val, fin_overlap,
                                           chord_tol, bore_radius=bore_pts[0][1])
            circ_solid = solids.build_revolve_solid(circ_full, chord_tol)
            bore_solid = booleans.fuse(circ_solid, fin_solid, seam_eps)
    elif bore_rings:
        bore_solid = _build_bore_prism_or_loft(bore_rings, z_min, z_max, eps_cut_val, chord_tol)
    else:
        bore_full = [(z_min - eps_cut_val, bore_pts[0][1])] + bore_pts \
            + [(z_max + eps_cut_val, bore_pts[-1][1])]
        bore_solid = solids.build_revolve_solid(bore_full, chord_tol)

    shape = booleans.cut(outer_solid, bore_solid, tol.fuzzy(chord_tol))
    # Slot lobes of a decomposed merged bore+slot cavity: each overlaps the already-cut bore
    # deeply and transversally, so independent sequential cuts are robust (same argument as the
    # M7 satellites below).
    for lobe in lobe_cutters:
        shape = booleans.cut(shape, lobe, tol.fuzzy(chord_tol))
    # Satellite perforations (M7) are geometrically disjoint from the main bore and from each
    # other, so a sequence of independent cuts gives the same result as fusing them first and
    # is simpler/more robust than a multi-solid fuse of disjoint cutters.
    sat_events_raw = []
    for sat_solid, z_lo, z_hi in sat_cutters:
        shape = booleans.cut(shape, sat_solid, tol.fuzzy(chord_tol))
        if z_lo > z_min + eps_cut_val:
            sat_events_raw.append(z_lo)
        if z_hi < z_max - eps_cut_val:
            sat_events_raw.append(z_hi)
    # Several satellite chains often die/are born at (numerically near-identical) the same
    # z-plane (M7: all 6 perforations end at z=7000) — report ONE event per distinct plane, not
    # one per chain, matching what score.py's `topo_events_max` anti-gaming check expects.
    sat_events_z_mm = []
    for z in sorted(sat_events_raw):
        if sat_events_z_mm and abs(z - sat_events_z_mm[-1]) <= tol.topo_tol(chord_tol, L):
            sat_events_z_mm[-1] = 0.5 * (sat_events_z_mm[-1] + z)
        else:
            sat_events_z_mm.append(z)
    shape, valid = export.finalize(shape, chord_tol)
    if not valid:
        print("rebuild.py: final solid failed BRepCheck_Analyzer validity check", file=sys.stderr)
        return 5

    shape = export.undo_axis_transform(shape, R_axis)
    export.write_step(shape, args.output, chord_tol)
    if args.stl:
        export.write_stl(shape, args.stl, chord_tol)
    if args.report:
        # Report z-values relative to `axial_origin_z` (the fore dome's theoretical R=0 apex, per
        # the generator's canonical-frame convention -- see the comment where it's solved above),
        # not our internally-arbitrary z=0, so `stations_z_mm`/`topology_events_z_mm` land in the
        # same axial coordinate system the scorer's hidden ground-truth frame uses (MISSION §5.5.1
        # frame normalisation; matters once the input has a nonzero axial origin, e.g. M10).
        topo_events_z_mm = sorted(
            ([zone_fore, zone_aft] if zone_fore is not None
             else ([event_z] if event_z is not None else [])) + sat_events_z_mm
        )
        report.write(
            args.report,
            n_stations=len(zs),
            stations_z_mm=[z - axial_origin_z for z in zs],
            paths_used={
                "outer": "revolve",
                "bore": "mixed" if (bore_rings and bore_pts) else
                         ("prism" if bore_rings else "revolve"),
                **({"slots": paths_slot} if paths_slot else {}),
            },
            topology_events_z_mm=[z - axial_origin_z for z in topo_events_z_mm],
            frame={"axis": info["axis_unit"], "origin_xy_mm": info["origin_xy_mm"],
                   "units": args.units},
            axial_extent_mm=z_max - z_min,
        )
    return 0


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return _run(args)
    except Exception as exc:  # never let the harness see a raw crash (MISSION.md §5.2 step 8)
        tb = traceback.format_exc().splitlines()[-6:]
        print(f"rebuild.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("\n".join(tb), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
