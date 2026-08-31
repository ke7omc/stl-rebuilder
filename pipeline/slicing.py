"""Stage 3 (slicing): section the mesh at a station z. MISSION.md §5.2 step 3."""
import numpy as np
import trimesh
from shapely.geometry import Polygon

from . import tol


def _bbox_thickness(coords) -> float:
    arr = np.asarray(coords)
    return float(min(arr[:, 0].max() - arr[:, 0].min(), arr[:, 1].max() - arr[:, 1].min()))


def _is_sliver(coords, a_min: float, thick_min: float) -> bool:
    return Polygon(coords).area < a_min or _bbox_thickness(coords) < thick_min


def slice_station(mesh, z: float, chord_tol: float, retries: int = 3):
    """Returns (list[shapely.Polygon with holes], actual_z). Retries with a growing jitter on
    an open/garbage section (§5.2 step 3, §10 failure mode 2).

    Filters slivers per §5.2 step 3 / §5.4 `tol.a_min` (area < A_min or bbox thickness
    < 3*chord_tol): a noisy input (M9's Gaussian-perturbed marching-cubes surface) throws off a
    handful of sub-mm² disjoint loops per station that are not part of the true cross-section --
    confirmed on M9 at z=5854.415, 8 spurious loops of area 0.09-19.4 mm^2 alongside the one true
    ~2e6 mm^2 outer polygon (with its 9 true interiors already correctly nested by shapely).
    A_min = pi*(5*chord_tol)^2 is >100x the observed noise-loop area and orders of magnitude
    below every real feature in MISSION.md's milestone table (smallest is M7's r=100 satellite,
    area ~31400 mm^2), so it cannot mask a genuine small feature.
    """
    normal = np.array([0.0, 0.0, 1.0])
    to_2D = trimesh.geometry.plane_transform(np.array([0.0, 0.0, z]), normal)
    a_min = tol.a_min(chord_tol)
    thick_min = 3.0 * chord_tol

    for attempt in range(retries + 1):
        zz = z if attempt == 0 else z + attempt * 0.1 * chord_tol
        sec = mesh.section(plane_origin=[0.0, 0.0, zz], plane_normal=[0.0, 0.0, 1.0])
        if sec is None:
            continue
        planar, _ = sec.to_2D(to_2D=to_2D)
        raw_polys = [p for p in planar.polygons_full if p is not None]
        polys = []
        for p in raw_polys:
            if _is_sliver(p.exterior.coords, a_min, thick_min):
                continue
            interiors = [r for r in p.interiors if not _is_sliver(r.coords, a_min, thick_min)]
            if len(interiors) != len(p.interiors):
                p = Polygon(p.exterior.coords, [r.coords for r in interiors])
            polys.append(p)
        if polys:
            return polys, zz
    return [], z
