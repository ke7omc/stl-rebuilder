"""CLI entry point. Contract in MISSION.md §5.3.

Currently implements the axisymmetric fast path only (§5.2 step 6, first bullet): one outer
envelope + one bore chain, both circular and centered on the axis, revolved from an RDP-
simplified (R, z) meridian. This covers M1 (annular cylinder). Non-axisymmetric sections,
multiple hole chains, and topology events (chain birth/death) are not yet implemented.
"""
import argparse
import math
import sys
import traceback

import numpy as np

from pipeline import booleans, export, io as pio, report, solids, stations, tol
from pipeline.fitting import fit_circle
from pipeline.slicing import slice_station


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="rebuild.py")
    p.add_argument("input_stl")
    p.add_argument("--axis", default="z")
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


def _densify_dome_chords(outer_pts, z_lo, z_hi, at_start: bool, min_dz: float, resid_tol: float,
                          window_z: float, n_samples: int = 24):
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
    z0, coef, _ = _fit_r2_quadratic(outer_pts, at_start, min_dz, resid_tol)
    z_end = z_lo if at_start else z_hi
    lo, hi = (z_end, window_z) if at_start else (window_z, z_end)
    zs = np.linspace(lo, hi, n_samples)
    return [(float(z), _eval_r2_quadratic(z0, coef, z)) for z in zs]


def _run(args) -> int:
    chord_tol = args.chord_tol
    mesh, R_axis, info = pio.load_and_orient(args.input_stl, args.axis)
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
    station_eps = max(eps_end_val, 200.0 * chord_tol)
    zs = stations.uniform_stations(z_min, z_max, args.sections, station_eps,
                                    vertex_zs=mesh.vertices[:, 2])

    outer_pts = []   # (z, R) of the exterior loop
    bore_pts = []    # (z, R) of the (single) interior loop
    for z in zs:
        polys, zz = slice_station(mesh, z, chord_tol)
        if not polys:
            print(f"rebuild.py: no section recovered at z={z:.3f}", file=sys.stderr)
            return 4
        if len(polys) != 1 or len(polys[0].interiors) != 1:
            print(f"rebuild.py: expected 1 outer loop + 1 hole at z={zz:.3f}, "
                  f"got {len(polys)} outer / "
                  f"{len(polys[0].interiors) if polys else 0} holes (unsupported topology)",
                  file=sys.stderr)
            return 4

        poly = polys[0]
        ext = np.asarray(poly.exterior.coords)
        cx, cy, Ro, max_resid, _ = fit_circle(ext)
        if max_resid > tol.circle_max_resid(chord_tol) or not _axis_centered(cx, cy, Ro, chord_tol):
            print(f"rebuild.py: outer loop at z={zz:.3f} is not an axis-centered circle "
                  f"(max_resid={max_resid:.4f}, center=({cx:.4f},{cy:.4f})) — "
                  f"non-axisymmetric sections not yet implemented", file=sys.stderr)
            return 4
        outer_pts.append((zz, Ro))

        hole = np.asarray(poly.interiors[0].coords)
        cx2, cy2, Ri, max_resid2, _ = fit_circle(hole)
        if max_resid2 > tol.circle_max_resid(chord_tol) or not _axis_centered(cx2, cy2, Ri, chord_tol):
            print(f"rebuild.py: hole loop at z={zz:.3f} is not an axis-centered circle "
                  f"(max_resid={max_resid2:.4f}, center=({cx2:.4f},{cy2:.4f})) — "
                  f"non-axisymmetric sections not yet implemented", file=sys.stderr)
            return 4
        bore_pts.append((zz, Ri))

    # Envelope spans the true axial extent. Extrapolate the outer radius to it from the two
    # nearest fitted stations (linear secant) rather than copying the nearest station's R flat —
    # correct either way for a prismatic profile (M1, zero slope) and far closer for a curved
    # end (M2's domes, see `_extrapolate_end`).
    min_dz = 5.0 * chord_tol
    resid_tol = tol.circle_max_resid(chord_tol)
    if len(outer_pts) < 2:
        r_start, fore_window_z = outer_pts[0][1], z_min
        r_end, aft_window_z = outer_pts[-1][1], z_max
    else:
        r_start, fore_window_z = _extrapolate_end(outer_pts, z_min, True, min_dz, resid_tol)
        r_end, aft_window_z = _extrapolate_end(outer_pts, z_max, False, min_dz, resid_tol)
    # A dome that a straight bore breaks through (M2/M5) pinches to zero annular width exactly
    # at the true mesh extent, i.e. the outer radius there *equals* the bore radius (a bore fit
    # is far more reliable than the outer extrapolation, since it isn't near the dome's steep
    # curvature). Snap to it when the extrapolation already landed close, rather than trusting
    # the extrapolation's residual ~10 mm error verbatim.
    is_pinch_start = abs(r_start - bore_pts[0][1]) < 50.0
    is_pinch_end = abs(r_end - bore_pts[-1][1]) < 50.0
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
        fore_gap = _densify_dome_chords(outer_pts, z_min, None, True, min_dz, resid_tol, fore_window_z)
        fore_gap[0] = (z_min, r_start)  # keep the bore-snapped value, not the model's own fit there
        middle_pts = [p for p in middle_pts if p[0] > fore_window_z]
        curve_windows.append((z_min, fore_window_z))
    if is_pinch_end:
        aft_gap = _densify_dome_chords(outer_pts, None, z_max, False, min_dz, resid_tol, aft_window_z)
        aft_gap[-1] = (z_max, r_end)
        middle_pts = [p for p in middle_pts if p[0] < aft_window_z]
        curve_windows.append((aft_window_z, z_max))
    start_pt = [] if is_pinch_start else [(z_min, r_start)]
    end_pt = [] if is_pinch_end else [(z_max, r_end)]
    outer_full = sorted(start_pt + fore_gap + middle_pts + aft_gap + end_pt, key=lambda p: p[0])
    bore_full = [(z_min - eps_cut_val, bore_pts[0][1])] + bore_pts \
        + [(z_max + eps_cut_val, bore_pts[-1][1])]

    outer_solid = solids.build_revolve_solid(outer_full, chord_tol, curve_windows=curve_windows)
    bore_solid = solids.build_revolve_solid(bore_full, chord_tol)

    shape = booleans.cut(outer_solid, bore_solid, tol.fuzzy(chord_tol))
    shape, valid = export.finalize(shape)
    if not valid:
        print("rebuild.py: final solid failed BRepCheck_Analyzer validity check", file=sys.stderr)
        return 5

    shape = export.undo_axis_transform(shape, R_axis)
    export.write_step(shape, args.output)
    if args.stl:
        export.write_stl(shape, args.stl, chord_tol)
    if args.report:
        report.write(
            args.report,
            n_stations=len(zs),
            stations_z_mm=list(zs),
            paths_used={"outer": "revolve", "bore": "revolve"},
            topology_events_z_mm=[],
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
