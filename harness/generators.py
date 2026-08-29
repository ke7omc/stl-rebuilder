"""Truth geometry generators: build analytic BRep solids, tessellate to STL, export STEP.

`make(milestone_name)` is the public API. Truth files regenerate deterministically when missing.
All units: mm.
"""
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
from OCP.TopoDS import TopoDS_Shape

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


# ---------------------------------------------------------------------------
# M2–M5: not yet implemented (built in later M0 iterations)
# ---------------------------------------------------------------------------

def _make_m2() -> Truth:
    raise NotImplementedError("M2 generator not yet built (M0 iteration 2+)")


def _make_m3() -> Truth:
    raise NotImplementedError("M3 generator not yet built (M0 iteration 2+)")


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
