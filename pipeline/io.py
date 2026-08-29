"""Stage 1 (io): load the input STL and orient the motor axis to +Z. MISSION.md §5.2 step 1."""
import numpy as np
import trimesh

_AXIS_MAP = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def parse_axis(axis_arg: str) -> np.ndarray:
    if axis_arg in _AXIS_MAP:
        return np.array(_AXIS_MAP[axis_arg], dtype=float)
    parts = [float(v) for v in axis_arg.split(",")]
    v = np.array(parts, dtype=float)
    return v / np.linalg.norm(v)


def load_and_orient(stl_path: str, axis_arg: str):
    """Returns (mesh, R, info). `R` is the 4x4 transform applied to the mesh to bring the
    motor axis to +Z; its inverse must be applied to the result shape before export."""
    mesh = trimesh.load(stl_path, process=True, force="mesh")
    mesh.merge_vertices()
    mesh.fix_normals()

    axis_vec = parse_axis(axis_arg)
    target = np.array([0.0, 0.0, 1.0])
    if np.allclose(axis_vec, target):
        R = np.eye(4)
    else:
        R = trimesh.geometry.align_vectors(axis_vec, target)
    mesh.apply_transform(R)

    info = {
        "is_watertight": bool(mesh.is_watertight),
        "is_volume": bool(mesh.is_volume),
        "body_count": int(mesh.body_count),
        "bounds": mesh.bounds.tolist(),
    }
    return mesh, R, info
