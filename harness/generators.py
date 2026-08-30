"""Truth geometry generators: build analytic BRep solids, tessellate to STL, export STEP.

`make(milestone_name)` is the public API. Truth files regenerate deterministically when missing.
All units: mm.
"""
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakeRevol,
    BRepPrimAPI_MakePrism,
)
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.GC import GC_MakeArcOfEllipse, GC_MakeArcOfCircle
from OCP.gp import gp_Elips, gp_Ax2, gp_Ax1, gp_Pnt, gp_Dir, gp_Trsf, gp_Vec
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeVertex,
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_Transform,
)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet2d
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepTools import BRepTools

from harness import milestones as ms
from harness import metrics
from harness import voxelize

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
    """Tight bounding box from exact geometry.

    `BRepBndLib.Add_s` boxes a B-spline by its *control poles*, which for a spline through points
    on a circle lie outside the surface — a 20-station periodic fit of R=1000 overshoots by ~16 mm,
    which would trip the 0.1 % `bbox_err_pct` gate (2 mm here) on a geometrically correct result.
    `AddOptimal_s` evaluates the real geometry instead. `useTriangulation=False` keeps the answer
    independent of whether a triangulation happens to be attached to the shape.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
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


def _star_wire(n_star: int, R_valley: float, R_tip: float, fillet_tip: float,
               fillet_valley: float, z: float) -> "TopoDS_Wire":
    """The filleted star profile's outer wire (see `_star_bore_cutter`), built directly in the
    z=`z` plane so it can be fed to `BRepOffsetAPI_ThruSections` as a loft section. Same vertex
    order/orientation/seam (starts at angle 0) as every other call, so two calls at different
    (radii, z) are directly loft-compatible without needing OCCT's twist-correction heuristic.
    """
    points = []
    for i in range(2 * n_star):
        angle = i * math.pi / n_star
        r = R_tip if i % 2 == 0 else R_valley
        points.append(gp_Pnt(r * math.cos(angle), r * math.sin(angle), z))

    vertices = [BRepBuilderAPI_MakeVertex(p).Vertex() for p in points]
    mkwire = BRepBuilderAPI_MakeWire()
    for i in range(len(vertices)):
        v1, v2 = vertices[i], vertices[(i + 1) % len(vertices)]
        mkwire.Add(BRepBuilderAPI_MakeEdge(v1, v2).Edge())
    if not mkwire.IsDone():
        raise RuntimeError("M6 star profile wire construction failed")
    face = BRepBuilderAPI_MakeFace(mkwire.Wire(), True).Face()

    fillet = BRepFilletAPI_MakeFillet2d(face)
    for i, v in enumerate(vertices):
        r = fillet_tip if i % 2 == 0 else fillet_valley
        fillet.AddFillet(v, r)
    fillet.Build()
    if not fillet.IsDone():
        raise RuntimeError("M6 star profile fillet construction failed")
    filleted_face = TopoDS.Face_s(fillet.Shape())
    return BRepTools.OuterWire_s(filleted_face)


def _star_loft_cutter(params: dict) -> TopoDS_Shape:
    """Ruled loft between the M6 star profile at z=loft_z0 and its x1.5-scaled twin at
    z=loft_z1, both spanning past [0, L] so the loft fully consumes the cylinder's ends."""
    n_star = params["n_star"]
    wire0 = _star_wire(n_star, params["R_valley0"], params["R_tip0"],
                        params["fillet_tip0"], params["fillet_valley0"], params["loft_z0"])
    wire1 = _star_wire(n_star, params["R_valley1"], params["R_tip1"],
                        params["fillet_tip1"], params["fillet_valley1"], params["loft_z1"])

    loft = BRepOffsetAPI_ThruSections(True, True)  # isSolid=True, ruled=True
    loft.CheckCompatibility(False)  # sections built identically (same order/orientation/seam)
    loft.AddWire(wire0)
    loft.AddWire(wire1)
    loft.Build()
    if not loft.IsDone():
        raise RuntimeError("M6 star loft construction failed")
    return loft.Shape()


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


def _fin_slot_prism(fin_w: float, r_inner: float, r_outer: float, tip_r: float,
                    z0: float, z1: float, angle: float) -> TopoDS_Shape:
    """One fin-slot cutter: a prism over z in [z0, z1] whose cross-section is a radial slot of
    width `fin_w` along the ray at `angle`, rounded at its outer end with radius `tip_r`.

    `tip_r == fin_w/2` (the milestone params), so the outer cap is an exact semicircle centred at
    r_outer - tip_r and the slot's radial extent is exactly r_outer. The inner end is pulled
    *inside* the bore (r_inner - 50) so fusing it onto the bore cylinder is transversal rather
    than tangent (MISSION §10.6); the extra material sits where the bore already removes
    everything, so it cannot change the resulting solid.
    """
    hw = fin_w / 2.0
    if abs(tip_r - hw) > 1e-9:
        raise ValueError(f"fin tip radius {tip_r} must equal half-width {hw} for a semicircular cap")
    r_cap = r_outer - tip_r
    r_start = r_inner - 50.0
    if r_start <= hw:
        raise ValueError("fin slot start radius collapses onto the axis")

    p_a = gp_Pnt(r_start,  hw, 0.0)
    p_b = gp_Pnt(r_start, -hw, 0.0)
    p_c = gp_Pnt(r_cap,   -hw, 0.0)
    p_m = gp_Pnt(r_outer,  0.0, 0.0)
    p_d = gp_Pnt(r_cap,    hw, 0.0)
    arc = GC_MakeArcOfCircle(p_c, p_m, p_d).Value()

    mkwire = BRepBuilderAPI_MakeWire()
    for e in (
        BRepBuilderAPI_MakeEdge(p_a, p_b).Edge(),
        BRepBuilderAPI_MakeEdge(p_b, p_c).Edge(),
        BRepBuilderAPI_MakeEdge(arc).Edge(),
        BRepBuilderAPI_MakeEdge(p_d, p_a).Edge(),
    ):
        mkwire.Add(e)
    if not mkwire.IsDone():
        raise RuntimeError("fin slot profile wire construction failed")
    face = BRepBuilderAPI_MakeFace(mkwire.Wire(), True).Face()

    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0.0, 0.0, z1 - z0)).Shape()
    place = gp_Trsf()
    place.SetTranslation(gp_Pnt(0.0, 0.0, 0.0), gp_Pnt(0.0, 0.0, z0))
    prism = BRepBuilderAPI_Transform(prism, place, True).Shape()
    if angle:
        rot = gp_Trsf()
        rot.SetRotation(gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)), angle)
        prism = BRepBuilderAPI_Transform(prism, rot, True).Shape()
    return prism


def _finocyl_cutter(params: dict, L: float, fin_z_end: float) -> TopoDS_Shape:
    """Full-length straight bore fused with `n_fins` fin slots spanning z in
    [fin_z_start, fin_z_end]. The fins' fore face at fin_z_start is the flat wall that makes the
    milestone's topology event; `fin_z_end` is where they stop (past the aft end for M4, at the
    aft dome shoulder for M5)."""
    n_fins = params["n_fins"]
    hw = params["fin_w"] / 2.0
    r_start = params["fin_r_inner"] - 50.0
    if hw >= r_start * math.sin(math.pi / n_fins):
        raise ValueError("fin slots overlap each other at their inner ends")

    cutter = _straight_bore(params["R_bore"], L)
    for k in range(n_fins):
        fin = _fin_slot_prism(
            params["fin_w"], params["fin_r_inner"], params["fin_r_outer"],
            params["fin_tip_r"], params["fin_z_start"], fin_z_end,
            2.0 * math.pi * k / n_fins,
        )
        fuse = BRepAlgoAPI_Fuse(cutter, fin)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError(f"finocyl cutter fuse failed on fin {k}")
        cutter = fuse.Shape()
    return cutter


def _finish(milestone: str, shape: TopoDS_Shape) -> Truth:
    V, A = _volume_area(shape)
    bbox = _bbox(shape)
    step_path = TRUTH_DIR / f"{milestone}.step"
    stl_path = TRUTH_DIR / f"{milestone}.stl"
    _write_step(shape, step_path)
    _write_stl(shape, stl_path)
    return Truth(
        milestone=milestone,
        shape=shape,
        V_truth=V,
        A_truth=A,
        bbox=bbox,
        step_path=step_path,
        stl_path=stl_path,
    )


# ---------------------------------------------------------------------------
# M4: finocyl — straight cylinder, circular bore, 8 fin slots aft of fin_z_start
# ---------------------------------------------------------------------------

def _make_m4() -> Truth:
    spec = ms.get("M4")
    L = spec.params["L"]
    outer = BRepPrimAPI_MakeCylinder(spec.params["R_o"], L).Shape()
    # Fins run past the aft end so the z=L face is a clean planar cut, not a tangency.
    cutter = _finocyl_cutter(spec.params, L, L + 10.0)

    cut = BRepAlgoAPI_Cut(outer, cutter)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("M4 boolean cut failed")
    return _finish("M4", cut.Shape())


# ---------------------------------------------------------------------------
# M5: M2's domed capsule + M4's bore and fin slots
# ---------------------------------------------------------------------------

def _make_m5() -> Truth:
    spec = ms.get("M5")
    L = spec.params["L"]
    dome_h = spec.params["dome_semi_axial"]

    outer = _capsule_outer_shape(L, spec.params["R_o"], dome_h)
    # Fins stop at the aft dome shoulder (z = L - dome_h), matching the `fin_zone` region band in
    # milestones._m5. Running them to z=L as in M4 would punch the slots straight through the
    # dome wall (the dome's radius drops below fin_r_outer at z≈9857), which is not a finocyl.
    cutter = _finocyl_cutter(spec.params, L, L - dome_h)

    cut = BRepAlgoAPI_Cut(outer, cutter)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("M5 boolean cut failed")
    return _finish("M5", cut.Shape())


# ---------------------------------------------------------------------------
# M6: cylinder minus a ruled loft between two star cross-sections (linear scale in z)
# ---------------------------------------------------------------------------

def _make_m6() -> Truth:
    spec = ms.get("M6")
    L = spec.params["L"]
    outer = BRepPrimAPI_MakeCylinder(spec.params["R_o"], L).Shape()
    cutter = _star_loft_cutter(spec.params)

    cut = BRepAlgoAPI_Cut(outer, cutter)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("M6 boolean cut failed")
    return _finish("M6", cut.Shape())


# ---------------------------------------------------------------------------
# M7: central bore through + 6 satellite perforations dying into a flat wall at z=7000
# ---------------------------------------------------------------------------

def _satellite_cylinder(radius: float, r_center: float, angle: float, z0: float, z1: float,
                        margin: float = 20.0) -> TopoDS_Shape:
    """A cylinder of `radius` centred at (r_center*cos(angle), r_center*sin(angle)), spanning
    z in [z0, z1]. Overshoots past z0 by margin/2 (an open end, fused onto other cutters/the
    fore face) but stops exactly at z1 (the intentional flat end wall / chain death — no
    overshoot there, unlike `_straight_bore`'s through-cut)."""
    x = r_center * math.cos(angle)
    y = r_center * math.sin(angle)
    ax = gp_Ax2(gp_Pnt(x, y, z0 - margin / 2.0), gp_Dir(0.0, 0.0, 1.0))
    return BRepPrimAPI_MakeCylinder(ax, radius, (z1 - z0) + margin / 2.0).Shape()


def _make_m7() -> Truth:
    spec = ms.get("M7")
    L = spec.params["L"]
    outer = BRepPrimAPI_MakeCylinder(spec.params["R_o"], L).Shape()
    cutter = _straight_bore(spec.params["R_bore"], L)

    n_sat = spec.params["n_sat"]
    R_sat = spec.params["R_sat"]
    r_sat = spec.params["r_sat"]
    sat_z_end = spec.params["sat_z_end"]
    for k in range(n_sat):
        sat = _satellite_cylinder(R_sat, r_sat, 2.0 * math.pi * k / n_sat, 0.0, sat_z_end)
        fuse = BRepAlgoAPI_Fuse(cutter, sat)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError(f"M7 cutter fuse failed on satellite {k}")
        cutter = fuse.Shape()

    cut = BRepAlgoAPI_Cut(outer, cutter)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("M7 boolean cut failed")
    return _finish("M7", cut.Shape())


# ---------------------------------------------------------------------------
# M8: M5 cavity "dilated" by web w=150 -> bore R=450 + 8 obround slots, end-loops filleted
# ---------------------------------------------------------------------------

def _obround_slot_cutter(r0: float, r1: float, half_w: float, z0: float, z1: float,
                          fillet_r: float, angle: float) -> TopoDS_Shape:
    """A radial *wedge* slot cutter: a meridian rectangle [r0,r1] x [z0,z1] (built in the
    half-plane y=0, all 4 corners rounded by `fillet_r` via 2-D fillet — the "end-edge loop"
    rounding) revolved a limited angle about Z, centred on `angle`.

    A constant-Cartesian-width box was tried first and rejected: at `n_slots=8` (45 deg apart)
    a `half_w=190` box overlaps its neighbour for any radius below ~500 mm (`r0` starts at 400),
    and the resulting near-tangent boolean fuse produced sub-0.02 gmsh SICN slivers right on the
    shared boundary. A wedge with constant *angular* half-width `theta_half = atan(half_w/r1)`
    (matching the stated half-width at the slot's outer radius) tapers toward the bore instead of
    staying full-width, which keeps every slot's angular span (~2*theta_half*n_slots ~ 201 deg
    total for these params) safely inside its own 45 deg sector — no neighbour ever touches.
    """
    theta_half = math.atan(half_w / r1)
    pts = [gp_Pnt(r0, 0.0, z0), gp_Pnt(r1, 0.0, z0), gp_Pnt(r1, 0.0, z1), gp_Pnt(r0, 0.0, z1)]
    vertices = [BRepBuilderAPI_MakeVertex(p).Vertex() for p in pts]
    mkwire = BRepBuilderAPI_MakeWire()
    for i in range(len(vertices)):
        v1, v2 = vertices[i], vertices[(i + 1) % len(vertices)]
        mkwire.Add(BRepBuilderAPI_MakeEdge(v1, v2).Edge())
    if not mkwire.IsDone():
        raise RuntimeError("M8 slot meridian wire construction failed")
    face = BRepBuilderAPI_MakeFace(mkwire.Wire(), True).Face()

    fillet = BRepFilletAPI_MakeFillet2d(face)
    for v in vertices:
        fillet.AddFillet(v, fillet_r)
    fillet.Build()
    if not fillet.IsDone():
        raise RuntimeError("M8 slot meridian fillet construction failed")
    filleted_face = TopoDS.Face_s(fillet.Shape())

    rot0 = gp_Trsf()
    rot0.SetRotation(gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)), angle - theta_half)
    placed = TopoDS.Face_s(BRepBuilderAPI_Transform(filleted_face, rot0, True).Shape())

    axis = gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
    return BRepPrimAPI_MakeRevol(placed, axis, 2.0 * theta_half).Shape()


def _capsule_slot_cavity_shape(p: dict, label: str) -> TopoDS_Shape:
    """Capsule outer minus (straight bore fused with `n_slots` obround wedge-slot cutters).
    Shared by M8 (as-is) and M10 (M8's construction re-run on already-scaled params, then
    placed in a non-canonical frame by the caller)."""
    L = p["L"]
    outer = _capsule_outer_shape(L, p["R_o"], p["dome_semi_axial"])
    cutter = _straight_bore(p["R_bore"], L)

    n_slots = p["n_slots"]
    hw = p["slot_half_width"]
    r1 = p["slot_outer_r"]
    z0 = p["slot_z_lo"]
    z1 = p["slot_z_hi"]
    fr = p["slot_fillet"]
    # overlap into the bore so the fuse is transversal, not tangent; scaled with the rest of the
    # geometry via p["slot_overlap"] (M10 scales this alongside everything else, unlike a
    # hardcoded constant which would go negative at M10's x1/40 scale).
    r0 = p["R_bore"] - p["slot_overlap"]
    for k in range(n_slots):
        slot = _obround_slot_cutter(r0, r1, hw, z0, z1, fr, 2.0 * math.pi * k / n_slots)
        fuse = BRepAlgoAPI_Fuse(cutter, slot)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError(f"{label} cutter fuse failed on slot {k}")
        cutter = fuse.Shape()

    cut = BRepAlgoAPI_Cut(outer, cutter)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError(f"{label} boolean cut failed")
    return cut.Shape()


def _make_m8() -> Truth:
    spec = ms.get("M8")
    return _finish("M8", _capsule_slot_cavity_shape(spec.params, "M8"))


def _make_m9() -> Truth:
    """M8's exact solid, but the truth STL fed to the pipeline is a noisy, skewed
    marching-cubes surface on an anisotropic grid (MISSION §6.2/§7.2 M9), synthesised by
    `harness/voxelize.py` from a clean fine tessellation of this same solid. `truth.shape`/
    `V_truth`/`A_truth`/`bbox`/`step_path` stay exact (M8's analytic geometry) so every
    check that compares against them (volume_err_pct, deviation, bbox) is unaffected by the
    pathology; only `truth.stl_path` -- the file `score.py` copies to `input.stl` and also
    uses for `input_watertight` -- points at the pathological mesh."""
    spec = ms.get("M9")
    shape = _capsule_slot_cavity_shape(spec.params, "M9")
    V, A = _volume_area(shape)
    bbox = _bbox(shape)
    step_path = TRUTH_DIR / "M9.step"
    stl_path = TRUTH_DIR / "M9.stl"
    _write_step(shape, step_path)

    # Clean, fine reference tessellation for voxelize.py's occupancy/SDF sampling -- never
    # written to M9.stl itself, and far finer than the M9 input grid spacing (10,10,40 mm).
    ref_path = TRUTH_DIR / "M9.ref.stl"
    _write_stl(shape, ref_path, chord_tol=spec.chord_tol / 2.0)
    ref_mesh = metrics.load_mesh(ref_path)

    input_mesh = voxelize.synthesize_voxel_input(ref_mesh, spec.input, seed=7)
    stl_path.parent.mkdir(parents=True, exist_ok=True)
    input_mesh.export(str(stl_path))

    return Truth(
        milestone="M9",
        shape=shape,
        V_truth=V,
        A_truth=A,
        bbox=bbox,
        step_path=step_path,
        stl_path=stl_path,
    )


def _place_in_frame(shape: TopoDS_Shape, frame: "ms.Frame", scale: float = 1.0) -> TopoDS_Shape:
    """Rotate the canonical +z-axis shape onto `frame.axis`, uniformly scale about the origin
    (about the origin so it commutes with the rotation), then translate to `frame.origin_mm`.
    Both the truth STEP and the truth's own bbox live in this rotated/scaled/translated *mm*
    frame; only the STL's numeric units differ (handled separately by `_finish_framed`).

    `scale` defaults to 1.0 (M13 does not shrink the geometry, only M10 does). When `scale != 1`,
    callers should build the input `shape` at *full* size first and let this scale it down as the
    very last step, rather than pre-scaling the construction params: doing the fillet/boolean ops
    at full scale keeps them well inside OCCT's absolute tolerance, then a single linear scale of
    the finished BRep preserves that conditioning (scaling parametric geometry doesn't redo any
    boolean). Building the same cavity directly at 1/40 scale instead measurably degrades mesh
    quality (min SICN dropped from ~0.25 to ~0.03 on the M10 capsule) because the slot fillets
    became small enough that OCCT's fixed absolute tolerance ate into their relative precision.
    """
    z = gp_Dir(0.0, 0.0, 1.0)
    target = gp_Dir(*frame.axis)
    trsf_rot = gp_Trsf()
    if z.IsEqual(target, 1e-12):
        pass  # already aligned, identity rotation
    elif z.IsOpposite(target, 1e-12):
        # 180 degree flip: any axis perpendicular to z works.
        trsf_rot.SetRotation(gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(1.0, 0.0, 0.0)), math.pi)
    else:
        rot_axis = z.Crossed(target)
        angle = z.Angle(target)
        trsf_rot.SetRotation(gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), rot_axis), angle)
    placed = BRepBuilderAPI_Transform(shape, trsf_rot, True).Shape()

    if scale != 1.0:
        trsf_scale = gp_Trsf()
        trsf_scale.SetScale(gp_Pnt(0.0, 0.0, 0.0), scale)
        placed = BRepBuilderAPI_Transform(placed, trsf_scale, True).Shape()

    trsf_trans = gp_Trsf()
    trsf_trans.SetTranslation(gp_Vec(*frame.origin_mm))
    return BRepBuilderAPI_Transform(placed, trsf_trans, True).Shape()


def _finish_framed(milestone: str, shape_mm: TopoDS_Shape, frame: "ms.Frame",
                    chord_tol_mm: float) -> Truth:
    """Like `_finish`, but `shape_mm` is already placed in its non-canonical truth frame
    (rotated axis, off-origin) and expressed in mm. The truth STEP stays mm (MISSION §6.2 M10:
    "truth STEP in that frame (mm)"); the truth STL is written in `frame.units` (mm/25.4 = in)."""
    V, A = _volume_area(shape_mm)
    bbox = _bbox(shape_mm)
    step_path = TRUTH_DIR / f"{milestone}.step"
    stl_path = TRUTH_DIR / f"{milestone}.stl"
    _write_step(shape_mm, step_path)

    unit_scale = 1.0 / frame.scale_to_mm  # mm -> frame.units
    trsf_scale = gp_Trsf()
    trsf_scale.SetScale(gp_Pnt(0.0, 0.0, 0.0), unit_scale)
    shape_units = BRepBuilderAPI_Transform(shape_mm, trsf_scale, True).Shape()
    _write_stl(shape_units, stl_path, chord_tol=chord_tol_mm * unit_scale)

    return Truth(
        milestone=milestone,
        shape=shape_mm,
        V_truth=V,
        A_truth=A,
        bbox=bbox,
        step_path=step_path,
        stl_path=stl_path,
    )


def _make_m10() -> Truth:
    spec = ms.get("M10")
    m8_params = ms.get("M8").params
    scale = spec.params["L"] / m8_params["L"]  # 1/40, MISSION §6.2 M10
    shape_full = _capsule_slot_cavity_shape(m8_params, "M10")
    shape_mm = _place_in_frame(shape_full, spec.frame, scale=scale)
    return _finish_framed("M10", shape_mm, spec.frame, spec.chord_tol)


def _make_m12() -> Truth:
    """Near-end-of-burn cavity decomposition: same dilated-cavity construction as M8 (straight
    bore fused with `n_slots` wedge-revolve obround slots), but with M12's larger params (wider
    bore, wider/taller slots) that push the slot cutter's outer radius past the aft dome's local
    envelope radius — the boolean cut naturally opens the slots through the dome surface there
    (the "breakthrough" the spec's params/description call out), no special-case geometry needed:
    `_obround_slot_cutter` and `_capsule_outer_shape` are already general in z and r.
    """
    spec = ms.get("M12")
    p = spec.params
    L = p["L"]
    outer = _capsule_outer_shape(L, p["R_o"], p["dome_semi_axial"])
    cutter = _straight_bore(p["R_bore"], L)

    n_slots = p["n_slots"]
    hw = p["slot_half_width"]
    r1 = p["slot_outer_r"]
    z0 = p["slot_z_lo"]
    z1 = p["slot_z_hi"]
    fr = p["slot_fillet"]
    # `_obround_slot_cutter`'s 2-D fillet needs each corner's radius to clear half of both
    # adjacent edge lengths, so the radial edge (r1-r0) must be >= 2*fr with margin. M8's fixed
    # `R_bore - 50` overlap (radial edge 450) was already tight for its fr=150 (2*fr=300); M12's
    # fr=250 (2*fr=500) would make that same 450 overlap self-intersect the two corner fillets on
    # the radial edge, which is exactly the "M8 slot meridian fillet construction failed" OCCT
    # error hit when this was first tried unchanged. Size the overlap into the bore explicitly
    # from fr instead of copying M8's constant.
    r0 = min(p["R_bore"] - 50.0, r1 - 2.0 * fr - 50.0)
    for k in range(n_slots):
        slot = _obround_slot_cutter(r0, r1, hw, z0, z1, fr, 2.0 * math.pi * k / n_slots)
        fuse = BRepAlgoAPI_Fuse(cutter, slot)
        fuse.Build()
        if not fuse.IsDone():
            raise RuntimeError(f"M12 cutter fuse failed on slot {k}")
        cutter = fuse.Shape()

    cut = BRepAlgoAPI_Cut(outer, cutter)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("M12 boolean cut failed")
    return _finish("M12", cut.Shape())


# ---------------------------------------------------------------------------
# M11: 3 disjoint annular BATES segments (gapped, flat ends) -> one compound of 3 solids
# ---------------------------------------------------------------------------

def _annular_segment(R_o: float, R_i: float, z_lo: float, z_hi: float) -> TopoDS_Shape:
    """A single annular cylinder segment spanning z in [z_lo, z_hi] (both ends flat, no
    overshoot — each segment is a standalone solid, not fused to its neighbours)."""
    ax = gp_Ax2(gp_Pnt(0.0, 0.0, z_lo), gp_Dir(0.0, 0.0, 1.0))
    outer = BRepPrimAPI_MakeCylinder(ax, R_o, z_hi - z_lo).Shape()
    bore = BRepPrimAPI_MakeCylinder(ax, R_i, z_hi - z_lo).Shape()
    cut = BRepAlgoAPI_Cut(outer, bore)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError("M11 segment boolean cut failed")
    return cut.Shape()


def _make_m11() -> Truth:
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    spec = ms.get("M11")
    R_o = spec.params["R_o"]
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for seg in spec.params["segments"]:
        solid = _annular_segment(R_o, seg["R_i"], seg["z_lo"], seg["z_hi"])
        builder.Add(compound, solid)
    return _finish("M11", compound)


_MAKERS = {
    "M1": _make_m1,
    "M2": _make_m2,
    "M3": _make_m3,
    "M4": _make_m4,
    "M5": _make_m5,
    "M6": _make_m6,
    "M7": _make_m7,
    "M8": _make_m8,
    "M9": _make_m9,
    "M10": _make_m10,
    "M11": _make_m11,
    "M12": _make_m12,
}


def make(milestone: str) -> Truth:
    """Build (or rebuild) the analytic ground truth for *milestone* and return a Truth object."""
    if milestone not in ms.MILESTONES:
        raise KeyError(f"Unknown milestone {milestone!r}. Valid: {list(ms.MILESTONES)}")
    if milestone not in _MAKERS:
        # Registered in milestones.py (a real milestone) but its generator isn't built yet.
        # NotImplementedError (not KeyError) so selftest.py's per-milestone catch reports this
        # as a FAIL for that milestone instead of crashing the whole selftest run.
        raise NotImplementedError(f"generator for {milestone!r} not yet implemented")
    return _MAKERS[milestone]()
