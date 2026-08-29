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
