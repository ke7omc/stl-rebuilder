"""Stage 1 (io): load the input STL and orient the motor axis to +Z. MISSION.md §5.2 step 1."""
import numpy as np
import trimesh

_AXIS_MAP = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
_UNITS_TO_MM = {"mm": 1.0, "in": 25.4, "m": 1000.0}


def parse_units(units_arg: str) -> float:
    """Returns the scale factor to convert the input STL's coordinates to millimetres."""
    try:
        return _UNITS_TO_MM[units_arg]
    except KeyError:
        raise ValueError(f"unknown --units {units_arg!r}; expected one of {sorted(_UNITS_TO_MM)}")


def _auto_axis(mesh) -> np.ndarray:
    """MISSION.md §5.5.1: principal direction of the surface-area-weighted covariance of the
    mesh, choosing the *distinct* eigenvalue -- not always the largest or the smallest, since a
    short fat grain's long-axis variance can be the SMALLEST of the three (the two in-plane,
    axisymmetric directions tie for largest). The axisymmetric pair is whichever two eigenvalues
    are closest to each other; the axis is the remaining (odd-one-out) eigenvector."""
    centers = mesh.triangles_center
    areas = mesh.area_faces
    total = areas.sum()
    mean = (centers * areas[:, None]).sum(axis=0) / total
    diffs = centers - mean
    cov = (diffs * areas[:, None]).T @ diffs / total
    eigvals, eigvecs = np.linalg.eigh(cov)
    gaps = [abs(eigvals[0] - eigvals[1]), abs(eigvals[0] - eigvals[2]), abs(eigvals[1] - eigvals[2])]
    closest_pair = [(0, 1), (0, 2), (1, 2)][int(np.argmin(gaps))]
    distinct_idx = ({0, 1, 2} - set(closest_pair)).pop()
    axis_vec = eigvecs[:, distinct_idx]
    return axis_vec / np.linalg.norm(axis_vec)


def parse_axis(axis_arg: str, mesh=None) -> np.ndarray:
    if axis_arg == "auto":
        return _auto_axis(mesh)
    if axis_arg in _AXIS_MAP:
        return np.array(_AXIS_MAP[axis_arg], dtype=float)
    parts = [float(v) for v in axis_arg.split(",")]
    v = np.array(parts, dtype=float)
    return v / np.linalg.norm(v)


def _outer_circle_fits(mesh, chord_tol: float, fracs) -> list:
    """Circle-fits the outer loop at each z fraction of the current mesh's z-extent (mesh must
    already be roughly axis-aligned to +Z). Returns a list of (z, cx, cy) for every fraction
    where a section and a circle fit were both recoverable."""
    from . import fitting, slicing

    z_min, z_max = mesh.bounds[0, 2], mesh.bounds[1, 2]
    span = z_max - z_min
    if span <= 0:
        return []
    out = []
    for f in fracs:
        z = z_min + f * span
        polys, zz = slicing.slice_station(mesh, z, chord_tol)
        if not polys:
            continue
        outer = max(polys, key=lambda p: p.area)
        pts = np.asarray(outer.exterior.coords)
        try:
            cx, cy, _, _, _ = fitting.fit_circle(pts)
        except Exception:
            continue
        out.append((zz, cx, cy))
    return out


def _axis_origin_refine(mesh, chord_tol: float):
    """MISSION.md §5.5.1: origin = centroid of outer-loop circle fits at ~5 coarse stations. A
    single PCA pass (`_auto_axis`) only gets the motor axis right to within its numerical/
    symmetry noise floor -- for a long part even a ~0.02 degree residual tilt shows up as a
    systematic per-station drift in the fitted outer-loop center (drift ~= span * tan(tilt),
    which is easily larger than the tight axis-centered budget downstream stations are checked
    against). Correct for that by fitting a line to (z, cx, cy) across several stations: its
    slope IS the residual tilt (in the current, already-once-rotated frame), so a second small
    rotation removes it; only then is a plain average of the (now z-independent) centers precise
    enough to use as the origin translation. Returns (R2, ox, oy) where `R2` is the secondary
    4x4 rotation to apply (about the origin, in the current frame) and `(ox, oy)` is the origin
    offset to translate out AFTER `R2` is applied."""
    fracs = np.linspace(0.1, 0.9, 9)
    fits = _outer_circle_fits(mesh, chord_tol, fracs)
    if len(fits) < 3:
        ox, oy = (fits[0][1], fits[0][2]) if fits else (0.0, 0.0)
        return np.eye(4), ox, oy

    arr = np.array(fits)
    zs, cxs, cys = arr[:, 0], arr[:, 1], arr[:, 2]
    bx, ax = np.polyfit(zs, cxs, 1)
    by, ay = np.polyfit(zs, cys, 1)

    R2 = np.eye(4)
    if abs(bx) > 1e-9 or abs(by) > 1e-9:
        tilt_vec = np.array([bx, by, 1.0])
        tilt_vec /= np.linalg.norm(tilt_vec)
        R2 = trimesh.geometry.align_vectors(tilt_vec, np.array([0.0, 0.0, 1.0]))
        mesh.apply_transform(R2)
        fits = _outer_circle_fits(mesh, chord_tol, fracs)

    if not fits:
        return R2, 0.0, 0.0
    arr = np.array(fits)
    return R2, float(arr[:, 1].mean()), float(arr[:, 2].mean())


def load_and_orient(stl_path: str, axis_arg: str, units_arg: str = "mm", chord_tol: float = 0.5):
    """Returns (mesh, F, info). `F` is the 4x4 rotation+translation transform applied to the
    (already unit-converted) mesh to bring the motor axis to +Z through the origin; its inverse
    must be applied to the result shape before export (unit conversion is NOT part of `F` --
    the exported STEP is always mm, so that scaling is never undone)."""
    mesh = trimesh.load(stl_path, process=True, force="mesh")
    mesh.merge_vertices()
    mesh.fix_normals()

    scale = parse_units(units_arg)
    if scale != 1.0:
        mesh.apply_scale(scale)

    axis_vec = parse_axis(axis_arg, mesh)
    target = np.array([0.0, 0.0, 1.0])
    if np.allclose(axis_vec, target):
        R1 = np.eye(4)
    else:
        R1 = trimesh.geometry.align_vectors(axis_vec, target)
    mesh.apply_transform(R1)

    R2, ox, oy = _axis_origin_refine(mesh, chord_tol)
    T = np.eye(4)
    T[0, 3] = -ox
    T[1, 3] = -oy
    if not np.allclose(T, np.eye(4)):
        mesh.apply_transform(T)
    F = T @ R2 @ R1

    # The motor axis as a unit vector in the input STL's own (unit-converted-to-mm, otherwise
    # untouched) coordinate frame -- MISSION §7.2's `report["frame"]["axis"]` contract. `F`'s
    # rotation part carries +Z back to this axis, since F's linear part is orthogonal.
    axis_in_input_frame = F[:3, :3].T @ np.array([0.0, 0.0, 1.0])

    info = {
        "is_watertight": bool(mesh.is_watertight),
        "is_volume": bool(mesh.is_volume),
        "body_count": int(mesh.body_count),
        "bounds": mesh.bounds.tolist(),
        "units": units_arg,
        "origin_xy_mm": [ox, oy],
        "axis_unit": axis_in_input_frame.tolist(),
    }
    return mesh, F, info
