"""Stage 8 (export). MISSION.md §5.2 step 8."""
import numpy as np
from OCP.ShapeFix import ShapeFix_Shape
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.gp import gp_Trsf
from OCP.TopoDS import TopoDS_Shape


def finalize(shape: TopoDS_Shape):
    """ShapeFix -> UnifySameDomain -> validity check. Returns (shape, is_valid).

    UnifySameDomain occasionally breaks manifoldness instead of just simplifying face count —
    observed on M5's double-fuse (two topology-event seams close together): the fixed,
    pre-unify shape was valid, unify's output was not. Since unify is a pure simplification
    step (merges coincident-domain faces/edges, changes no geometry), a shape it invalidates is
    worse than the one going in, so fall back to the pre-unify shape rather than propagate the
    corruption; the only cost is a few extra faces (`face_count_max` is a generous anti-
    tessellation guard, not a style gate)."""
    fixer = ShapeFix_Shape(shape)
    fixer.Perform()
    shape = fixer.Shape()
    if not BRepCheck_Analyzer(shape).IsValid():
        return shape, False

    unify = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    unify.Build()
    unified = unify.Shape()

    if BRepCheck_Analyzer(unified).IsValid():
        return unified, True
    return shape, True


def undo_axis_transform(shape: TopoDS_Shape, R: np.ndarray) -> TopoDS_Shape:
    """`R` is the 4x4 forward transform (original axis -> +Z) applied in pipeline.io. Apply its
    inverse to the result shape so the exported STEP is back in the input's original frame."""
    if np.allclose(R, np.eye(4)):
        return shape
    m = np.linalg.inv(R)
    trsf = gp_Trsf()
    trsf.SetValues(m[0, 0], m[0, 1], m[0, 2], m[0, 3],
                   m[1, 0], m[1, 1], m[1, 2], m[1, 3],
                   m[2, 0], m[2, 1], m[2, 2], m[2, 3])
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def write_step(shape: TopoDS_Shape, path: str) -> None:
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(str(path))


def write_stl(shape: TopoDS_Shape, path: str, chord_tol: float) -> None:
    BRepMesh_IncrementalMesh(shape, chord_tol, False, 0.3, True).Perform()
    StlAPI_Writer().Write(shape, str(path))
