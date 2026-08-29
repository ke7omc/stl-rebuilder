"""Truth geometry generators: build analytic BRep solids, tessellate to STL, export STEP.

`make(milestone_name)` is the public API. Truth files regenerate deterministically when missing.
All units: mm.
"""
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeRevol, BRepPrimAPI_MakePrism
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.GC import GC_MakeArcOfEllipse
from OCP.gp import gp_Elips, gp_Ax2, gp_Ax1, gp_Pnt, gp_Dir, gp_Trsf, gp_Vec
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeVertex,
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_Transform,
)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet2d

from harness import milestones as ms

TRUTH_DIR = Path(__file__).parent / "truth"
CHORD_TOL = ms.CHORD_TOL  # mm


@dataclass
class Truth:
    milestone: str
    shape: TopoDS_Shape   # the OCP solid
    V_truth: float        # mm³ from BRepGProp
    A_truth: float        # mm² surface area
    bbox: Tuple[float, float, float, float, float, float]  # xmin,ymin,zmin,xmax,ymax,zmax
    step_path: Path
    stl_path: Path


def _volume_area(shape: TopoDS_Shape) -> Tuple[float, float]:
    vprops = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vprops)
    volume = vprops.Mass()
    aprops = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, aprops)
    area = aprops.Mass()
    return volume, area


def _bbox(shape: TopoDS_Shape) -> Tuple[float, float, float, float, float, float]:
    from OCP.BRep import BRep_Builder
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return xmin, ymin, zmin, xmax, ymax, zmax


def _write_step(shape: TopoDS_Shape, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    w.Write(str(path))


def _write_stl(shape: TopoDS_Shape, path: Path, chord_tol: float = CHORD_TOL) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Tessellate at controlled chordal deflection (same value passed to rebuild.py)
    BRepMesh_IncrementalMesh(shape, chord_tol, False, 0.3, True).Perform()
    writer = StlAPI_Writer()
    writer.Write(shape, str(path))


# ---------------------------------------------------------------------------
# M1: annular cylinder  L=10000, R_o=1000, R_i=300
# ---------------------------------------------------------------------------

def _make_m1() -> Truth:
    spec = ms.get("M1")
    L  = spec.params["L"]
    R_o = spec.params["R_o"]
    R_i = spec.params["R_i"]

    outer = BRepPrimAPI_MakeCylinder(R_o, L).Shape()
    inner = BRepPrimAPI_MakeCylinder(R_i, L).Shape()

    cut = BRepAlgoAPI_Cut(outer, inner)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("M1 boolean cut failed")
    shape = cut.Shape()

    V, A = _volume_area(shape)
    bbox = _bbox(shape)

    step_path = TRUTH_DIR / "M1.step"
    stl_path  = TRUTH_DIR / "M1.stl"
    _write_step(shape, step_path)
    _write_stl(shape, stl_path)

    return Truth(
        milestone="M1",
        shape=shape,
        V_truth=V,
        A_truth=A,
        bbox=bbox,
        step_path=step_path,
        stl_path=stl_path,
    )


def _ellipse_dome_edge(radial: float, axial: float, center_z: float, n_dir: Tuple[float, float, float]):
    """Quarter-ellipse arc edge in the XZ half-plane (y=0): apex on axis <-> shoulder at x=radial.

    `n_dir` selects which of the two possible ellipse windings points the arc's angle-90 end
    (the apex) toward +z or -z from `center_z` — see harness/generators.py module docstring
    note below for the derivation.
    """
    ax2 = gp_Ax2(gp_Pnt(0.0, 0.0, center_z), gp_Dir(*n_dir), gp_Dir(1.0, 0.0, 0.0))
    elips = gp_Elips(ax2, radial, axial)
    arc = GC_MakeArcOfEllipse(elips, 0.0, math.pi / 2.0, True)
    return BRepBuilderAPI_MakeEdge(arc.Value()).Edge()


def _capsule_outer_shape(L: float, R_o: float, dome_h: float) -> TopoDS_Shape:
    """Revolve a meridian (fore ellipse arc + cylindrical wall + aft ellipse arc + axis) about Z
    into a solid "capsule": a cylinder of length L-2*dome_h with 2:1 ellipsoidal domes (radial
    semi-axis R_o, axial semi-axis dome_h) capping both ends, apexes on-axis at z=0 and z=L.
    """
    # n_dir=(0,1,0) at center_z=dome_h puts the arc's apex at z=0 (fore); n_dir=(0,-1,0) at
    # center_z=L-dome_h puts it at z=L (aft) — the two ellipse windings needed so both apexes
    # land on-axis instead of mirrored back into the cylinder body.
    fore_edge = _ellipse_dome_edge(R_o, dome_h, dome_h, (0.0, 1.0, 0.0))
    aft_edge = _ellipse_dome_edge(R_o, dome_h, L - dome_h, (0.0, -1.0, 0.0))
    wall_edge = BRepBuilderAPI_MakeEdge(
        gp_Pnt(R_o, 0.0, dome_h), gp_Pnt(R_o, 0.0, L - dome_h)
    ).Edge()
    axis_edge = BRepBuilderAPI_MakeEdge(gp_Pnt(0.0, 0.0, L), gp_Pnt(0.0, 0.0, 0.0)).Edge()

    mkwire = BRepBuilderAPI_MakeWire()
    for e in (fore_edge, wall_edge, aft_edge, axis_edge):
        mkwire.Add(e)
    if not mkwire.IsDone():
        raise RuntimeError("capsule meridian wire construction failed")
    face = BRepBuilderAPI_MakeFace(mkwire.Wire(), True).Face()

    axis = gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
    return BRepPrimAPI_MakeRevol(face, axis, 2.0 * math.pi).Shape()


def _straight_bore(R_i: float, L: float, margin: float = 20.0) -> TopoDS_Shape:
    """A straight cylinder of radius R_i, centered on Z, extending margin/2 past each end of
    [0, L] so the cutter fully consumes any capsule cross-section even exactly at z=0/z=L."""
    cyl = BRepPrimAPI_MakeCylinder(R_i, L + margin).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Pnt(0.0, 0.0, 0.0), gp_Pnt(0.0, 0.0, -margin / 2.0))
    return BRepBuilderAPI_Transform(cyl, trsf, True).Shape()


# ---------------------------------------------------------------------------
# M2: M1 + 2:1 ellipsoidal domes on the outer surface both ends, straight bore through
# ---------------------------------------------------------------------------

def _make_m2() -> Truth:
    spec = ms.get("M2")
    L = spec.params["L"]
    R_o = spec.params["R_o"]
    R_i = spec.params["R_i"]
    dome_h = spec.params["dome_semi_axial"]

    outer = _capsule_outer_shape(L, R_o, dome_h)
    bore = _straight_bore(R_i, L)

    cut = BRepAlgoAPI_Cut(outer, bore)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("M2 boolean cut failed")
    shape = cut.Shape()

    V, A = _volume_area(shape)
    bbox = _bbox(shape)

    step_path = TRUTH_DIR / "M2.step"
    stl_path = TRUTH_DIR / "M2.stl"
    _write_step(shape, step_path)
    _write_stl(shape, stl_path)

    return Truth(
        milestone="M2",
        shape=shape,
        V_truth=V,
        A_truth=A,
        bbox=bbox,
        step_path=step_path,
        stl_path=stl_path,
    )


def _star_bore_cutter(n_star: int, R_valley: float, R_tip: float, fillet_tip: float,
                       fillet_valley: float, L: float, margin: float = 20.0) -> TopoDS_Shape:
    """A star-shaped prism cutter, centered on Z, extending margin/2 past each end of [0, L].

    Profile: 2*n_star vertices alternating tip (R_tip) / valley (R_valley) around the origin,
    connected by straight edges, then rounded per-vertex via BRepFilletAPI_MakeFillet2d (tips
    get fillet_tip, valleys get fillet_valley) — the 2D fillet operator needs the *unrounded*
    polygon's own vertices, so vertices are built once with BRepBuilderAPI_MakeVertex and reused
    identically in both the edges and the AddFillet calls (shared TopoDS_Vertex identity is what
    lets Fillet2d find the two edges adjacent to each corner).
    """
    points = []
    for i in range(2 * n_star):
        angle = i * math.pi / n_star
        r = R_tip if i % 2 == 0 else R_valley
        points.append(gp_Pnt(r * math.cos(angle), r * math.sin(angle), 0.0))

    vertices = [BRepBuilderAPI_MakeVertex(p).Vertex() for p in points]
    mkwire = BRepBuilderAPI_MakeWire()
    for i in range(len(vertices)):
        v1, v2 = vertices[i], vertices[(i + 1) % len(vertices)]
        mkwire.Add(BRepBuilderAPI_MakeEdge(v1, v2).Edge())
    if not mkwire.IsDone():
        raise RuntimeError("M3 star profile wire construction failed")
    face = BRepBuilderAPI_MakeFace(mkwire.Wire(), True).Face()

    fillet = BRepFilletAPI_MakeFillet2d(face)
    for i, v in enumerate(vertices):
        r = fillet_tip if i % 2 == 0 else fillet_valley
        fillet.AddFillet(v, r)
    fillet.Build()
    if not fillet.IsDone():
        raise RuntimeError("M3 star profile fillet construction failed")
    filleted_face = TopoDS.Face_s(fillet.Shape())

    prism = BRepPrimAPI_MakePrism(filleted_face, gp_Vec(0.0, 0.0, L + margin)).Shape()
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Pnt(0.0, 0.0, 0.0), gp_Pnt(0.0, 0.0, -margin / 2.0))
    return BRepBuilderAPI_Transform(prism, trsf, True).Shape()


# ---------------------------------------------------------------------------
# M3: outer cylinder + 6-point star bore (filleted tips/valleys), full length
# ---------------------------------------------------------------------------

def _make_m3() -> Truth:
    spec = ms.get("M3")
    L = spec.params["L"]
    R_o = spec.params["R_o"]
    R_valley = spec.params["R_valley"]
    R_tip = spec.params["R_tip"]
    n_star = spec.params["n_star"]
    fillet_tip = spec.params["fillet_tip"]
    fillet_valley = spec.params["fillet_valley"]

    outer = BRepPrimAPI_MakeCylinder(R_o, L).Shape()
    bore = _star_bore_cutter(n_star, R_valley, R_tip, fillet_tip, fillet_valley, L)

    cut = BRepAlgoAPI_Cut(outer, bore)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("M3 boolean cut failed")
    shape = cut.Shape()

    V, A = _volume_area(shape)
    bbox = _bbox(shape)

    step_path = TRUTH_DIR / "M3.step"
    stl_path = TRUTH_DIR / "M3.stl"
    _write_step(shape, step_path)
    _write_stl(shape, stl_path)

    return Truth(
        milestone="M3",
        shape=shape,
        V_truth=V,
        A_truth=A,
        bbox=bbox,
        step_path=step_path,
        stl_path=stl_path,
    )


# ---------------------------------------------------------------------------
# M4–M5: not yet implemented (built in later M0 iterations)
# ---------------------------------------------------------------------------


def _make_m4() -> Truth:
    raise NotImplementedError("M4 generator not yet built (M0 iteration 2+)")


def _make_m5() -> Truth:
    raise NotImplementedError("M5 generator not yet built (M0 iteration 2+)")


_MAKERS = {
    "M1": _make_m1,
    "M2": _make_m2,
    "M3": _make_m3,
    "M4": _make_m4,
    "M5": _make_m5,
}


def make(milestone: str) -> Truth:
    """Build (or rebuild) the analytic ground truth for *milestone* and return a Truth object."""
    if milestone not in _MAKERS:
        raise KeyError(f"Unknown milestone {milestone!r}. Valid: {list(_MAKERS)}")
    return _MAKERS[milestone]()
