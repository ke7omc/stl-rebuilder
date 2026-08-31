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


def fit_circle_robust(points: np.ndarray, trim_pct: float = 1.0, trim_cap_pct: float = 5.0):
    """Same fit as `fit_circle`, but the returned residual ignores up to `trim_pct`% of the
    worst-fitting points (capped at `trim_cap_pct`%) before taking the max. On a genuinely
    circular ring the mesher's chord_tol bounds deviation along the *meridian* (surface-normal),
    not the in-plane radius; where the local dR/dz slope is steep (e.g. near a dome pole, or M8's
    bore-pinch region right past z_min) that meridian error projects into a much larger apparent
    radial residual at a handful of individual polygon vertices, even though the loop is exactly
    circular (measured on M8's dome, z=168.5, n=396: the single worst point is 0.75mm off while
    the 99th percentile is 0.37mm). The downstream revolve profile is fit from many stations'
    (z, R) samples, so a few near-noise-floor vertices should not veto treating a station as
    circular. A genuinely non-circular loop (a fin slot, a star bore) has a large FRACTION of its
    points off-circle, not a handful, so trimming does not mask real topology."""
    cx, cy, R, max_resid, rms_resid = fit_circle(points)
    n = len(points)
    n_trim = min(int(np.ceil(n * trim_pct / 100.0)), int(n * trim_cap_pct / 100.0))
    if n_trim > 0 and n - n_trim >= 20:
        resid = np.abs(np.sqrt((points[:, 0] - cx) ** 2 + (points[:, 1] - cy) ** 2) - R)
        max_resid = float(np.sort(resid)[: n - n_trim][-1])
    return cx, cy, R, max_resid, rms_resid


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


def _grow_runs_to_circle(arr, runs):
    """Extend each arc run over adjacent *unclassified* points that provably lie on the circle
    the run itself defines. The mirror image of `_trim_run_to_circle`, and it fixes the opposite
    failure of the same cause: the classifier's `+-window` local fit is contaminated for the
    `window` points either side of a curve/line junction, so the point AT the tangency (which
    belongs to both) and the first point or two genuinely on the arc read as "straight" and are
    left out of the run.

    That matters because a straight run does not become edges through its own points — it
    collapses to ONE chord from the previous run's last point to the next run's first point. So
    a truncated arc run does not just lose an arc point; it moves the *straight* edge's endpoint
    off the straight line and tilts the whole edge. Measured on M5 (`build_prism_solid` on the
    merged bore+fin ring): the fin-tip R=40 fillet run started 2 points late, so each fin flank's
    single chord ran from the bore corner to a point 0.446 mm inside the true flank, deviating
    linearly along the whole 360 mm flank — `surface_deviation_p99_mm` 0.4127 against a 0.4 gate,
    with every other region unchanged.

    The test is the run's own fit quality, not a shared tolerance: fit the circle on the run's
    interior points and absorb a neighbour only when its residual is within `4 x` the interior
    rms (floored so an exactly-fitted run can still grow, capped at 2 % of R so a noisy run
    cannot swallow a straight side). The margin is enormous in practice because a tangent line
    leaves the circle quadratically — on M5's fillets the interior rms is 2.0e-4 mm, the two
    absorbed points sit at 1.8-2.3e-4 mm, and the first genuinely-straight point beyond them is
    324 mm off; on the same ring's 300 mm bore-arc runs every neighbour is 361 mm off, so they do
    not grow at all. Points already owned by another run are never taken, and a run can at most
    double in length, so growth cannot cascade around the ring."""
    if not runs:
        return runs
    n = len(arr)
    owned = set()
    for run in runs:
        owned.update(run)
    out = []
    for run in runs:
        run = list(run)
        if len(run) < 3 or len(run) >= n - 1:
            out.append(run)
            continue
        core = run[1:-1] if len(run) >= 5 else run
        cx, cy, R, _max, rms = fit_circle(arr[core])
        tol = min(max(4.0 * rms, 1e-9 * max(R, 1.0)), 0.02 * R)
        budget = len(run)
        for step in (-1, 1):
            grown = 0
            while grown < budget and len(run) < n - 1:
                j = ((run[0] if step < 0 else run[-1]) + step) % n
                if j in owned:
                    break
                if abs(math.hypot(arr[j, 0] - cx, arr[j, 1] - cy) - R) > tol:
                    break
                if step < 0:
                    run.insert(0, j)
                else:
                    run.append(j)
                owned.add(j)
                grown += 1
        out.append(run)
    return out


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
        # Picking the single largest ratio anywhere in the sorted array is fooled by a tiny
        # (1-2 point) cluster of spuriously small local radii — e.g. a genuinely sharp corner
        # introduced by a boolean/union step (M5's `_build_slot_lobes` translate-and-union seam)
        # makes a window straddling it fit a near-zero-radius circle. That pair's own ratio to
        # its *next* neighbor can beat the true elbow between the real curved cluster (fillets,
        # a disc-cut arc) and the straight runs, picking a threshold far too small and reading
        # every genuine curve as "straight" (measured on M5: threshold ~12mm instead of ~500mm,
        # turning all 8 fin-tip fillets + disc arcs into polyline chords -> gmsh_tet sliver,
        # min SICN 0.0817 vs gate 0.1). Require the candidate split to leave at least
        # `window + 1` points on the small side, so a 1-2 point outlier spike can't set the
        # boundary by itself; it still ends up classified (usually folded into the real arc
        # cluster, occasionally trimmed away by `_trim_run_to_circle`).
        min_side = window + 1
        candidates = [g for g in range(len(ratios)) if g + 1 >= min_side]
        gap = max(candidates, key=lambda g: ratios[g]) if candidates else int(np.argmax(ratios))
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
    runs = [_trim_run_to_circle(arr, run, resid_tol) if len(run) >= 3 else run for run in runs]
    return _grow_runs_to_circle(arr, runs)


def _fit_line_tls(pts):
    """Total-least-squares line through `pts` -> (point_on_line, unit_direction)."""
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    d = vt[0]
    return c, d / np.linalg.norm(d)


def _cross2(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def fit_fillet_ring(pts, arc_spans, line_tol: float):
    """Reconstruct a filleted-polygon cross-section (a star bore, a slotted bore) as EXACT
    tangent fillets rather than as an arc fitted independently to each fillet's own sample points.

    `pts` is the raw closed ring (x, y), `arc_spans` a list of `(i_first, i_last)` raw indices --
    one per fillet, in ring order -- marking (approximately) where that fillet's points are;
    consecutive spans are separated by a straight flank. Returns one dict per fillet
    `{center, radius, t1, t2, mid}` (all (x, y) tuples / floats, ring order), where `t1`/`t2` are
    the tangent points on the incoming/outgoing flank and `mid` is the arc's midpoint, or `None`
    if the ring does not have this structure.

    Why not fit each fillet's circle from its own points (iters 40/42/43, all falsified): the
    per-point windowed curvature classification that produces `arc_spans` systematically
    TRUNCATES each run -- measured on M6's star, the classified spans are 95.6 deg (tips) and
    30.9 deg (valleys) against true spans of 123.68 / 63.68 deg. A 31 deg arc of a 50 mm circle
    has a 1.8 mm sagitta, so the ~0.25 mm tessellation noise on the sample points moves the fitted
    radius by ~2 mm, and the straight edge that replaces the unclassified remainder chords across
    ~30 deg of real fillet (measured 1.16-2.74 mm of perpendicular error over a 250 mm flank).

    The flanks are the well-conditioned features instead: ~250 mm long and exactly planar in the
    solid, so a TLS line through the middle of each gap is ~100x better conditioned than the arc
    fit. Intersecting adjacent flank lines gives the true (unfilleted) corner; the inscribed
    fillet is then tangent to both flanks by construction, which leaves only its RADIUS free --
    one 1-D least-squares parameter recovered from the arc points, and even a poor recovery moves
    the surface by far less than a wrong centre would. The reconstructed arc spans the full fillet
    (tangent point to tangent point), not the classified fragment.

    `line_tol` is the perpendicular distance within which a gap point is accepted as belonging to
    the flank when the fit is grown outward from the gap's middle; it must be well under the
    fillet sagitta so leftover fillet points stay excluded."""
    arr = np.asarray(pts, dtype=float)
    n = len(arr)
    m = len(arc_spans)
    if m < 3 or n < 4 * m:
        return None

    lines = []
    extra = [[] for _ in range(m)]   # gap points the flank fit rejected -> they are arc points
    for i in range(m):
        a = arc_spans[i][1]
        b = arc_spans[(i + 1) % m][0]
        idx = list(range(a, b + 1)) if b >= a else list(range(a, n)) + list(range(0, b + 1))
        gap = arr[idx]
        if len(gap) < 4:
            return None
        # Consensus fit seeded from every pair of gap points, scored by (inlier count, inlier
        # span). The gap always carries points that belong to the neighbouring fillets -- the
        # curvature classification truncates the arc runs by ~30 deg -- and they sit at the two
        # ENDS, where they have the most leverage on a straight fit. Neither of the cheaper
        # schemes survives that: seeding from the gap's middle third fails because the short gaps
        # here hold only 12-14 points, and greedily peeling the worst residual can converge onto a
        # locally-straight subset of the FILLET instead (measured on M6: 4 of 12 flanks came out
        # with 2-3 inliers and up to 0.69 mm of error, which then dragged their corners and
        # radii). The gap is small (<= ~30 points), so the exhaustive O(k^3) search is free, and
        # since the flanks are exactly planar faces of the solid their true inliers agree to
        # tessellation precision -- the largest consistent subset is unambiguous.
        k = len(gap)
        best_score, keep = None, None
        for p in range(k - 1):
            for q in range(p + 2, k + 1):
                win = gap[p:q]
                cc, dd = _fit_line_tls(win)
                if np.abs(_cross2(win - cc, dd)).max() > line_tol:
                    continue
                proj = (win - cc) @ dd
                score = (float(proj.max() - proj.min()), q - p)
                if best_score is None or score > best_score:
                    best_score, keep = score, win
        if keep is None or len(keep) < 2:
            return None
        c, d = _fit_line_tls(keep)
        lines.append((c, d))
        # Every gap point the line rejected is a point of the fillet the classifier truncated:
        # the ones at the head of the gap belong to fillet i, the ones at the tail to fillet i+1.
        # Feeding them back roughly triples the sample the radius is fitted from and, more
        # importantly, extends it over the full arc instead of its middle third.
        off = np.abs(_cross2(gap - c, d)) > line_tol
        h = 0
        while h < len(off) and off[h]:
            h += 1
        t = len(off)
        while t > h and off[t - 1]:
            t -= 1
        extra[i].extend(idx[:h])
        extra[(i + 1) % m].extend(idx[t:])

    out = []
    for i in range(m):
        c1, d1 = lines[(i - 1) % m]     # flank arriving at fillet i
        c2, d2 = lines[i]               # flank leaving fillet i
        den = _cross2(d1, d2)
        if abs(den) < 1e-9:
            return None
        t = _cross2(c2 - c1, d2) / den
        corner = c1 + t * d1
        # unit directions pointing away from the corner, along each flank
        e1 = d1 * (1.0 if np.dot(c1 - corner, d1) > 0 else -1.0)
        e2 = d2 * (1.0 if np.dot(c2 - corner, d2) > 0 else -1.0)
        bis = e1 + e2
        nb = float(np.linalg.norm(bis))
        if nb < 1e-9:
            return None
        u = bis / nb
        cos_h = float(np.clip(np.dot(e1, u), -1.0, 1.0))
        sin_h = math.sqrt(max(1e-12, 1.0 - cos_h ** 2))

        a, b = arc_spans[i]
        idx = list(range(a, b + 1)) if b >= a else list(range(a, n)) + list(range(0, b + 1))
        ap = arr[sorted(set(idx) | set(extra[i]))]
        if len(ap) < 2:
            return None
        # Centre lies at corner + s*u; the tangency constraint makes the radius s*sin_h, so the
        # whole fillet is the single parameter s -- 1-D least squares on the arc's own points.
        # Bracket s from the arc's closest approach to the corner, which for a true tangent
        # fillet is exactly s*(1 - sin_h). Projecting the arc points' MEAN onto u instead
        # underestimates s badly at wide corners (it lands near s*(1-sin_h), which is s/6.7 at
        # M6's 116 deg valleys) and put the true value outside the search bracket.
        d_min = float(np.min(np.linalg.norm(ap - corner, axis=1)))
        s0 = d_min / max(1e-6, 1.0 - sin_h)
        if not np.isfinite(s0) or s0 <= 0.0:
            return None

        def resid(s):
            cen = corner + s * u
            return float(np.sum((np.linalg.norm(ap - cen, axis=1) - s * sin_h) ** 2))

        grid = np.linspace(max(1e-6, 0.2 * s0), 3.0 * s0, 201)
        s = float(grid[int(np.argmin([resid(g) for g in grid]))])
        step = float(grid[1] - grid[0])
        for _ in range(60):
            step *= 0.5
            for cand in (s - step, s + step):
                if cand > 1e-6 and resid(cand) < resid(s):
                    s = cand
        r = s * sin_h
        cen = corner + s * u
        tan_off = s * cos_h
        out.append({
            "center": (float(cen[0]), float(cen[1])),
            "radius": float(r),
            "t1": tuple(map(float, corner + e1 * tan_off)),
            "t2": tuple(map(float, corner + e2 * tan_off)),
            "mid": tuple(map(float, cen - r * u)),
        })
    return out


def fillet_ring_deviation(pts, fillets):
    """Max distance from every point of the raw ring `pts` to the piecewise arc/line curve
    described by `fillets` (output of `fit_fillet_ring`). The self-check that decides whether the
    reconstruction is trustworthy enough to loft, or whether to fall back to a dense polygon."""
    arr = np.asarray(pts, dtype=float)
    m = len(fillets)
    best = np.full(len(arr), np.inf)
    for i, f in enumerate(fillets):
        cen = np.asarray(f["center"], dtype=float)
        r = f["radius"]
        t1 = np.asarray(f["t1"], dtype=float)
        t2 = np.asarray(f["t2"], dtype=float)
        mid = np.asarray(f["mid"], dtype=float)
        # distance to the arc: radial if the point projects inside the arc's angular span,
        # otherwise to the nearer endpoint.
        v = arr - cen
        nv = np.linalg.norm(v, axis=1)
        nv = np.where(nv < 1e-12, 1e-12, nv)
        proj = cen + v / nv[:, None] * r
        inside = (np.linalg.norm(proj - mid, axis=1)
                  <= max(np.linalg.norm(t1 - mid), np.linalg.norm(t2 - mid)))
        d_arc = np.where(inside, np.abs(nv - r),
                         np.minimum(np.linalg.norm(arr - t1, axis=1),
                                    np.linalg.norm(arr - t2, axis=1)))
        best = np.minimum(best, d_arc)
        # straight flank from this fillet's t2 to the next fillet's t1
        a = t2
        b = np.asarray(fillets[(i + 1) % m]["t1"], dtype=float)
        ab = b - a
        den = float(np.dot(ab, ab))
        if den < 1e-18:
            continue
        tt = np.clip(((arr - a) @ ab) / den, 0.0, 1.0)
        best = np.minimum(best, np.linalg.norm(arr - (a + tt[:, None] * ab), axis=1))
    return float(np.max(best))


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
