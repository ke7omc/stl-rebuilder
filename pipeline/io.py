"""Stage 1 (io): load the input STL and orient the motor axis to +Z. MISSION.md §5.2 step 1."""
import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

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
    are closest to each other; the axis is the remaining (odd-one-out) eigenvector.

    `eigh` returns each eigenvector with an ARBITRARY sign, and nothing in a grain's geometry
    says which end is fore -- but the reported axis fixes the direction every axial coordinate in
    the report (`stations_z_mm`, `topology_events_z_mm`) is measured along, so an unstable sign
    silently mirrors the whole report. Canonicalise it the standard way: make the
    largest-magnitude component positive (ties -> lowest index). Both auto-axis milestones then
    resolve to +x deterministically instead of by eigensolver luck."""
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
    axis_vec = axis_vec / np.linalg.norm(axis_vec)
    if axis_vec[int(np.argmax(np.abs(axis_vec)))] < 0.0:
        axis_vec = -axis_vec
    return axis_vec


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


def _weld_by_radius(mesh: "trimesh.Trimesh", tol_mm: float = 2e-3) -> "trimesh.Trimesh":
    """Re-weld vertices that are within `tol_mm` of each other in true Euclidean distance
    (union-find over a KD-tree radius query), instead of trimesh's default per-axis coordinate
    rounding (`merge_vertices(digits_vertex=...)`). Rounding-based merge has a real failure
    mode found live on M13's input: an "unwelded" marching-cubes STL's per-facet vertex
    duplicates aren't only ~1e-5 mm apart as intentionally jittered -- round-tripping through
    binary STL's float32 vertex encoding *itself* quantizes coordinates to their local ULP,
    which for this part's ~1e4 mm coordinate magnitudes is ~1e-3 mm, an order of magnitude
    *coarser* than the intentional jitter. Duplicates landing on either side of a rounding-grid
    boundary at that scale silently fail to merge (measured live: still 441k/8.2M unmatched
    boundary edges at a 1e-3 mm rounding tolerance) -- a true-distance union-find has no grid to
    straddle and merges every such duplicate cleanly (measured live: 0 boundary edges at
    tol_mm=1e-3). `tol_mm` stays far below any real feature (islands are 5 mm tetrahedra)."""
    verts = mesh.vertices
    faces = mesh.faces
    tree = cKDTree(verts)
    pairs = tree.query_pairs(r=tol_mm, output_type="ndarray")
    n = len(verts)
    if len(pairs) == 0:
        return mesh
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    uniq, inverse = np.unique(labels, return_inverse=True)
    new_verts = np.zeros((len(uniq), 3))
    group_counts = np.zeros(len(uniq))
    np.add.at(new_verts, labels, verts)
    np.add.at(group_counts, labels, 1)
    new_verts /= group_counts[:, None]
    new_faces = inverse[faces]
    return trimesh.Trimesh(vertices=new_verts, faces=new_faces, process=False)


def _drop_small_islands(mesh: "trimesh.Trimesh", min_frac: float = 0.02):
    """MISSION §6.2 M13: a real-STL marching-cubes input carries small disconnected noise
    islands (isolated tetrahedra) alongside the main body -- `n_solids = 1 (islands dropped)`
    is the spec, so these must never reach the multi-body path. Split into connected
    components and keep only ones whose bbox diagonal is a substantial fraction of the
    largest component's -- this discards noise islands (orders of magnitude smaller than the
    part) while preserving genuinely-multi-body input (M11's segmented grain, whose segments
    are comparable in size to each other, well above `min_frac`). Returns (mesh, n_dropped)."""
    bodies = mesh.split(only_watertight=False)
    if len(bodies) <= 1:
        return mesh, 0
    diags = [float(np.linalg.norm(b.bounds[1] - b.bounds[0])) for b in bodies]
    max_diag = max(diags)
    kept = [b for b, d in zip(bodies, diags) if d >= min_frac * max_diag]
    dropped = len(bodies) - len(kept)
    if dropped == 0:
        return mesh, 0
    merged = trimesh.util.concatenate(kept) if len(kept) > 1 else kept[0]
    return merged, dropped


def load_and_orient(stl_path: str, axis_arg: str, units_arg: str = "mm", chord_tol: float = 0.5,
                    on_progress=None):
    """Returns (mesh, F, info). `F` is the 4x4 rotation+translation transform applied to the
    (already unit-converted) mesh to bring the motor axis to +Z through the origin; its inverse
    must be applied to the result shape before export (unit conversion is NOT part of `F` --
    the exported STEP is always mm, so that scaling is never undone).

    `on_progress(frac, message)`, when given, is called at each major step (reading the file,
    repairing/welding, orienting, refining the origin) -- this is the ONLY instrumentation
    possible here: each step is a single call into trimesh/numpy with no internal progress hook
    of its own, so these are coarse checkpoints, not a fine-grained loop. On a huge STL (M13:
    265 MB) `trimesh.load` alone can run tens of seconds with nothing to report in between --
    the GUI dial's own "creep" animation (app/dashboard.py) is what fills that gap visibly,
    these checkpoints are just where the needle gets re-anchored to real progress."""
    def _progress(frac, message):
        if on_progress is not None:
            on_progress(frac, message)

    _progress(0.0, "reading STL file")
    mesh = trimesh.load(stl_path, process=True, force="mesh")

    scale = parse_units(units_arg)
    if scale != 1.0:
        mesh.apply_scale(scale)

    # `process=True`'s implicit merge_vertices() rounds at trimesh's default 1e-8 (absolute,
    # now in mm since scale was already applied above) -- far tighter than a marching-cubes
    # exporter's legitimate per-facet vertex jitter can be (MISSION M13: +-1e-5 mm), which
    # leaves every triangle its own disconnected "body" (seen live: body_count=125791 on
    # M13's real input). Re-weld with a true-distance union-find (see `_weld_by_radius`) --
    # coordinate-rounding merge was tried first and left hundreds of thousands of boundary
    # edges even at generous tolerances, root-caused to binary STL's float32 export
    # quantization (~1e-3 mm ULP at this part's coordinate magnitude) straddling rounding-grid
    # boundaries independently of the mesh's own jitter.
    _progress(0.5, "repairing and welding mesh")
    mesh = _weld_by_radius(mesh, tol_mm=2e-3)
    mesh, n_dropped_islands = _drop_small_islands(mesh)
    mesh.fix_normals(multibody=True)

    _progress(0.75, "detecting motor axis")
    axis_vec = parse_axis(axis_arg, mesh)
    target = np.array([0.0, 0.0, 1.0])
    if np.allclose(axis_vec, target):
        R1 = np.eye(4)
    else:
        R1 = trimesh.geometry.align_vectors(axis_vec, target)
    mesh.apply_transform(R1)

    _progress(0.9, "refining axis origin")
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
        "n_dropped_islands": n_dropped_islands,
    }
    return mesh, F, info
