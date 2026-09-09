"""Stage report JSON. MISSION.md §5.2 step 8, §5.3 (scorer always passes --report)."""
import json


def write(path: str, n_stations: int, stations_z_mm, paths_used: dict,
          topology_events_z_mm=None, frame: dict = None, axial_extent_mm=None,
          axial_origin_z: float = None, verification: dict = None,
          roundness: dict = None) -> None:
    payload = {
        "n_stations": int(n_stations),
        "stations_z_mm": [float(z) for z in stations_z_mm],
        "paths_used": dict(paths_used),
        "topology_events_z_mm": [float(z) for z in (topology_events_z_mm or [])],
    }
    if frame is not None:
        payload["frame"] = frame
    if axial_extent_mm is not None:
        payload["axial_extent_mm"] = axial_extent_mm
    if axial_origin_z is not None:
        # GUI-only field (additive, not read by the scorer): the offset subtracted from raw
        # solid-frame Z to produce `stations_z_mm`/`topology_events_z_mm` above, so a consumer
        # that also has the exported STEP/STL (which is in the undone, un-shifted frame) can map
        # report Z back to mesh-space Z (`mesh_z = report_z + axial_origin_z`). G3 visual review
        # #2 found the GUI's station table was sampling the solid mesh at the wrong Z because it
        # compared report-frame Z directly against mesh-frame Z without this offset.
        payload["axial_origin_z"] = float(axial_origin_z)
    if verification is not None:
        # Additive end-user verification block (input mesh vs produced solid: volume, per-axis
        # bounds, body count, approximate sampled deviation — see
        # `pipeline.engine._compute_verification`). Not read by the frozen scorer.
        payload["verification"] = verification
    if roundness is not None:
        # Additive decoupled-roundness-tolerance block (docs/plans/decoupled_roundness_tolerance
        # .md, 2026-09-09): the mesh-measured (or user-stated) out-of-roundness floor this run's
        # `resid_gate` was built from -- `floor_mm`, and (when auto-measured) `measured_mm`/
        # `raw_mm`/`n_probes`/`capped`/`cap_pct`, or `source: "cli"` when `--roundness-tol` gave
        # an explicit value. Not read by the frozen scorer.
        payload["roundness"] = roundness
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
