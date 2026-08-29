"""Stage 3 (slicing): section the mesh at a station z. MISSION.md §5.2 step 3."""
import numpy as np
import trimesh


def slice_station(mesh, z: float, chord_tol: float, retries: int = 3):
    """Returns (list[shapely.Polygon with holes], actual_z). Retries with a growing jitter on
    an open/garbage section (§5.2 step 3, §10 failure mode 2)."""
    normal = np.array([0.0, 0.0, 1.0])
    to_2D = trimesh.geometry.plane_transform(np.array([0.0, 0.0, z]), normal)

    for attempt in range(retries + 1):
        zz = z if attempt == 0 else z + attempt * 0.1 * chord_tol
        sec = mesh.section(plane_origin=[0.0, 0.0, zz], plane_normal=[0.0, 0.0, 1.0])
        if sec is None:
            continue
        planar, _ = sec.to_2D(to_2D=to_2D)
        polys = [p for p in planar.polygons_full if p is not None]
        if polys:
            return polys, zz
    return [], z
