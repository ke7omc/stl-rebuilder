"""Stage 2 (stations): place slice stations along z. MISSION.md §5.2 step 2. `uniform_stations`
is the cosine-clustered default; `adaptive_stations` (--adaptive) additionally concentrates
stations around detected geometric features (fast coarse cross-section scan) so narrow wall/
transition bands get enough density out of a fixed total station budget.

`apply_anchor_stations` (2026-09-05) is a second, independent hardening layer on top of that:
the quantile-of-CDF draw above is a *statistical* placement -- it usually puts something near a
real feature, but "usually" is exactly the gap that let M8 pass at `--adaptive --sections 100`
and fail at `140` (a different bug, since fixed in `pipeline/engine.py`, but the placement
draw's own reliability was never guaranteed either -- measured directly: M8 at n=40 leaves a
47 mm one-sided bracket around its own z=5850 event). `apply_anchor_stations` guarantees a
station within a bounded distance of a given z by *moving* the nearest existing one there,
never by inserting or removing one -- seeing MISSION §7's `n_stations_max`/`station_bands`/
`dome_stations_min` gates read the reported station COUNT with as little as 0 stations of
headroom on some milestones ruled out changing that count as an option."""
import numpy as np
import trimesh

from . import tol


def uniform_stations(z_min: float, z_max: float, n: int, eps_end_val: float,
                      vertex_zs=None, jitter: float | None = None,
                      chord_tol: float | None = None, anchor_zs=None,
                      anchor_per_side: int = 1) -> np.ndarray:
    """Cosine-clustered (not literally uniform, name kept for the CLI's historical call site):
    stations are denser near both ends of [lo, hi] and sparser in the middle. This matters for
    curved end caps (domes) whose curvature is concentrated near the axial extremes — MISSION
    §5.2 step 6 calls this out explicitly ("dense cosine-clustered stations" toward the apex).
    A single cosine warp only gets ~15% of an n=40 budget into each end's dome band (measured on
    M2: 6/40 stations land in the ~500 mm fore_dome band, short of the `dome_stations_min>=8`
    gate); composing the warp with itself concentrates further and clears it with margin (10/40).
    A prismatic profile (M1) is unaffected either way — RDP still collapses it to 2 points.

    `anchor_zs` (2026-09-05): guarantee a station near each given z by moving the nearest
    existing one there (see `apply_anchor_stations`) -- e.g. the verify-and-refine retry loop's
    deviation target. `None`/empty is a complete no-op, so every caller that doesn't pass it
    gets byte-identical output to before this parameter existed."""
    n = max(int(n), 2)
    lo, hi = z_min + eps_end_val, z_max - eps_end_val
    if hi <= lo:
        lo, hi = z_min, z_max
    theta = np.linspace(0.0, np.pi, n)
    u = (1.0 - np.cos(theta)) / 2.0       # in [0, 1], clustered at 0 and 1
    u2 = (1.0 - np.cos(u * np.pi)) / 2.0  # compose again for tighter end clustering
    zs = lo + (hi - lo) * u2
    zs = apply_anchor_stations(zs, anchor_zs, chord_tol, hi - lo, per_side=anchor_per_side)
    return _nudge_vertex_coincidence(zs, vertex_zs, hi - lo, jitter)


def _anchor_pad(gap: float, chord_tol: float | None, span: float) -> float:
    """How close a station must land to `anchor` to count as "covering" it: 3x MISSION §5.2
    step 7's own event-density figure, but capped at a fraction of the station's own local gap
    so a milestone with a coarse chord_tol (M9 ct=5, M13 ct=8) can't demand a pad wider than the
    local spacing itself -- that would force a MUCH bigger displacement than the gap allows and
    starve the station's other neighbour instead of just tightening around the anchor."""
    if chord_tol is None or chord_tol <= 0:
        base = 1e-4 * max(span, 1.0)
    else:
        base = 3.0 * tol.topo_tol(chord_tol, span)
    return min(max(base, 1e-6 * max(span, 1.0)), 0.4 * gap)


def apply_anchor_stations(zs: np.ndarray, anchors, chord_tol: float | None, span: float,
                          per_side: int = 1) -> np.ndarray:
    """Guarantee a station within a bounded distance of each z in `anchors`, by MOVING the
    nearest existing station(s) toward it -- never inserting or removing one, so the total count
    (and therefore every station-count gate) is untouched. A pure no-op (returns `zs` unchanged,
    same object) when there's nothing to anchor to or too few stations to safely move.

    Safe without any knowledge of hidden region/band definitions because every move is capped at
    `0.4x` the station's own local gap: a station inside a tight, already-dense band (few mm
    apart, e.g. a `station_bands`-gated wall) can only move a few mm, while a station with real
    room to move is -- by that same logic -- not the only thing holding up a tight band. Wrapped
    so it can never raise: this is an optimisation, not a correctness requirement, and must never
    be able to turn a working rebuild into a crashed one.

    Only intervenes when the CURRENT distance is more than `_TRIGGER_MULTIPLE x pad` -- i.e. a
    real, clearly-inadequate gap, not "not perfectly tight". The first version moved a station
    whenever it wasn't already within one `pad`, which "fixed" M12's `breakthrough` band (whose
    two already-close-together anchors were already bracketed within ~2.6-2.8x pad by the
    ordinary quantile draw) by tightening two ALREADY-reasonable brackets down to the pad
    exactly -- concentrating them near the anchors and thinning the band's spread enough to drop
    its station-count gate from 10 to 9 (measured, `harness/score.py --milestone M12`). M8's
    actual under-covered case (n=40, a real 47 mm one-sided bracket) is ~4.2x its own pad --
    comfortably above this threshold, so that fix is unaffected; a distribution the quantile
    draw already got roughly right is left alone (Brady, 2026-09-05)."""
    _TRIGGER_MULTIPLE = 3.5
    try:
        if anchors is None:
            return zs
        anchors = sorted({float(a) for a in anchors}, reverse=True)
        if not anchors or len(zs) < 4:
            return zs
        out = np.array(zs, dtype=float, copy=True)
        claimed: set[int] = set()
        for a in anchors:
            j = int(np.searchsorted(out, a))
            below = [j - 1 - k for k in range(per_side) if 0 <= j - 1 - k]
            above = [j + k for k in range(per_side) if j + k < len(out)]
            for side_indices, sign in ((below, -1.0), (above, 1.0)):
                for m, i in enumerate(side_indices):
                    if i in claimed or i <= 0 or i >= len(out) - 1:
                        continue
                    gap = out[i + 1] - out[i - 1]
                    if gap <= 0:
                        continue
                    pad = (2 * m + 1) * _anchor_pad(gap, chord_tol, span)
                    if abs(a - out[i]) <= _TRIGGER_MULTIPLE * pad:
                        continue  # already roughly adequate -- don't disturb it
                    target = a + sign * pad  # pad short of the anchor, from this side
                    max_move = 0.45 * gap
                    if sign < 0:  # station below the anchor: only allowed to move UP toward it
                        new_z = min(max(out[i], target), out[i] + max_move)
                        new_z = min(new_z, a)
                    else:         # station above the anchor: only allowed to move DOWN toward it
                        new_z = max(min(out[i], target), out[i] - max_move)
                        new_z = max(new_z, a)
                    out[i] = new_z
                    claimed.add(i)
        if out.shape != zs.shape or not np.all(np.diff(out) > 0):
            return zs  # never hand back something that isn't still a valid strictly-increasing set
        return out
    except Exception:
        return zs


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


def _scan_area_and_loops(mesh, zs: np.ndarray, on_progress=None):
    """Cheap per-z cross-section scan: total polygon area (holes subtracted, shapely does this
    natively) and loop (polygon) count. Used only to build a placement *weight*, not for the
    final station geometry — a failed/degenerate section just contributes 0/1 and is otherwise
    harmless since it's overwritten by the real (retried) `slice_station` later at the chosen z.

    `on_progress(frac)`, when given, is called every ~20 iterations (and on the last one) — this
    coarse scan can run up to ~2000 sections on a GUI-max run and was previously entirely silent,
    which is what made large `--adaptive` runs look stalled at 0% for long stretches."""
    normal = np.array([0.0, 0.0, 1.0])
    areas = np.zeros(len(zs))
    nloops = np.ones(len(zs), dtype=int)
    n = len(zs)
    for i, z in enumerate(zs):
        if on_progress is not None and (i % 20 == 0 or i == n - 1):
            on_progress(i / max(n - 1, 1))
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


def _merge_anchors(anchors_with_strength, chord_tol: float | None, span: float,
                   dz: float) -> list[float]:
    """Collapse anchors closer than `max(0.5*dz, tol.topo_tol(...))` -- two "events" that close
    together are almost always the same transition sampled either side of it -- keeping the
    STRONGER one's z, never a midpoint: a midpoint sits mid-transition, the single worst place
    to put a station meant to resolve that transition."""
    if not anchors_with_strength:
        return []
    merge_tol = max(0.5 * dz, tol.topo_tol(chord_tol, span) if chord_tol else 0.5 * dz)
    ordered = sorted(anchors_with_strength, key=lambda t: t[0])
    clusters = [[ordered[0]]]
    for z, s in ordered[1:]:
        if z - clusters[-1][-1][0] <= merge_tol:
            clusters[-1].append((z, s))
        else:
            clusters.append([(z, s)])
    return [max(c, key=lambda t: t[1])[0] for c in clusters]


def _detect_topology_anchors(scan_zs: np.ndarray, areas: np.ndarray,
                             change_idx: np.ndarray, n: int, chord_tol: float | None,
                             span: float, dz: float) -> list[float]:
    """Every scan interval where the loop count changes is a real topology event worth
    guaranteeing a station next to -- but RANKED BY |delta area|, not |delta loop count|. That
    distinction matters: on a noisy marching-cubes mesh (M9) the loop count can jump by hundreds
    at a burst of sub-mm surface noise (measured: 3 -> 251 -> 1 loops at one such burst) while
    the real slot birth/death nearby changes area by orders of magnitude more than the noise
    does -- ranking by loop count would spend the anchor budget entirely on noise. Capped at
    `max(1, n // 10)` anchors so a part with many small features can't claim the whole budget."""
    events = [(0.5 * (scan_zs[i0] + scan_zs[i0 + 1]), float(abs(areas[i0 + 1] - areas[i0])))
              for i0 in change_idx]
    merged = _merge_anchors(events, chord_tol, span, dz)
    if not merged:
        return []
    # `_merge_anchors` already dropped the strength; re-pair for the final ranked cap by
    # nearest-original-event strength (post-merge, ties broken by z order -- deterministic).
    strength_by_z = {z: 0.0 for z in merged}
    for z, s in events:
        nearest = min(merged, key=lambda m: abs(m - z))
        strength_by_z[nearest] = max(strength_by_z[nearest], s)
    ranked = sorted(merged, key=lambda z: strength_by_z[z], reverse=True)
    return ranked[:max(1, n // 10)]


def adaptive_stations(mesh, z_min: float, z_max: float, n: int, eps_end_val: float,
                       vertex_zs=None, n_scan: int = 400, jitter: float | None = None,
                       chord_tol: float | None = None, anchor_zs=None,
                       anchor_per_side: int = 1, on_progress=None) -> np.ndarray:
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
    areas, nloops = _scan_area_and_loops(mesh, scan_zs, on_progress=on_progress)

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

    # Guarantee coverage of the events this scan itself just found (2026-09-05), rather than
    # trusting the quantile draw above to land near them: it usually does, but "usually" is
    # exactly the gap a statistical placement can't close (measured on M8 at n=40: a 47 mm
    # one-sided bracket around a real event). Caller-supplied `anchor_zs` (the verify-and-refine
    # retry loop's deviation target) are folded in alongside the events this function already
    # detected, so a single `apply_anchor_stations` call covers both sources without moving the
    # same station twice for two different reasons.
    anchors = _detect_topology_anchors(scan_zs, areas, change_idx, n, chord_tol, hi - lo, dz)
    if anchor_zs:
        anchors = _merge_anchors(
            [(z, float("inf")) for z in anchors] + [(float(z), float("inf")) for z in anchor_zs],
            chord_tol, hi - lo, dz)
    zs = apply_anchor_stations(zs, anchors, chord_tol, hi - lo, per_side=anchor_per_side)
    return _nudge_vertex_coincidence(zs, vertex_zs, hi - lo, jitter)
