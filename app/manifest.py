"""Builds the Output-node result manifest (MISSION §12/G2: "bodies, faces, volume, min edge,
warnings"). Reads the produced STEP directly via OCP -- the same dependency-light approach
`pipeline.engine._read_step_shape` already uses -- since the current report JSON
(`pipeline/report.py`) doesn't carry body/face/volume counts (a documented HANDOFF gap)."""
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape

from pipeline.engine import _read_step_shape


def build(result, analysis=None) -> dict:
    manifest = {
        "output_path": result.output_path,
        "stl_path": result.stl_path,
        "warnings": [],
    }
    report = result.report or {}
    manifest["n_stations"] = report.get("n_stations")
    manifest["topology_events_z_mm"] = report.get("topology_events_z_mm", [])
    manifest["paths_used"] = report.get("paths_used", {})
    manifest["verification"] = report.get("verification")
    if analysis is not None:
        manifest["median_edge_length_mm"] = analysis.median_edge_length_mm

    try:
        shape = _read_step_shape(result.output_path)
        solids = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_SOLID, solids)
        faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        manifest["bodies"] = solids.Size()
        manifest["faces"] = faces.Size()
        manifest["volume_mm3"] = props.Mass()
    except Exception as exc:
        manifest["warnings"].append(f"could not read output STEP for manifest: {exc}")
    return manifest
