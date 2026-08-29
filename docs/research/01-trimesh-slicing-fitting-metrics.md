# Research: trimesh 5.0 slicing, shapely/scipy curve fitting, mesh-vs-mesh metrics

_Verified research dump, 2026-08-28. Treat as reference, not gospel: re-verify any API against the installed package version before relying on it._

FINDINGS — Python STL slicing / 2D-processing layer (verified against trimesh 5.0.0 docs/source, shapely 2.1.1, scipy current, Aug 2026)

=== AREA 1: TRIMESH ===

VERSION/INSTALL
- Current: trimesh 5.0.0 (PyPI). 5.0 dropped py3.8/3.9, modernized type annotations. Requires numpy only at core.
- CRITICAL: bare `pip install trimesh` cannot do path/section→Polygon work. Sectioning to Path2D/shapely requires `shapely` + `networkx`; proximity acceleration requires `rtree`; `scipy` for splines/convex hulls. Install `trimesh[easy]` or explicitly `trimesh shapely networkx scipy rtree`. Host is py3.14.5 — shapely needs py>=3.10 (fine); wheels bundle GEOS 3.13.1; verify cp314 wheels exist at install time, conda-forge is the fallback.
- API-break note: `Path3D.to_planar` was deprecated with "removal 1/1/2026" — in 5.0 use `Path3D.to_2D` only. Training-data code using `to_planar` will break.

LOADING + REPAIR
- `trimesh.load_mesh(path)` or `trimesh.load(path, force='mesh', process=True)` (plain `load` can return a Scene; force='mesh' concatenates). `process=True` (default) runs `process(validate=False)`: removes NaN/Inf + `merge_vertices()`. STL is a triangle soup so merge_vertices is what welds it; merge tolerance = `trimesh.constants.tol.merge = 1e-8` (same value SolidWorks uses), applied via digit-rounding.
- `process(validate: bool = False, merge_tex=None, merge_norm=None) -> Self` — validate=True additionally removes degenerate + duplicate triangles and attempts consistent winding.
- `merge_vertices(merge_tex=None, merge_norm=None, digits_vertex=None, digits_norm=None, digits_uv=None) -> None`.
- `fix_normals(multibody: bool | None = None) -> Self` — consistent outward normals + winding; set `multibody=True` for the nested-cylinder ground truth (mesh.body_count > 1 case).
- `fill_holes() -> bool` — ONLY fills single-triangle and single-quad holes; returns resulting is_watertight. Not a general hole filler; do not rely on it for real repair.
- `is_watertight: bool` (every edge in exactly two faces); `is_volume: bool` (watertight AND consistent winding AND outward normals AND finite). Gate every metric on `is_volume`.
- Other useful: `mesh.euler_number`, `mesh.body_count`, `mesh.split(only_watertight=True)` to separate outer wall from bore body.

SECTIONING — EXACT API (5.0.0 source)
```python
def section(self, plane_normal: ArrayLike, plane_origin: ArrayLike, **kwargs) -> Path3D | None
```
- ARGUMENT-ORDER TRAP: in current source `plane_normal` is FIRST positionally (older docs/examples show plane_origin first). Always call with keywords: `mesh.section(plane_origin=o, plane_normal=n)`.
- Returns `Path3D`, or `None` if the plane misses the mesh. Internally: `intersections.mesh_plane(..., return_faces=True)` → `load_path`/lines_to_path; face indices stored in `path.metadata['face_index']`.

```python
Path3D.to_2D(to_2D: ArrayLike|None = None, normal: ArrayLike|None = None, check: bool = True) -> tuple[Path2D, NDArray[float64]]
```
- Returns `(planar_path2D, to_3D)` where to_3D is the 4x4 homogeneous transform mapping the 2D frame back to the original 3D frame. If `to_2D` not passed it FITS a plane to the vertices — the resulting in-plane x/y axes are arbitrary and differ per station. For lofting you need consistent frames: build one known transform per station yourself (e.g. `trimesh.geometry.plane_transform(origin, normal)` → pass as `to_2D=`) so all sections share x/y axes. `check=True` raises ValueError if vertices aren't coplanar.

```python
def section_multiplane(self, plane_origin: ArrayLike, plane_normal: ArrayLike, heights: ArrayLike) -> list[Path2D | None]
```
- NOTE opposite arg order vs `section` (origin first here). Sections offset by each height along normal; returns Path2D (already 2D) or None per height; `path.metadata['to_3D']` holds the transform back to 3D; `metadata['face_index']` present. Internally `intersections.mesh_multiplane` — one pass, much faster than looping `section`.
- KNOWN BUG HISTORY (issue #743): `to_3D` transforms from section_multiplane contained -1 axis-inversion terms for x-/y-normal planes (z-normal was fine), and sections at heights starting exactly at `mesh.bounds[0]` came back None while `section()` at the same plane worked. Workaround that always behaves: loop `section()` + `to_2D(to_2D=my_transform)`. Since motor axis can be rotated to +Z first (`mesh.apply_transform`), z-normal multiplane is the safe fast path. Verify to_3D round-trip on station 0 in the harness regardless.

POLYGON EXTRACTION (Path2D)
- `polygons_closed` → (n,) array of `shapely.geometry.Polygon`, one per closed circuit in the vertex graph, exterior only, NO interiors, no hole/area semantics. Entries can be None for degenerate geometry.
- `polygons_full` → list, length == len(self.root), of shapely Polygons WITH interiors — built by `polygons.enclosure_tree(self.polygons_closed)` (containment tree; roots = exterior shells, direct children = holes). THIS is the one you want: an annular grain section comes back as ONE Polygon whose `.exterior` is the case wall and `.interiors` list holds the bore loop(s). Entry is None when the root curve's geometry couldn't be recovered. Multiple disjoint outer loops (e.g. multi-segment grain) → multiple roots → multiple entries.
- `root` → indices into paths/polygons_closed that are shells. `Path2D.area` = total area minus interiors (good scalar sanity check per station vs analytic annulus area).
- `discrete` → list of (m,2) float arrays of connected ordered vertices per path (ring arrays, first point == last point) — this is the ordered input for splprep. Alternatively `np.array(poly.exterior.coords)` / `interior.coords` from the shapely objects.
- `Path2D.simplify(**kwargs)` — merges colinear segments and replaces segmented circular arcs with circle entities (uses tol_path.seg_frac=0.125, seg_angle=50°, radius_frac=0.02, radius_min=1e-4, radius_max=50.0, tangent=20° — NOTE radius_max=50.0 is in model units: a 10 m motor in mm has R~O(10^2–10^3 mm), so circle detection will silently refuse; either work in meters or raise `trimesh.constants.tol_path.radius_max`).

GOTCHAS (slicing)
- Plane exactly at an end cap / coplanar faces: `mesh_plane` intersecting faces lying IN the plane yields duplicated/degenerate segments (issue #1745: faces on the splitting plane duplicate geometry) or open/garbage sections. Grazing tangent planes likewise. Mitigation: never slice at z == vertex coordinate; offset first/last stations by eps = ~1e-6 * bbox_extent inward from `mesh.bounds`, and nudge any height that coincides with a unique vertex z-value.
- Non-watertight input: `section` still returns a Path3D of whatever segments exist, but loops won't close → those circuits vanish from `polygons_closed`/`polygons_full` (or come back None). Detect via `path.is_closed` (property: True iff every vertex in the vertex graph has degree 2), `path.dangling` (entities not in any closed path). Repair option: `path.fill_gaps(distance=0.025)` (in-place, connects degree!=2 vertices within distance — default is absolute 0.025 units, scale it).
- Path vertex merging tolerance: `tol_path.merge = 1e-5`, `tol_path.zero = 1e-12` (in `trimesh.constants`); loop closure at section time is sensitive to this if model units are meters and features are sub-mm — consider working in mm, or scale mesh to unit box and back.
- Harness check per station: `len(polys) == expected_bodies`, each `poly.is_valid`, `abs(poly.area - analytic)/analytic < tol`, holes count == expected bore count.

=== AREA 2: LOOP SIMPLIFICATION + CURVE FITTING ===

SHAPELY SIMPLIFY (shapely 2.1.1)
- `shapely.simplify(geometry, tolerance, preserve_topology=True, **kwargs)`; also method form `poly.simplify(tolerance, preserve_topology=True)`. Douglas-Peucker; `tolerance` = maximum allowed displacement of the geometry, ABSOLUTE in coordinate units. `preserve_topology=True` (default) avoids invalid output (collapses, ring self-intersections) at extra cost; `False` uses plain DP and can delete interior rings entirely (documented example: hole disappears). 2.1.0 deprecation: `preserve_topology` must be keyword, not positional. On a Polygon it simplifies exterior AND interiors together. Still assert `.is_valid` and `poly.exterior.is_simple` after; large tolerance on thin star webs will merge slots.

CIRCLE FIT (Kasa algebraic + geometric refine)
```python
import numpy as np
from scipy.optimize import least_squares
def fit_circle(pts):                      # pts (n,2)
    x, y = pts[:,0], pts[:,1]
    A = np.column_stack([x, y, np.ones_like(x)])
    b = x*x + y*y
    (a0, b0, c0), *_ = np.linalg.lstsq(A, b, rcond=None)   # Kasa: x^2+y^2 = a x + b y + c
    cx, cy = a0/2, b0/2
    r = np.sqrt(c0 + cx*cx + cy*cy)
    def resid(p):                                          # geometric refinement
        return np.hypot(x-p[0], y-p[1]) - p[2]
    sol = least_squares(resid, x0=[cx, cy, r])
    cx, cy, r = sol.x
    rms = np.sqrt(np.mean(resid(sol.x)**2))
    return cx, cy, r, rms, np.abs(resid(sol.x)).max()
```
- Kasa alone biases r low on partial arcs; the least_squares refine fixes it. Decision rule for "this loop IS a circle": max |residual| < chord-sag of the STL facets (≈ r - sqrt(r²-(L/2)²) for facet edge length L), else fall through to spline.

PERIODIC B-SPLINE (scipy.interpolate.splprep — FITPACK parametric)
```python
scipy.interpolate.splprep(x, w=None, u=None, ub=None, ue=None, k=3, task=0, s=None,
                          t=None, full_output=0, nest=None, per=0, quiet=1)
# x: list of arrays [xs, ys] (dims < 11); returns (tck, u); tck = (t, c, k)
tck, u = splprep([ring[:,0], ring[:,1]], s=smooth, per=1)
xs, ys = splev(np.linspace(0, 1, 2000), tck)
```
- `s` semantics: constraint `sum((w*(y-g))**2) <= s`. s=0 → interpolation through all points. Guideline range with unit weights: s in (m - sqrt(2m), m + sqrt(2m)) where m = #points — BUT that assumes w = 1/std(noise); practical recipe: w=1, s = m * sigma² with sigma ≈ expected chordal deviation of the STL faceting. s=None defaults to that m-based estimate when w given.
- `per=1`: data treated as periodic with period u[m-1]-u[0]; THE LAST POINT'S y VALUES AND WEIGHTS ARE IGNORED — so pass the ring WITH the duplicated closing point (trimesh `discrete` rings already have first==last), or you drop a real point. k=3 recommended; avoid even k with small s; need m > k.
- Seam/ordering pitfalls: points must be sequentially ordered around the loop (trimesh discrete / shapely coords are; raw segment soup is not — never feed unordered points). Seam location is where u=0: smoothing is C(k-1) across the seam with per=1, so no seam kink, but the PARAMETERIZATION default is normalized cumulative chord length — highly nonuniform point spacing (DP-simplified loops!) distorts u; fit the spline to the RAW dense loop and use DP only for segment-count decisions, or resample uniformly by arc length before fitting.
- Self-intersection detection of the fitted curve: densely sample (>=10x control count), then
```python
from shapely.geometry import LinearRing
ring = LinearRing(np.column_stack([xs, ys]))   # raises/goes invalid on degenerate
ok = ring.is_simple and ring.is_valid          # is_simple False => self-intersecting
```
  or `shapely.is_simple(LineString(pts))`. Star-point concave corners with too-large s are the failure mode: on detect, reduce s (bisect) and refit.

=== AREA 3: MESH-VS-MESH COMPARISON METRICS ===

- `trimesh.proximity.ProximityQuery(mesh)`:
  - `.on_surface(points)` → `(closest (m,3) float, distance (m,) float, triangle_id (m,) int)` — unsigned point-to-surface distance. Needs `rtree` for the r-tree candidate query (`nearby_faces`); `closest_point_naive` is the O(n·m) fallback.
  - `.signed_distance(points)` → (n,) float. SIGN CONVENTION: points OUTSIDE the mesh get NEGATIVE distance, inside positive (opposite of most SDF conventions — don't flip-flop).
  - `.vertex(points)` → `(distance (n,), vertex_id (n,))`.
  - Module-level: `trimesh.proximity.closest_point(mesh, points)`, `signed_distance(mesh, points)`, `nearby_faces(mesh, points)`, `thickness(mesh, points, exterior=False, normals=None, method='max_sphere')`.
- `trimesh.sample.sample_surface(mesh, count, face_weight=None, sample_color=False, seed=None)` → `(points (count,3), face_index (count,))`. Area-weighted by default; pass `seed=` for deterministic harness runs. `sample_surface_even(mesh, count, radius=None, seed=None)` — blue-noise-ish, MAY RETURN FEWER than count (don't assert length).
- Mass properties (valid only if `is_volume`): `mesh.volume: float64` ("garbage" if not watertight — docstring's word), `mesh.center_mass: (3,)`, `mesh.moment_inertia: (3,3)` (about center_mass, axis-aligned with cartesian axes; also `mesh.mass_properties` dict, density via `mesh.density`).

SYMMETRIC HAUSDORFF / RMS RECIPE
```python
def deviation(a, b, n=100_000, seed=0):
    pa, _ = trimesh.sample.sample_surface(a, n, seed=seed)
    pb, _ = trimesh.sample.sample_surface(b, n, seed=seed)
    da = trimesh.proximity.ProximityQuery(b).on_surface(pa)[1]  # a -> b
    db = trimesh.proximity.ProximityQuery(a).on_surface(pb)[1]  # b -> a
    d = np.concatenate([da, db])
    return {"hausdorff_approx": d.max(),          # sampled, understates true HD at sharp features
            "rms": np.sqrt((d**2).mean()),
            "p99": np.quantile(d, 0.99),
            "mean": d.mean()}
```
- Caveats: sampled Hausdorff misses worst-case at edges/points — also feed both meshes' VERTICES through on_surface (vertices hit extremes better than random samples) and take the max over all four sets. Use p99 not max as the pass/fail gate (max is noisy). For the STEP-vs-STL harness, tessellate the exported STEP back to a mesh first, then run this + relative errors: `abs(V1-V2)/V1`, `norm(com1-com2)/bbox_diag`, `norm(I1-I2)/norm(I1)` (inertia compares full shape distribution incl. star lobes; scale-aware).
- Scoring gate order: is_volume both → volume rel-err → deviation dict → CoM/inertia. All deterministic with fixed seeds.

Sources: https://pypi.org/project/trimesh/ | https://trimesh.org/trimesh.base.html | https://trimesh.org/trimesh.path.path.html | https://trimesh.org/trimesh.proximity.html | https://trimesh.org/trimesh.sample.html | https://github.com/mikedh/trimesh (trimesh/base.py, trimesh/path/path.py, trimesh/constants.py @ main) | https://github.com/mikedh/trimesh/issues/743 | https://github.com/mikedh/trimesh/issues/1745 | https://github.com/mikedh/trimesh/issues/962 | https://shapely.readthedocs.io/en/stable/reference/shapely.simplify.html | https://github.com/shapely/shapely/releases | https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.splprep.html