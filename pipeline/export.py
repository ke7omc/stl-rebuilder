"""Stage 8 (export). MISSION.md §5.2 step 8."""
import numpy as np
from OCP.ShapeFix import ShapeFix_Shape, ShapeFix_FixSmallFace
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType, STEPControl_Reader
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.gp import gp_Trsf
from OCP.TopoDS import TopoDS_Shape


def _debug_dump_brepcheck(shape: TopoDS_Shape) -> None:
    """Temporary diagnostic (REBUILD_DEBUG_BREPCHECK=1): report which sub-shapes and defect
    types BRepCheck_Analyzer flags, since IsValid() alone gives no localisation."""
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_SOLID, TopAbs_SHELL, TopAbs_WIRE
    analyzer = BRepCheck_Analyzer(shape)
    for kind, name in ((TopAbs_SOLID, "SOLID"), (TopAbs_SHELL, "SHELL"),
                        (TopAbs_FACE, "FACE"), (TopAbs_WIRE, "WIRE"), (TopAbs_EDGE, "EDGE")):
        exp = TopExp_Explorer(shape, kind)
        seen = set()
        n_total = 0
        n_bad = 0
        while exp.More():
            sub = exp.Current()
            key = sub.HashCode(1 << 30) if hasattr(sub, "HashCode") else id(sub)
            if key not in seen:
                seen.add(key)
                n_total += 1
                if not analyzer.IsValid(sub):
                    n_bad += 1
                    if n_bad <= 5:
                        result = analyzer.Result(sub)
                        statuses = [str(st) for st in result.Status()]
                        print(f"REBUILD_DEBUG_BREPCHECK: bad {name} #{n_bad}: {statuses}",
                              flush=True)
            exp.Next()
        print(f"REBUILD_DEBUG_BREPCHECK: {name} total={n_total} bad={n_bad}", flush=True)


def finalize(shape: TopoDS_Shape, chord_tol: float = None):
    """ShapeFix -> UnifySameDomain -> validity check. Returns (shape, is_valid).

    UnifySameDomain occasionally breaks manifoldness instead of just simplifying face count —
    observed on M5's double-fuse (two topology-event seams close together): the fixed,
    pre-unify shape was valid, unify's output was not. Since unify is a pure simplification
    step (merges coincident-domain faces/edges, changes no geometry), a shape it invalidates is
    worse than the one going in, so fall back to the pre-unify shape rather than propagate the
    corruption; the only cost is a few extra faces (`face_count_max` is a generous anti-
    tessellation guard, not a style gate).

    `chord_tol`, if given, is passed to `ShapeFix_Shape.SetPrecision` before fixing — on M5's
    near-tangent fore seam (two independently circle-fit boundaries meeting at almost, but not
    exactly, the same radius) the default OCCT precision leaves a knife-edge sliver that gmsh
    can't tet cleanly (min_quality ~0.006 vs the 0.1 gate); widening the fixer's working
    precision lets it heal that sliver instead of preserving it exactly.

    M13 (iter 70) found the opposite failure of the same trade-off: at `chord_tol=8` (its
    coarser real-STL-scale tolerance), `ShapeFix_Shape`+`ShapeFix_FixSmallFace` corrupted an
    ALREADY-VALID boolean-cut result into an invalid one (`BRepCheck_EnclosedRegion` — the
    single solid came out as two shells, one bogusly nested inside the other) — confirmed by
    instrumenting `pipeline/cli.py` to check validity immediately before this call (valid) and
    the fixed shape here (invalid), isolating the corruption to these two calls specifically,
    not the boolean ops. Since neither fixer is supposed to change valid geometry, only repair
    invalid geometry, a shape they invalidate is strictly worse than what went in — same
    "worse than the input, so fall back" logic already applied to `UnifySameDomain` below,
    extended to cover this pair too."""
    pre_fix_valid = BRepCheck_Analyzer(shape).IsValid()
    pre_fix_shape = shape

    fixer = ShapeFix_Shape(shape)
    if chord_tol is not None:
        fixer.SetPrecision(chord_tol)
    fixer.Perform()
    shape = fixer.Shape()

    # A boolean cut whose cutter is a thin sliver relative to the surrounding faces (a
    # near-degenerate off-axis hole right at the edge of where it merges into a bigger combined
    # loop, M8's slot/bore overlap) can leave a genuine zero-area face that plain ShapeFix_Shape
    # doesn't remove. This sliver can be small enough that BRepCheck_Analyzer still calls the
    # IN-MEMORY shape valid, yet re-reading it back after a STEP write (score.py's own check
    # does this, and it's what the driver actually grades) reveals it as invalid — STEP's
    # limited on-disk numeric precision turns a merely-tiny face into a truly zero-area one.
    # Run FixSmallFace unconditionally (not only when the pre-export check already fails) so
    # this class of face never reaches the exporter in the first place.
    small = ShapeFix_FixSmallFace()
    small.Init(shape)
    if chord_tol is not None:
        small.SetPrecision(chord_tol)
    small.Perform()
    shape = small.FixShape()
    if not BRepCheck_Analyzer(shape).IsValid():
        if pre_fix_valid:
            return pre_fix_shape, True
        import os
        if os.environ.get("REBUILD_DEBUG_BREPCHECK"):
            _debug_dump_brepcheck(shape)
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


def write_step(shape: TopoDS_Shape, path: str, chord_tol: float = None) -> None:
    """Write, then self-heal against STEP's own limited on-disk numeric precision: a face that
    passed BRepCheck_Analyzer in memory (see `finalize`'s comment on FixSmallFace) can still come
    back invalid once round-tripped through the file, because writing quantizes geometry to the
    STEP file's precision and can turn a merely-tiny face into a truly zero-area one. Re-reading
    and re-fixing the ACTUAL written shape (rather than trying to predict the quantization before
    writing) is what reliably matches what `score.py`'s own reread-and-check does downstream."""
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(str(path))

    for _ in range(2):
        reader = STEPControl_Reader()
        reader.ReadFile(str(path))
        reader.TransferRoots()
        reread = reader.OneShape()
        if BRepCheck_Analyzer(reread).IsValid():
            return
        small = ShapeFix_FixSmallFace()
        small.Init(reread)
        if chord_tol is not None:
            small.SetPrecision(chord_tol)
        small.Perform()
        fixed = small.FixShape()
        fixer = ShapeFix_Shape(fixed)
        if chord_tol is not None:
            fixer.SetPrecision(chord_tol)
        fixer.Perform()
        fixed = fixer.Shape()
        writer = STEPControl_Writer()
        writer.Transfer(fixed, STEPControl_StepModelType.STEPControl_AsIs)
        writer.Write(str(path))


def write_stl(shape: TopoDS_Shape, path: str, chord_tol: float) -> None:
    BRepMesh_IncrementalMesh(shape, chord_tol, False, 0.3, True).Perform()
    StlAPI_Writer().Write(shape, str(path))
