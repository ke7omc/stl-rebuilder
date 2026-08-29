"""Stage 2 (stations): place slice stations along z. MISSION.md §5.2 step 2 (uniform only for now;
--refine-bands / --adaptive are accepted by the CLI but not yet implemented)."""
import numpy as np


def uniform_stations(z_min: float, z_max: float, n: int, eps_end_val: float,
                      vertex_zs=None, jitter: float | None = None) -> np.ndarray:
    """Cosine-clustered (not literally uniform, name kept for the CLI's historical call site):
    stations are denser near both ends of [lo, hi] and sparser in the middle. This matters for
    curved end caps (domes) whose curvature is concentrated near the axial extremes — MISSION
    §5.2 step 6 calls this out explicitly ("dense cosine-clustered stations" toward the apex).
    A single cosine warp only gets ~15% of an n=40 budget into each end's dome band (measured on
    M2: 6/40 stations land in the ~500 mm fore_dome band, short of the `dome_stations_min>=8`
    gate); composing the warp with itself concentrates further and clears it with margin (10/40).
    A prismatic profile (M1) is unaffected either way — RDP still collapses it to 2 points."""
    n = max(int(n), 2)
    lo, hi = z_min + eps_end_val, z_max - eps_end_val
    if hi <= lo:
        lo, hi = z_min, z_max
    theta = np.linspace(0.0, np.pi, n)
    u = (1.0 - np.cos(theta)) / 2.0       # in [0, 1], clustered at 0 and 1
    u2 = (1.0 - np.cos(u * np.pi)) / 2.0  # compose again for tighter end clustering
    zs = lo + (hi - lo) * u2

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
