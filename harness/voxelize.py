"""Synthesize marching-cubes input meshes from analytic truth solids (harness-only).

MISSION.md §7.2. `synthesize_voxel_input` is the public entry point: given a clean trimesh
tessellation of a truth solid (the harness's own `ct/2` mesh, never the pipeline's output) and
an `InputSpec` (see `harness/milestones.py`), it builds a narrow-band signed-distance grid,
extracts the zero level-set with `skimage.measure.marching_cubes`, and applies pathologies
(noise, flipped facets, disconnected islands, unweld+jitter) in that fixed order with a seeded
RNG so every run is deterministic. Unit scaling (mm -> in) is applied last, separately, by the
caller via `scale=`.

Never run inside the scorer's timed section (MISSION §7.2) -- this is truth-generation-time
work, cached like the rest of `harness/generators.py`.
"""
import numpy as np
import shapely
import trimesh
from scipy.ndimage import distance_transform_edt
from skimage.measure import marching_cubes as _marching_cubes

from harness import metrics


def _occupancy_grid(mesh: trimesh.Trimesh, xs: np.ndarray, ys: np.ndarray,
                     zs: np.ndarray) -> np.ndarray:
    """Boolean occupancy, shape (nx, ny, nz), via z-plane sections + `shapely.contains_xy`.

    Same section-then-project pattern as `pipeline/slicing.py` (z-normal multiplane is the
    trimesh-recommended safe path, see docs/research/01 §`to_2D`), with a small z-jitter retry
    for the rare exact-tangent plane that returns no closed loop.
    """
    X, Y = np.meshgrid(xs, ys, indexing="ij")  # (nx, ny), matches occ[:, :, iz]
    occ = np.zeros((len(xs), len(ys), len(zs)), dtype=bool)
    normal = np.array([0.0, 0.0, 1.0])
    for iz, z in enumerate(zs):
        sec = None
        zz = z
        for jitter in (0.0, 1e-3, -1e-3, 1e-2, -1e-2):
            zz = z + jitter
            sec = mesh.section(plane_origin=[0.0, 0.0, zz], plane_normal=[0.0, 0.0, 1.0])
            if sec is not None:
                break
        if sec is None:
            continue
        to_2D = trimesh.geometry.plane_transform(np.array([0.0, 0.0, zz]), normal)
        planar, _ = sec.to_2D(to_2D=to_2D)
        for poly in planar.polygons_full:
            if poly is None or poly.is_empty:
                continue
            occ[:, :, iz] |= shapely.contains_xy(poly, X, Y)
    return occ


def _grid_axes(mesh: trimesh.Trimesh, spacing: np.ndarray, band: float):
    pad = band + spacing.max()
    bmin, bmax = mesh.bounds
    bmin = bmin - pad
    bmax = bmax + pad
    xs = np.arange(bmin[0], bmax[0] + spacing[0], spacing[0])
    ys = np.arange(bmin[1], bmax[1] + spacing[1], spacing[1])
    zs = np.arange(bmin[2], bmax[2] + spacing[2], spacing[2])
    return xs, ys, zs


def signed_distance_narrow_band(mesh: trimesh.Trimesh, spacing_mm, seed: int = 0):
    """Narrow-band signed distance grid per MISSION §7.2.

    Occupancy per z-plane (fast, approximate boundary location) selects which grid points fall
    within `|d| <= 2h` of the surface (`h = max(spacing)`); those get an *exact* distance from
    `metrics.point_mesh_distance` (bounded-memory KD-tree query, same helper the scorer uses for
    deviation). Everything else is far field at `+-2h`. Sign convention: **inside is positive**.
    `marching_cubes_surface` calls skimage with `gradient_direction="ascent"` to match this
    convention and produce outward-facing normals (empirically verified against a sphere's
    known-outward orientation -- skimage's own docstring labels for "descent"/"ascent" describe
    the opposite of what this sign convention needed; trust the test, not the label).

    Returns `(phi, xs, ys, zs)` with `phi.shape == (len(xs), len(ys), len(zs))`.
    """
    spacing = np.asarray(spacing_mm, dtype=np.float64)
    h = float(spacing.max())
    band = 2.0 * h
    xs, ys, zs = _grid_axes(mesh, spacing, band)

    occ = _occupancy_grid(mesh, xs, ys, zs)

    # Approximate (index-space) distance-to-boundary just to pick the narrow band; anisotropic
    # spacing handled via `sampling=`. Only used for band selection, not the final phi value.
    approx_out = distance_transform_edt(~occ, sampling=spacing)
    approx_in = distance_transform_edt(occ, sampling=spacing)
    approx = np.where(occ, approx_in, approx_out)
    band_mask = approx <= band

    phi = np.where(occ, band, -band).astype(np.float64)
    if band_mask.any():
        ix, iy, iz = np.nonzero(band_mask)
        pts = np.column_stack([xs[ix], ys[iy], zs[iz]])
        d = metrics.point_mesh_distance(mesh, pts, seed=seed)
        d = np.clip(d, 0.0, band)
        signs = np.where(occ[band_mask], 1.0, -1.0)
        phi[band_mask] = signs * d
    return phi, xs, ys, zs


def marching_cubes_surface(mesh: trimesh.Trimesh, spacing_mm, seed: int = 0):
    """Exact-SDF marching-cubes surface of `mesh` on an (anisotropic) grid.

    Returns `(vertices, faces)` in the same coordinate frame as `mesh`.
    """
    spacing = np.asarray(spacing_mm, dtype=np.float64)
    phi, xs, ys, zs = signed_distance_narrow_band(mesh, spacing, seed=seed)
    if phi.min() >= 0 or phi.max() <= 0:
        raise RuntimeError(
            "voxelize.marching_cubes_surface: grid does not straddle the zero level set "
            f"(min={phi.min():.3f}, max={phi.max():.3f}) -- spacing too coarse or mesh outside "
            "the grid bounds")
    verts, faces, _normals, _values = _marching_cubes(phi, level=0.0, spacing=tuple(spacing),
                                                        gradient_direction="ascent")
    verts = verts + np.array([xs[0], ys[0], zs[0]])
    return verts, faces


def _add_normal_noise(mesh: trimesh.Trimesh, sigma_mm: float, rng: np.random.Generator
                       ) -> trimesh.Trimesh:
    normals = mesh.vertex_normals
    offsets = rng.normal(0.0, sigma_mm, size=len(mesh.vertices))
    verts = mesh.vertices + normals * offsets[:, None]
    return trimesh.Trimesh(vertices=verts, faces=mesh.faces, process=False)


def _flip_facets(mesh: trimesh.Trimesh, flip_frac: float, rng: np.random.Generator
                  ) -> trimesh.Trimesh:
    faces = mesh.faces.copy()
    n_flip = int(round(flip_frac * len(faces)))
    if n_flip > 0:
        idx = rng.choice(len(faces), size=n_flip, replace=False)
        faces[idx] = faces[idx][:, [0, 2, 1]]
    return trimesh.Trimesh(vertices=mesh.vertices, faces=faces, process=False)


def _sample_interior_points(reference_mesh: trimesh.Trimesh, n: int,
                             rng: np.random.Generator) -> np.ndarray:
    """`n` points strictly inside `reference_mesh` (the harness's own clean truth tessellation,
    not the pathology-laden output mesh, so `is_watertight`/`contains` stay reliable)."""
    seed = int(rng.integers(0, 2**31 - 1))
    if reference_mesh.is_watertight:
        pts = trimesh.sample.volume_mesh(reference_mesh, n * 4)
        if len(pts) >= n:
            return pts[:n]
    # Fallback: rejection sampling in the bounding box via ray-cast containment.
    bmin, bmax = reference_mesh.bounds
    out = []
    local_rng = np.random.default_rng(seed)
    while len(out) < n:
        cand = local_rng.uniform(bmin, bmax, size=(max(n * 8, 64), 3))
        inside = reference_mesh.contains(cand)
        out.extend(cand[inside].tolist())
    return np.array(out[:n])


def _add_islands(mesh: trimesh.Trimesh, reference_mesh: trimesh.Trimesh, n_islands: int,
                  rng: np.random.Generator, edge_mm: float = 5.0) -> trimesh.Trimesh:
    """Append `n_islands` small disconnected tetrahedra (edge `edge_mm`) at random interior
    points of `reference_mesh` -- MISSION §6.2 M13's "3 noise islands (5 mm tetrahedra inside
    the bore)"; interior sampling is unbiased over the whole solid, the bore just happens to be
    where interior points land for a thin-webbed grain."""
    centers = _sample_interior_points(reference_mesh, n_islands, rng)
    # Regular tetrahedron centered at the origin, edge length `edge_mm`.
    a = edge_mm / np.sqrt(2.0)
    base = np.array([
        [1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1],
    ], dtype=np.float64) * (a / 2.0)
    tet_faces = np.array([[0, 1, 2], [0, 3, 1], [0, 2, 3], [1, 3, 2]])

    verts = [mesh.vertices]
    faces = [mesh.faces]
    offset = len(mesh.vertices)
    for c in centers:
        rot = trimesh.transformations.random_rotation_matrix(rng.random(3))[:3, :3]
        verts.append(base @ rot.T + c)
        faces.append(tet_faces + offset)
        offset += 4
    return trimesh.Trimesh(vertices=np.vstack(verts), faces=np.vstack(faces), process=False)


def _unweld_and_jitter(mesh: trimesh.Trimesh, jitter_mm: float, rng: np.random.Generator
                        ) -> trimesh.Trimesh:
    """Give every face its own private copy of its 3 vertices (breaks all vertex sharing) and
    jitter each copy independently -- "per-facet vertices +- jitter" (MISSION §6.2 M13)."""
    tri = mesh.vertices[mesh.faces].reshape(-1, 3)
    tri = tri + rng.uniform(-jitter_mm, jitter_mm, size=tri.shape)
    faces = np.arange(len(tri)).reshape(-1, 3)
    return trimesh.Trimesh(vertices=tri, faces=faces, process=False)


def synthesize_voxel_input(truth_mesh: trimesh.Trimesh, spec, seed: int = 7,
                            scale: float = 1.0) -> trimesh.Trimesh:
    """Build the pathology-laden input mesh for a `kind="voxel"` `InputSpec`.

    `truth_mesh` must be the harness's own clean tessellation (never the pipeline's output --
    MR/anti-gaming concerns aside, it also needs to be watertight for `_add_islands`/interior
    sampling to be reliable). Fixed pathology order: noise, flipped facets, islands, unweld +
    jitter, then unit scale -- matches MISSION §7.2. Each stage advances a single seeded RNG so
    the whole pipeline is deterministic for a given seed.
    """
    rng = np.random.default_rng(seed)
    verts, faces = marching_cubes_surface(truth_mesh, spec.spacing_mm, seed=seed)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)

    if spec.noise_sigma_mm:
        mesh = _add_normal_noise(mesh, spec.noise_sigma_mm, rng)
    if spec.flip_frac:
        mesh = _flip_facets(mesh, spec.flip_frac, rng)
    if spec.islands:
        mesh = _add_islands(mesh, truth_mesh, spec.islands, rng)
    if spec.unweld_jitter_mm:
        mesh = _unweld_and_jitter(mesh, spec.unweld_jitter_mm, rng)
    if scale != 1.0:
        mesh = trimesh.Trimesh(vertices=mesh.vertices * scale, faces=mesh.faces, process=False)
    return mesh
