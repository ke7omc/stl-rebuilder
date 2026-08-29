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

    _, d_a_to_b, _ = trimesh.proximity.ProximityQuery(mesh_b).on_surface(pts_a)
    _, d_b_to_a, _ = trimesh.proximity.ProximityQuery(mesh_a).on_surface(pts_b)

    all_pts = np.vstack([pts_a, pts_b])
    all_d = np.concatenate([np.asarray(d_a_to_b), np.asarray(d_b_to_a)])

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
    BRepGProp.VolumeProperties_s(shape, vprops)
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
