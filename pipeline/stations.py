"""Stage 2 (stations): place slice stations along z. MISSION.md §5.2 step 2. `uniform_stations`
is the cosine-clustered default; `adaptive_stations` (--adaptive) additionally concentrates
stations around detected geometric features (fast coarse cross-section scan) so narrow wall/
transition bands get enough density out of a fixed total station budget."""
import numpy as np
import trimesh


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
    return _nudge_vertex_coincidence(zs, vertex_zs, hi - lo, jitter)


def _nudge_vertex_coincidence(zs: np.ndarray, vertex_zs, span: float,
                               jitter: float | None = None) -> np.ndarray:
    """Shared by `uniform_stations`/`adaptive_stations`: guard against exact (float)
    coincidence with a mesh vertex z. The nudge must stay far smaller than any inset band so it
    can never push a station out of it."""
    if vertex_zs is None or not len(vertex_zs):
        return zs
    if jitter is None:
        jitter = 1e-9 * max(span, 1.0)
    vs = np.sort(np.unique(np.asarray(vertex_zs)))
    for i, z in enumerate(zs):
        idx = np.searchsorted(vs, z)
        for j in (idx - 1, idx):
            if 0 <= j < len(vs) and abs(vs[j] - z) < jitter:
                zs[i] = z + jitter
    return zs


def _scan_area_and_loops(mesh, zs: np.ndarray):
    """Cheap per-z cross-section scan: total polygon area (holes subtracted, shapely does this
    natively) and loop (polygon) count. Used only to build a placement *weight*, not for the
    final station geometry — a failed/degenerate section just contributes 0/1 and is otherwise
    harmless since it's overwritten by the real (retried) `slice_station` later at the chosen z."""
    normal = np.array([0.0, 0.0, 1.0])
    areas = np.zeros(len(zs))
    nloops = np.ones(len(zs), dtype=int)
    for i, z in enumerate(zs):
        to_2D = trimesh.geometry.plane_transform(np.array([0.0, 0.0, z]), normal)
        sec = mesh.section(plane_origin=[0.0, 0.0, z], plane_normal=[0.0, 0.0, 1.0])
        if sec is None:
            continue
        planar, _ = sec.to_2D(to_2D=to_2D)
        polys = [p for p in planar.polygons_full if p is not None]
        areas[i] = sum(p.area for p in polys)
        # Count INTERIOR rings (holes), not exterior polygons: trimesh groups each hole as an
        # `interiors` entry of the (single, for our watertight single-body parts) outer polygon,
        # so the exterior-polygon count never changes even when e.g. one merged bore/slot cavity
        # splits into several separate holes -- exactly the transition this scan needs to catch.
        nloops[i] = max(sum(len(p.interiors) for p in polys), 1)
    return areas, nloops


def adaptive_stations(mesh, z_min: float, z_max: float, n: int, eps_end_val: float,
                       vertex_zs=None, n_scan: int = 400, jitter: float | None = None) -> np.ndarray:
    """Feature-aware placement (MISSION §6.2 M8+ `station_bands`/`adaptive_efficiency`): scan the
    mesh's cross-sectional area/loop-count at `n_scan` coarse samples, build a placement density
    from where that signal changes fastest (dome curvature, and — much more sharply — a
    topology event like a slot/hole appearing), then draw the final `n` station z's as quantiles
    of that density (a weighted-CDF inverse sample). This generalises the old fixed cosine warp:
    a hand-tuned end-clustering formula only concentrates stations at the two axial extremes, so
    a feature band in the *interior* (e.g. a wall transition mid-barrel) gets whatever the
    uniform middle of the cosine curve happens to drop there — which can be 0-1 stations out of
    80 even though the region right next to it (a dome) gets >15. Weighting by the actual
    measured |dA/dz| instead means ANY sharp feature earns stations, wherever it sits, with no
    milestone-specific band coordinates baked into the pipeline.

    A discrete topology change (loop count changes between two scan samples) gets an extra
    weight bump whose *width* is estimated from the data itself (how far the elevated |dA/dz|
    extends past the transition before decaying back near the baseline level) rather than a
    fixed mm figure, so this isn't tuned to one milestone's specific band width."""
    n = max(int(n), 2)
    lo, hi = z_min + eps_end_val, z_max - eps_end_val
    if hi <= lo:
        lo, hi = z_min, z_max
    n_scan = max(int(n_scan), 4 * n, 50)
    scan_zs = np.linspace(lo, hi, n_scan)
    areas, nloops = _scan_area_and_loops(mesh, scan_zs)

    dz = scan_zs[1] - scan_zs[0] if n_scan > 1 else 1.0
    darea = np.abs(np.gradient(areas, scan_zs))
    scale = np.median(darea[darea > 0]) if np.any(darea > 0) else 1.0
    scale = scale if scale > 0 else 1.0
    weight = np.log1p(darea / scale) + 0.05  # small positive floor: full-domain coverage

    # Topology-event bump: find every scan interval where the loop count changes, then expand
    # outward from it while |dA/dz| stays clearly above baseline (a data-driven feature width),
    # capped so one event can't swallow the whole scan range.
    baseline = np.median(darea)
    peak_w = float(np.max(weight))
    change_idx = np.flatnonzero(nloops[:-1] != nloops[1:])
    max_expand = max(int(0.15 * n_scan), 3)
    for i0 in change_idx:
        lo_i, hi_i = i0, i0 + 1
        steps = 0
        while lo_i > 0 and darea[lo_i - 1] > 2.0 * baseline and steps < max_expand:
            lo_i -= 1
            steps += 1
        steps = 0
        while hi_i < n_scan - 1 and darea[hi_i + 1] > 2.0 * baseline and steps < max_expand:
            hi_i += 1
            steps += 1
        weight[lo_i:hi_i + 1] = np.maximum(weight[lo_i:hi_i + 1], 1.3 * peak_w)

    cumw = np.concatenate(([0.0], np.cumsum(0.5 * (weight[1:] + weight[:-1]) * np.diff(scan_zs))))
    total = cumw[-1]
    targets = (np.arange(n) + 0.5) / n * total
    zs = np.interp(targets, cumw, scan_zs)
    zs = np.sort(zs)
    return _nudge_vertex_coincidence(zs, vertex_zs, hi - lo, jitter)
