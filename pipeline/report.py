"""Stage report JSON. MISSION.md §5.2 step 8, §5.3 (scorer always passes --report)."""
import json


def write(path: str, n_stations: int, stations_z_mm, paths_used: dict,
          topology_events_z_mm=None) -> None:
    payload = {
        "n_stations": int(n_stations),
        "stations_z_mm": [float(z) for z in stations_z_mm],
        "paths_used": dict(paths_used),
        "topology_events_z_mm": [float(z) for z in (topology_events_z_mm or [])],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
