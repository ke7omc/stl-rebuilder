"""CLI entry point. Contract in MISSION.md §5.3.

Currently implements the axisymmetric fast path only (§5.2 step 6, first bullet): one outer
envelope + one bore chain, both circular and centered on the axis, revolved from an RDP-
simplified (R, z) meridian. This covers M1 (annular cylinder). Non-axisymmetric sections,
multiple hole chains, and topology events (chain birth/death) are not yet implemented.
"""
import argparse
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


def _extrapolate_end(pts, target_z: float, at_start: bool) -> float:
    """Linear extrapolation of R at target_z from the two nearest fitted stations.

    A flat copy of the nearest station's R (M1's approach) is only correct for a prismatic
    profile. On a curved end (M2's ellipsoidal dome) the true radius keeps changing all the way
    to the true axial extent — R does not even reach 0 there when a straight bore intersects the
    dome first (the solid pinches out where R_dome(z) == R_bore, a finite radius, not an apex).
    A 2-point secant projected to target_z tracks that taper far better than a flat copy; clamped
    at 0 because a negative extrapolated radius is never physically meaningful.
    """
    (z0, r0), (z1, r1) = (pts[0], pts[1]) if at_start else (pts[-1], pts[-2])
    if abs(z1 - z0) < 1e-12:
        return r0
    slope = (r1 - r0) / (z1 - z0)
    return max(0.0, r0 + slope * (target_z - z0))


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

    zs = stations.uniform_stations(z_min, z_max, args.sections, eps_end_val,
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
    r_start = outer_pts[0][1] if len(outer_pts) < 2 else _extrapolate_end(outer_pts, z_min, True)
    r_end = outer_pts[-1][1] if len(outer_pts) < 2 else _extrapolate_end(outer_pts, z_max, False)
    outer_full = [(z_min, r_start)] + outer_pts + [(z_max, r_end)]
    bore_full = [(z_min - eps_cut_val, bore_pts[0][1])] + bore_pts \
        + [(z_max + eps_cut_val, bore_pts[-1][1])]

    outer_solid = solids.build_revolve_solid(outer_full, chord_tol)
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
