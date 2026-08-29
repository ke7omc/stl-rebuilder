"""Stage 2 (stations): place slice stations along z. MISSION.md §5.2 step 2 (uniform only for now;
--refine-bands / --adaptive are accepted by the CLI but not yet implemented)."""
import numpy as np


def uniform_stations(z_min: float, z_max: float, n: int, eps_end_val: float,
                      vertex_zs=None, jitter: float | None = None) -> np.ndarray:
    n = max(int(n), 2)
    lo, hi = z_min + eps_end_val, z_max - eps_end_val
    if hi <= lo:
        lo, hi = z_min, z_max
    zs = np.linspace(lo, hi, n)

    if vertex_zs is not None and len(vertex_zs):
        # Only guard against exact (float) coincidence with a mesh vertex z — the nudge must
        # stay far smaller than eps_end_val so it can never push a station out of its inset band.
        if jitter is None:
            jitter = 1e-9 * max(hi - lo, 1.0)
        vs = np.sort(np.unique(np.asarray(vertex_zs)))
        for i, z in enumerate(zs):
            idx = np.searchsorted(vs, z)
            for j in (idx - 1, idx):
                if 0 <= j < len(vs) and abs(vs[j] - z) < jitter:
                    zs[i] = z + jitter
    return zs
