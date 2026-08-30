"""Stage 7 (booleans). MISSION.md §5.2 step 7, §10 failure mode 6."""
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.TopoDS import TopoDS_Shape


def cut(outer: TopoDS_Shape, cutter: TopoDS_Shape, fuzzy: float) -> TopoDS_Shape:
    """`BRepAlgoAPI_Cut` with a fuzzy value; retries once at 3x fuzzy on failure (OCP 7.9.3
    exposes only `IsDone()`, no `HasErrors()` — see MISSION.md §10)."""
    for fz in (fuzzy, 3.0 * fuzzy):
        op = BRepAlgoAPI_Cut(outer, cutter)
        op.SetFuzzyValue(fz)
        op.SetRunParallel(True)
        op.Build()
        if op.IsDone():
            return op.Shape()
    raise RuntimeError("boolean cut failed even at 3x fuzzy value")


def fuse(a: TopoDS_Shape, b: TopoDS_Shape, fuzzy: float) -> TopoDS_Shape:
    """`BRepAlgoAPI_Fuse` with a fuzzy value; same retry-at-3x-fuzzy pattern as `cut`. Used to
    join two bore cutters that meet at a topology event (M4/M5's fin-slot birth plane) into one
    cutter solid before the single `cut` against the envelope."""
    for fz in (fuzzy, 3.0 * fuzzy):
        op = BRepAlgoAPI_Fuse(a, b)
        op.SetFuzzyValue(fz)
        op.SetRunParallel(True)
        op.Build()
        if op.IsDone():
            return op.Shape()
    raise RuntimeError("boolean fuse failed even at 3x fuzzy value")
