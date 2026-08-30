"""Stage 5 (fitting): circle fit + RDP polyline simplification. MISSION.md §5.2 step 5, step 6."""
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


def detect_arc_runs(pts, r_thresh: float, window: int = 2):
    """Classify a closed ring of (x, y) points into contiguous "arc" runs (small local radius,
    e.g. a fillet) vs "straight" runs (large/ill-conditioned local radius), using a windowed
    Kasa `fit_circle` centered on each point (`+-window` neighbors, wrapping around the ring).
    Returns a list of arc runs, each a list of point-indices in ring order (wraparound runs that
    span the array boundary are merged into one). `pts` must be a closed-ring-style ordered list
    (no explicit repeated first==last point); used by `solids.build_prism_solid`'s arc+line
    hybrid to place exact `GC_MakeArcOfCircle` edges only where the boundary truly is a circular
    fillet, and straight edges everywhere else, instead of approximating fillets with polyline
    chords (validated: chords alone can't clear `gmsh_min_sicn` at any face-count tradeoff) or
    smoothing the whole ring with one global periodic spline (validated: rounds off sharp cusps,
    ~0.9% volume error)."""
    n = len(pts)
    arr = np.asarray(pts, dtype=float)
    radii = np.empty(n)
    for i in range(n):
        idx = [(i + k) % n for k in range(-window, window + 1)]
        radii[i] = fit_circle(arr[idx])[2]
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
    return [run for is_arc_run, run in segs if is_arc_run]


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
