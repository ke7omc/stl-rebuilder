"""Stage 5 (fitting): circle fit + RDP polyline simplification. MISSION.md §5.2 step 5, step 6."""
import math

import numpy as np


def fit_circle(points: np.ndarray):
    """Kasa least-squares circle fit. Returns (cx, cy, R, max_resid, rms_resid)."""
    x = points[:, 0]
    y = points[:, 1]
    A = np.column_stack([x, y, np.ones_like(x)])
    b = x ** 2 + y ** 2
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx = sol[0] / 2.0
    cy = sol[1] / 2.0
    R = float(np.sqrt(sol[2] + cx ** 2 + cy ** 2))
    resid = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - R
    return float(cx), float(cy), R, float(np.max(np.abs(resid))), float(np.sqrt(np.mean(resid ** 2)))


def _perp_dist(p, a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    p = np.asarray(p, dtype=float)
    ab = b - a
    denom = np.dot(ab, ab)
    if denom == 0.0:
        return float(np.linalg.norm(p - a))
    t = np.dot(p - a, ab) / denom
    proj = a + t * ab
    return float(np.linalg.norm(p - proj))


def _trim_run_to_circle(arr, run, resid_tol: float):
    """Drop boundary points from `run` (a list of indices into `arr`) that don't actually lie on
    the circle the *rest* of the run defines. A per-point windowed local-radius test (what
    classifies a point as "curved" in the first place) can still misfire exactly at a real C1
    corner: a point genuinely on the adjoining straight edge can read as locally curved if its
    `+-window` neighbors happen to still be dominated by the true arc on one side (seen on M4:
    the mesh has no vertex AT the circle/line intersection, so the first straight-side sample
    is already ~300 mm further out radially, yet its window read as curved and got pulled into
    the bore-arc run). A single stray point like that turns a 3-point `GC_MakeArcOfCircle` fit
    (through the run's first/middle/last point) into a garbage off-axis circle, since 3-point
    fits are always exact through whichever 3 points are given — they don't validate that the
    rest of the run agrees. Fits a circle to the run's *interior* points (excluding both
    endpoints, when there are enough to spare) and tests only the two endpoints against that
    reference circle — a whole-run least-squares fit would let a single bad endpoint drag the
    fitted circle toward itself, keeping its own residual deceptively small while degrading the
    good points instead (measured on M4: including the outlier let max_resid over the *whole*
    run read as low as 7.6 mm from an off-axis 192 mm-radius circle, spreading the error rather
    than isolating it, so a simple max-residual-over-all-points stopping test never triggered).
    Drops whichever endpoint disagrees most with the interior-only fit and repeats, until both
    remaining endpoints agree with it within `resid_tol` or the run is too short to keep
    trimming."""
    run = list(run)
    while len(run) >= 3:
        core = run[1:-1] if len(run) >= 5 else run
        cx, cy, R, _, _ = fit_circle(arr[core])
        d_first = abs(math.hypot(arr[run[0], 0] - cx, arr[run[0], 1] - cy) - R)
        d_last = abs(math.hypot(arr[run[-1], 0] - cx, arr[run[-1], 1] - cy) - R)
        if max(d_first, d_last) <= resid_tol:
            return run
        run = run[1:] if d_first >= d_last else run[:-1]
    return run


def detect_arc_runs(pts, r_thresh: float = None, window: int = 2, resid_tol: float = None):
    """Classify a closed ring of (x, y) points into contiguous "arc" runs (small/finite local
    radius, e.g. a fillet or a genuinely curved wall) vs "straight" runs (large/ill-conditioned
    local radius), using a windowed Kasa `fit_circle` centered on each point (`+-window`
    neighbors, wrapping around the ring). Returns a list of arc runs, each a list of
    point-indices in ring order (wraparound runs that span the array boundary are merged into
    one). `pts` must be a closed-ring-style ordered list (no explicit repeated first==last
    point); used by `solids.build_prism_solid`'s arc+line hybrid to place exact
    `GC_MakeArcOfCircle` edges only where the boundary truly is curved, and straight edges
    everywhere else, instead of approximating curves with polyline chords (validated: chords
    alone can't clear `gmsh_min_sicn` at any face-count tradeoff, and for a wide arc the chord
    sagitta alone blows the surface_deviation gate) or smoothing the whole ring with one global
    periodic spline (validated: rounds off sharp cusps, ~0.9% volume error).

    `r_thresh=None` (default) auto-picks the split from the data itself: sort all local radii
    and take the single largest ratio between consecutive values (a log-scale "elbow") as the
    boundary, so it adapts to however many distinct curvature scales the cross-section actually
    has — M3's star has one scale (small fillets vs. ill-conditioned straight sides); M4/M5's
    finocyl bore has two "curved" scales fused into one ring (the ~40 mm tip fillet AND the
    ~300 mm main bore arc, both real curvature, both needing exact arcs) plus the straight fin
    sides, and a single fixed fraction of the ring's bounding radius (the previous hardcoded
    `0.25 * scale`) put the ~300 mm bore arc on the wrong side of the line, collapsing it to one
    straight chord with an 18 mm sagitta against a 1 mm gate. The elbow only fires when it's a
    genuine gap (ratio >= 3x, comfortably above the ~1.1-1.4x noise ratios measured between
    adjacent points on the same true curve); a ring with one uniform curvature scale throughout
    (no real "straight" side at all) returns no arcs at all rather than guessing a boundary in
    what is actually noise.

    `resid_tol` (defaults to `0.01 * r_thresh` if not given) is passed to `_trim_run_to_circle`,
    which drops boundary points that a *whole-run* circle fit rejects even though their own
    windowed local-radius test said "curved" (see that function's docstring for the corner case
    this catches — measured on M4, a stray point at the arc/straight junction turned a bore-arc
    run's 3-point fit into an off-axis 204 mm-radius circle instead of the true 300 mm one,
    biasing volume by >1%)."""
    n = len(pts)
    arr = np.asarray(pts, dtype=float)
    radii = np.empty(n)
    for i in range(n):
        idx = [(i + k) % n for k in range(-window, window + 1)]
        radii[i] = fit_circle(arr[idx])[2]

    if r_thresh is None:
        srt = np.sort(radii)
        if len(srt) < 2 or srt[0] <= 0.0:
            return []
        ratios = srt[1:] / np.maximum(srt[:-1], 1e-12)
        gap = int(np.argmax(ratios))
        if ratios[gap] < 3.0:
            return []
        r_thresh = math.sqrt(srt[gap] * srt[gap + 1])
    if resid_tol is None:
        resid_tol = 0.01 * r_thresh

    is_arc = radii < r_thresh

    if not is_arc.any() or is_arc.all():
        return []

    start0 = int(np.argmax(is_arc))
    order = [(start0 + k) % n for k in range(n)]
    cur_type, cur_run, segs = is_arc[order[0]], [order[0]], []
    for idx in order[1:]:
        if is_arc[idx] == cur_type:
            cur_run.append(idx)
        else:
            segs.append((cur_type, cur_run))
            cur_type, cur_run = is_arc[idx], [idx]
    segs.append((cur_type, cur_run))
    if len(segs) > 1 and segs[0][0] == segs[-1][0]:
        t0, r0 = segs[0]
        tl, rl = segs.pop()
        segs[0] = (t0, rl + r0)
    runs = [run for is_arc_run, run in segs if is_arc_run]
    return [_trim_run_to_circle(arr, run, resid_tol) if len(run) >= 3 else run for run in runs]


def simplify_closed_ring(pts, epsilon: float):
    """Douglas-Peucker simplification of a CLOSED 2D ring (`pts`: (x, y), no repeated
    first==last point). `rdp` above only handles an open polyline (fixed start/end anchors); a
    closed ring has no natural anchor pair, so split it at its two most-distant points (any pair
    far enough apart that both resulting chains are well-conditioned for RDP) into two open
    chains, simplify each independently, then rejoin.

    Used by `pipeline/cli.py::_build_bore_prism_or_loft` (MISSION §6.2 M6's linearly-scaling star
    bore) to turn a raw mesh-sliced ring (hundreds of points, most within sub-mm of a straight
    line) into a small vertex set before lofting -- straight runs collapse to 2 points while
    genuinely curved runs (the fillets) keep enough points to track the curve, which is exactly
    what `epsilon ~= chord_tol` should do without needing per-point arc/line *classification*
    (see that function's docstring for why classification itself is unreliable here: mesh-
    tessellation noise a doubly-ruled loft surface introduces reads as spurious low-radius
    "curvature" at a sub-chord_tol sagitta, well below `epsilon`, so RDP silently absorbs it
    into the nearest straight chord instead of needing a separate noise filter)."""
    pts = list(pts)
    n = len(pts)
    if n < 4:
        return pts
    arr = np.asarray(pts, dtype=float)
    d = np.sum((arr[:, None, :] - arr[None, :, :]) ** 2, axis=-1)
    i, j = np.unravel_index(np.argmax(d), d.shape)
    if i > j:
        i, j = j, i
    chain1 = pts[i:j + 1]
    chain2 = pts[j:] + pts[:i + 1]
    s1 = rdp(chain1, epsilon)
    s2 = rdp(chain2, epsilon)
    return s1[:-1] + s2[:-1]


def rdp(points, epsilon: float):
    """Douglas-Peucker simplification of a (z, R) polyline using perpendicular distance in the
    (z, R) plane (never radial |ΔR|, which diverges at a vertical dR/dz — MISSION.md §10)."""
    points = list(points)
    if len(points) < 3:
        return points
    start, end = points[0], points[-1]
    dmax, index = 0.0, 0
    for i in range(1, len(points) - 1):
        d = _perp_dist(points[i], start, end)
        if d > dmax:
            dmax, index = d, i
    if dmax > epsilon:
        left = rdp(points[:index + 1], epsilon)
        right = rdp(points[index:], epsilon)
        return left[:-1] + right
    return [start, end]
