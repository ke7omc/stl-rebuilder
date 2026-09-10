"""Geometry comparison metrics: volume/CoM/inertia, symmetric surface deviation, STEP round-trip.

All functions operate on already-loaded objects (trimesh.Trimesh / STEP paths) so score.py can
control I/O and error handling. Units: mm. See MISSION.md §7 for the contract and
docs/research/01-trimesh-slicing-fitting-metrics.md for the verified API this is built from.
"""
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopoDS import TopoDS_Shape


def load_mesh(path) -> trimesh.Trimesh:
    """Load an STL/mesh file with the standard repair pipeline (process=True: dedupe NaN/Inf +
    merge_vertices). Caller is responsible for gating on `.is_volume` before trusting mass
    properties (see MISSION.md §7)."""
    mesh = trimesh.load_mesh(str(path), process=True)
    if isinstance(mesh, trimesh.Scene):
        # A multi-body file loads as a Scene, whose .volume/.is_volume do not mean what the
        # gates assume. Concatenate so downstream `is_volume` correctly reports False.
        mesh = mesh.dump(concatenate=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise RuntimeError(f"{path}: loaded as {type(mesh).__name__}, not a triangle mesh")
    return mesh


def volume_com_inertia(mesh_a: trimesh.Trimesh, mesh_b: trimesh.Trimesh) -> Dict:
    """Compare mass properties of two watertight meshes. Both must be `.is_volume` — caller
    gates on that (a "garbage" trimesh volume on a non-volume mesh must never reach here).
    Returns relative errors: volume_err_pct (percent, matches milestone gate units), com_err_frac
    (CoM distance / bbox diagonal of mesh_a), inertia_err_frac (Frobenius-norm relative error).
    """
    if not mesh_a.is_volume:
        raise ValueError("mesh_a is not a valid volume (not watertight/consistently wound)")
    if not mesh_b.is_volume:
        raise ValueError("mesh_b is not a valid volume (not watertight/consistently wound)")

    v_a, v_b = mesh_a.volume, mesh_b.volume
    volume_err_pct = abs(v_a - v_b) / abs(v_a) * 100.0

    diag = float(np.linalg.norm(mesh_a.bounds[1] - mesh_a.bounds[0]))
    com_err_frac = float(np.linalg.norm(mesh_a.center_mass - mesh_b.center_mass) / diag) if diag > 0 else float("nan")

    ia, ib = mesh_a.moment_inertia, mesh_b.moment_inertia
    denom = float(np.linalg.norm(ia))
    inertia_err_frac = float(np.linalg.norm(ia - ib) / denom) if denom > 0 else float("nan")

    return {
        "volume_a": float(v_a),
        "volume_b": float(v_b),
        "volume_err_pct": float(volume_err_pct),
        "com_err_frac": com_err_frac,
        "inertia_err_frac": inertia_err_frac,
    }


#: Upper bound on triangle-candidate pairs materialised at once by `point_mesh_distance`.
#: 2e5 pairs -> ~14 MB for the (n,3,3) triangle array and a few multiples of that in the
#: temporaries inside trimesh.triangles.closest_point, so peak extra RSS stays well under 1 GB.
_MAX_CANDIDATE_PAIRS = 200_000
#: Surface samples used to build the search-radius cloud (on top of the mesh's own vertices).
_RADIUS_CLOUD_POINTS = 100_000


def point_mesh_distance(mesh: trimesh.Trimesh, points: np.ndarray, seed: int = 0) -> np.ndarray:
    """Exact distance from each point to the closest point on `mesh`'s surface, with bounded peak
    memory. Drop-in replacement for `trimesh.proximity.ProximityQuery(mesh).on_surface(points)[1]`.

    Why not use trimesh's own: `nearby_faces` sizes each point's r-tree query box by the distance
    to the nearest mesh *vertex*. OCCT's `BRepMesh` tessellates a ruled/cylindrical face as a few
    strip triangles that span the whole face, so on the M2 truth mesh triangles are up to 9953 mm
    long and the nearest vertex is 2000-4500 mm away. That inflates the query box until it selects
    ~2240 candidate faces per point; trimesh then materialises one (n_pairs, 3, 3) triangle array
    for *all* points at once, which at 130 k query points is ~291 M pairs -> tens of GB. The M0
    selftest was SIGKILLed by the OS there on every run after iteration 10.

    The radius bound is the fix: query the distance to a dense cloud of points that lie *on* the
    surface (the vertices plus `_RADIUS_CLOUD_POINTS` surface samples) instead of the vertices
    alone. Any on-surface point is an upper bound for the distance to the surface, so the box is
    still conservative and the result stays exact, but it shrinks from ~2000 mm to ~13 mm and the
    candidate count drops from ~2240 to ~5 per point. Candidates are then consumed in chunks of at
    most `_MAX_CANDIDATE_PAIRS`, so peak memory is bounded no matter how bad the tessellation is.
    """
    points = np.asarray(points, dtype=np.float64)
    if len(points) == 0:
        return np.empty(0, dtype=np.float64)

    cloud = np.vstack([np.asarray(mesh.vertices, dtype=np.float64),
                       trimesh.sample.sample_surface(mesh, _RADIUS_CLOUD_POINTS, seed=seed)[0]])
    kdtree = cKDTree(cloud)
    rtree = mesh.triangles_tree
    triangles = mesh.triangles.view(np.ndarray)

    out = np.empty(len(points), dtype=np.float64)
    # Radii come from the cloud in one shot (cheap, ~O(n log n)); the candidate lists are what
    # must be chunked, so walk the points in modest blocks and split each block further by the
    # candidate budget.
    block = 4096
    for start in range(0, len(points), block):
        pts = points[start:start + block]
        radii = kdtree.query(pts, workers=-1)[0] + 1e-9
        boxes = np.column_stack([pts - radii[:, None], pts + radii[:, None]])
        cand = [np.fromiter(rtree.intersection(b), dtype=np.int64) for b in boxes]
        counts = np.array([len(c) for c in cand], dtype=np.int64)
        # A box always contains the cloud point that set its radius, and that point lies inside
        # its own triangle's bounding box, so every list is non-empty. Belt and braces:
        if (counts == 0).any():
            raise RuntimeError("point_mesh_distance: empty candidate set (corrupt triangle r-tree)")

        edges = np.searchsorted(np.cumsum(counts), np.arange(0, counts.sum(), _MAX_CANDIDATE_PAIRS))
        edges = np.unique(np.concatenate([edges, [len(pts)]]))
        lo = 0
        for hi in edges:
            hi = int(max(hi, lo + 1))
            sub, sub_counts = cand[lo:hi], counts[lo:hi]
            flat = np.concatenate(sub)
            owner = np.repeat(np.arange(hi - lo), sub_counts)
            query = pts[lo:hi][owner]
            closest = trimesh.triangles.closest_point(triangles[flat], query)
            d = np.linalg.norm(closest - query, axis=1)
            # `owner` is non-decreasing and every group is non-empty, so reduceat gives the
            # per-point minimum directly.
            offsets = np.concatenate([[0], np.cumsum(sub_counts)[:-1]])
            out[start + lo:start + hi] = np.minimum.reduceat(d, offsets)
            lo = hi
            if lo >= len(pts):
                break
    return out


def surface_deviation(
    mesh_a: trimesh.Trimesh,
    mesh_b: trimesh.Trimesh,
    n: int = 100_000,
    seed: int = 0,
    regions: Optional[List] = None,
    z_min: Optional[float] = None,
    z_max: Optional[float] = None,
) -> Dict:
    """Symmetric two-sided deviation between two meshes (MISSION.md §7 / research doc AREA 3).

    Sample `n` surface points on each mesh (fixed seed) PLUS both meshes' vertices (vertices hit
    extremes better than random samples), query point->other-mesh distance in both directions,
    and report max/p99/rms/mean over the pooled distances plus the argmax location.

    If `regions` (a list of objects with `.label`, `.z_frac_lo`, `.z_frac_hi`) and `z_min`/`z_max`
    (the axial extent of the truth solid, mm) are given, also bins the pooled (point, distance)
    pairs by z into a `by_z_bin` table labelled with region names — matches the score.json
    `metrics.by_z_bin` contract.
    """
    sampled_a, _ = trimesh.sample.sample_surface(mesh_a, n, seed=seed)
    sampled_b, _ = trimesh.sample.sample_surface(mesh_b, n, seed=seed)

    pts_a = np.vstack([sampled_a, mesh_a.vertices])
    pts_b = np.vstack([sampled_b, mesh_b.vertices])

    d_a_to_b = point_mesh_distance(mesh_b, pts_a, seed=seed)
    d_b_to_a = point_mesh_distance(mesh_a, pts_b, seed=seed)

    all_pts = np.vstack([pts_a, pts_b])
    all_d = np.concatenate([d_a_to_b, d_b_to_a])

    argmax_idx = int(np.argmax(all_d))
    argmax_xyz = all_pts[argmax_idx].tolist()

    result: Dict = {
        "max_mm": float(all_d.max()),
        "p99_mm": float(np.quantile(all_d, 0.99)),
        "rms_mm": float(np.sqrt(np.mean(all_d ** 2))),
        "mean_mm": float(all_d.mean()),
        "argmax_xyz_mm": argmax_xyz,
        "argmax_z_mm": float(argmax_xyz[2]),
    }

    if regions is not None and z_min is not None and z_max is not None:
        L = z_max - z_min
        by_z_bin = []
        z_col = all_pts[:, 2]
        for rb in regions:
            z0 = z_min + rb.z_frac_lo * L
            z1 = z_min + rb.z_frac_hi * L
            mask = (z_col >= z0) & (z_col <= z1)
            if mask.any():
                d_bin = all_d[mask]
                by_z_bin.append({
                    "z0": float(z0), "z1": float(z1), "region": rb.label,
                    "max_mm": float(d_bin.max()), "p99_mm": float(np.quantile(d_bin, 0.99)),
                    "n_points": int(mask.sum()),
                })
            else:
                by_z_bin.append({
                    "z0": float(z0), "z1": float(z1), "region": rb.label,
                    "max_mm": None, "p99_mm": None, "n_points": 0,
                })
        result["by_z_bin"] = by_z_bin

    return result


def radius_profile(mesh: trimesh.Trimesh, z_min: float, z_max: float, n_bins: int = 1000):
    """Silhouette radius profile r(z) = max sqrt(x²+y²) over the mesh's vertices, binned in z.

    Returns (z_centers, r_max) as float arrays of length `n_bins`. Empty bins (possible only on a
    very coarse mesh) are filled by linear interpolation from their populated neighbours so the
    profile is always usable.
    """
    v = np.asarray(mesh.vertices, dtype=float)
    r = np.hypot(v[:, 0], v[:, 1])
    span = z_max - z_min
    if span <= 0:
        raise ValueError("z_max must exceed z_min")
    idx = np.clip(((v[:, 2] - z_min) / span * n_bins).astype(int), 0, n_bins - 1)

    prof = np.zeros(n_bins)
    np.maximum.at(prof, idx, r)
    populated = np.bincount(idx, minlength=n_bins) > 0
    z_centers = z_min + (np.arange(n_bins) + 0.5) * span / n_bins
    if not populated.all():
        if not populated.any():
            raise ValueError("radius profile has no populated bins")
        prof = np.interp(z_centers, z_centers[populated], prof[populated])
    return z_centers, prof


def _polyline_max_dist(pts: np.ndarray, poly: np.ndarray, block: int = 128) -> float:
    """Max over `pts` of the perpendicular distance to the polyline `poly`, both (N,2) in the
    meridian (z, r) plane.

    Perpendicular — not radial — distance is the whole point. At a dome apex the meridian has a
    vertical tangent in r(z), so |Δr| diverges there while the actual surface deviation stays
    small; measuring radially would demand thousands of stations to resolve a feature the
    deviation gate does not care about. Blocked over points to bound peak memory.
    """
    a, b = poly[:-1], poly[1:]
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    denom = np.where(denom == 0.0, 1e-30, denom)
    worst = 0.0
    for i in range(0, len(pts), block):
        chunk = pts[i:i + block]
        ap = chunk[:, None, :] - a[None, :, :]
        t = np.clip(np.einsum("nmj,mj->nm", ap, ab) / denom, 0.0, 1.0)
        proj = a[None, :, :] + t[:, :, None] * ab[None, :, :]
        d = np.linalg.norm(chunk[:, None, :] - proj, axis=2).min(axis=1)
        worst = max(worst, float(d.max()))
    return worst


def uniform_stations_needed(mesh: trimesh.Trimesh, z_min: float, z_max: float, tol: float,
                            max_n: int = 2048, n_bins: int = 8192) -> int:
    """Smallest number of *uniformly spaced* stations whose piecewise-linear interpolation of the
    silhouette profile r(z) stays within `tol` mm of the true profile.

    This is the baseline for MISSION §6's M5 adaptive-efficiency gate ("stations used <= 0.5 x the
    uniform count needed to hit the same deviation gate"). It is computed by bisection on the
    *truth geometry* rather than by re-running the pipeline at many station counts: the geometric
    answer is deterministic, costs milliseconds, and cannot make the frozen scorer time out or
    stall the loop — whereas a pipeline-driven bisection would add several minutes and a failure
    mode with no legal fix once harness/ is frozen.
    """
    # The reference profile must stay finer than the densest candidate station grid, or err(n)
    # is measured against a curve coarser than the thing being tested and reads too low.
    z_centers, prof = radius_profile(mesh, z_min, z_max, n_bins=n_bins)
    truth_pts = np.column_stack([z_centers, prof])

    def err(n: int) -> float:
        zs = np.linspace(z_min, z_max, n)
        poly = np.column_stack([zs, np.interp(zs, z_centers, prof)])
        return _polyline_max_dist(truth_pts, poly)

    n = 4
    while n <= max_n and err(n) >= tol:
        n *= 2
    if n > max_n:
        return max_n
    lo, hi = max(2, n // 2), n
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if err(mid) < tol:
            hi = mid
        else:
            lo = mid
    return hi


def read_step(path) -> Tuple[TopoDS_Shape, float]:
    """Read a STEP file via STEPControl_Reader and compute its kernel-exact volume with
    BRepGProp (docs/research/02-…md §5 pattern). Raises RuntimeError if the read fails or
    transfers zero roots — callers must catch this as a `step_roundtrip` check failure, not let
    it propagate (MISSION.md §7: harness must never crash on a pipeline bug)."""
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"STEP read failed (status={status}): {path}")
    n_roots = reader.TransferRoots()
    if n_roots < 1:
        raise RuntimeError(f"STEP file transferred 0 roots: {path}")
    shape = reader.OneShape()

    vprops = GProp_GProps()
    # Tight adaptive-integration epsilon (tapered_bore_dome_pinch_and_surface_area.md, M16 fix,
    # 2026-09-09): the plain 3-arg `BRepGProp.VolumeProperties_s(shape, vprops)` call every
    # earlier milestone's simpler surfaces (revolves, prisms, ruled/fillet lofts of exact analytic
    # circles) integrate accurately by default, but M16's actual-rings loft introduces this
    # project's first HIGH-DEGREE (8) B-spline lateral surface -- and the default epsilon under-
    # integrates it, measured directly: default call gave 25,364,879,899 mm^3 on a shape whose
    # OWN re-tessellated mesh volume (an independent cross-check) is 26,194,033,660 mm^3 -- a
    # spurious 3.1% "volume error" that is purely a numerical-integration artifact, not a real
    # geometric defect (verified separately: per-station cross-section area agrees with the
    # truth mesh to <0.04% everywhere along the part). The epsilon-taking overload's adaptive
    # integration at `1e-6` recovers 26,195,600,691 mm^3 (matching within 0.006%) on the exact
    # same shape. This can only ever IMPROVE accuracy for every earlier milestone's simpler
    # surfaces (which were already accurate at the default epsilon, so tightening it changes
    # nothing there) while fixing the one shape class that needs it.
    BRepGProp.VolumeProperties_s(shape, vprops, 1e-6)
    volume = vprops.Mass()
    return shape, volume


def step_roundtrip_check(step_path, reference_volume: float) -> Dict:
    """Re-import a STEP file and compare its BRepGProp volume against `reference_volume` (mm³ —
    the volume BRepGProp computed on the in-memory shape before it was exported). Implements the
    universal gate 'STEP re-imports with |ΔV|/V < 1e-6' (MISSION.md §6). Never raises — a read
    failure is reported as a failed check so score.py's fail-fast contract holds."""
    try:
        _, volume = read_step(step_path)
    except Exception as e:
        return {"ok": False, "reason": str(e), "rel_vol_err": None, "volume": None}

    rel_err = abs(volume - reference_volume) / abs(reference_volume) if reference_volume else float("inf")
    return {"ok": True, "rel_vol_err": float(rel_err), "volume": float(volume)}
