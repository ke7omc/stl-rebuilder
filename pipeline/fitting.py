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


# ---------------------------------------------------------------------------
# Joint tangent-fillet family fit for a smoothly-tapering star bore (M16 class).
#
# The problem this solves, and why every simpler decomposition failed (all measured on M16's
# own stations, HANDOFF.md "M16 status"): a two-family star ring's corner arcs carry only
# ~10-20 slice points each against 0.2-0.9 mm of tessellation noise, and radius is exquisitely
# sensitive to that noise on a shallow arc (a +-0.4 mm sagitta perturbation on a ~24 mm chord
# swings a 35 mm radius by -6/+8 mm), so (a) independent per-corner circle fits scatter
# station-to-station by tens of mm, (b) `fit_fillet_ring`'s flank-intersection corners are
# ill-conditioned on shallow valleys (radii 400-860 mm where truth is 30-52 mm), and (c) any
# scheme that CLASSIFIES points as arc-vs-flank before fitting bakes its boundary placement in
# as a systematic radius bias (~1-3 mm at the fore end, 1.65 mm surface error at z=6091).
#
# The joint fit removes all three at once:
#   * One ring is m alternating corner circles (center, radius) whose connecting flanks are
#     the common INTERNAL tangent of each consecutive circle pair (tips convex, valleys
#     concave, so centers sit on opposite sides of every flank) -- tangency is exact by
#     construction and no flank line is ever intersected, so the shallow-valley degeneracy
#     cannot occur.
#   * The residual is the distance to the BOUNDED piecewise curve (arc clamped to its tangent
#     span, flank clamped to its segment), with the nearest-feature choice made inside the
#     fit -- the arc/flank boundary is a solved unknown, not a prior classification. Bounding
#     matters: past a tangent point an extended circle hugs the flank only quadratically
#     (d ~ s^2/2r), so at 0.5 mm noise an UNbounded fit can trade ~sqrt(2*r*noise) ~ 5 mm of
#     arc for flank at sub-noise cost, which measurably walked radii off by several mm in
#     both directions; bounded endpoints make that trade cost linear in s.
#   * All stations are solved TOGETHER with every parameter linear in z. This is not a
#     smoothing convenience but the true model class: the truth-style construction is a ruled
#     loft between two end wires, whose intermediate section is the per-edge linear blend of
#     the ends -- and the blend of two circular arcs under matched parameters is (to second
#     order in their span mismatch) an arc with center and radius linear in z. Measured: this
#     family explains M16's mesh to rms 0.020 mm / p99 0.084 / max 0.195 over 15131 vertices.
#     (A wire rebuilt from linearly-INTERPOLATED sharp-polygon parameters is the wrong family:
#     its fillet centers travel v(z) + f(z)/sin_h(z) * bis(z), nonlinear in z -- measured 2.1 mm
#     off the actual loft surface mid-span while the linear-center family tracks it at 0.014.)
#
# The fit runs on mesh VERTICES, not slice points: slice points lie on facet chords,
# displaced one-sidedly toward the local curvature center by up to the tessellation sagitta
# (~chord_tol), which biased slice-fit arcs ~0.35-0.44 mm inward at every tip; vertices lie on
# the tessellated surface itself. Slice rings are still used for segmentation and seeding.
# ---------------------------------------------------------------------------


def detect_ring_extrema(pts, chord_tol: float):
    """Segment a closed CCW star-like ring by the alternating extrema of r(theta): returns
    [(theta, is_max), ...] sorted by theta, or [] when no clean alternating structure exists.

    This is the segmentation `detect_arc_runs` cannot do here (measured on M16: its curvature
    elbow needs a 3x gap between consecutive sorted local radii, but a tapered star's local
    radii are a continuum, max ratio 1.3-1.7). Extrema of r(theta) need no curvature scale
    separation at all -- a lobe tip is a maximum and a valley a minimum regardless of how
    gently the fillets blend -- and recovered 20/20 corners on 73/73 M16 stations.

    A light circular moving average (5 samples) suppresses vertex-level noise before the
    extremum scan; plateau runs of the same extremum type collapse to their most extreme
    member, which also enforces strict max/min alternation. `chord_tol` guards prominence via
    persistence-style PRUNING (see the loop below): an adjacent max/min pair whose radial gap
    is under 2*chord_tol is tessellation ripple, not a lobe, and is removed as a pair --
    never by rejecting the whole ring, which threw away 68% of otherwise-clean synthetic
    rings over one noise ripple on a flank."""
    xy = np.asarray(pts, dtype=float)
    th = np.arctan2(xy[:, 1], xy[:, 0])
    order = np.argsort(th)
    th_s = th[order]
    r_s = np.hypot(xy[order, 0], xy[order, 1])
    n = len(r_s)
    if n < 24:
        return []
    w = 5
    kern = np.ones(w) / w
    r_sm = np.convolve(np.concatenate([r_s[-w:], r_s, r_s[:w]]), kern, mode="same")[w:-w]

    ext = []
    for i in range(n):
        window = r_sm[[(i + k) % n for k in range(-3, 4)]]
        if r_sm[i] == window.max():
            ext.append((th_s[i], True, r_sm[i]))
        elif r_sm[i] == window.min():
            ext.append((th_s[i], False, r_sm[i]))
    if not ext:
        return []
    ext.sort(key=lambda e: e[0])
    out = []
    for e in ext:
        if out and e[1] == out[-1][1]:
            if (e[1] and e[2] > out[-1][2]) or (not e[1] and e[2] < out[-1][2]):
                out[-1] = e
        else:
            out.append(e)
    if len(out) >= 2 and out[0][1] == out[-1][1]:
        if (out[0][1] and out[-1][2] > out[0][2]) or \
                (not out[0][1] and out[-1][2] < out[0][2]):
            out[0] = out[-1]
        out.pop()
    # prominence pruning, persistence-style: repeatedly remove the ADJACENT max/min pair with
    # the smallest radial gap while that gap is under 2*chord_tol -- removing the pair (not
    # one member) preserves alternation, and the survivors' own gaps only grow. Rejecting the
    # whole ring on the first weak extremum instead (the first implementation) threw away
    # 68% of otherwise-clean synthetic rings over one noise ripple on a flank; pruning keeps
    # the ring and drops only the ripple.
    while len(out) >= 4:
        m = len(out)
        gaps = [abs(out[i][2] - out[(i + 1) % m][2]) for i in range(m)]
        i_min = int(np.argmin(gaps))
        if gaps[i_min] >= 2.0 * chord_tol:
            break
        j = (i_min + 1) % m
        for idx in sorted((i_min, j), reverse=True):
            out.pop(idx)
        # removing two neighbours can leave a same-type adjacency; re-collapse it
        i = 0
        while len(out) >= 2 and i < len(out):
            k = (i + 1) % len(out)
            if k != i and out[i][1] == out[k][1]:
                if (out[i][1] and out[k][2] > out[i][2]) or \
                        (not out[i][1] and out[k][2] < out[i][2]):
                    out[i] = out[k]
                out.pop(k)
            else:
                i += 1
    if len(out) < 6 or len(out) % 2 != 0:
        return []
    return [(float(e[0]), bool(e[1])) for e in out]


def _corner_regions(x, pts_xy, order, sigs, perp_signs, m):
    """Assign each point to a CORNER REGION: region j holds corner `order[j]`'s arc and is
    bounded by the midpoints of its two adjacent flanks, computed from `x`'s intercept
    parameters. Returns the per-point region index array, or None when any flank tangent
    fails to construct (|q| >= 1 -- overlapping seed circles, not this shape class).

    Boundaries at FLANK MIDPOINTS, never at the r(theta) extrema: on an asymmetric corner
    the curve's extremum is displaced onto the flank (the shallower flank's perpendicular
    foot, measured 4-6 deg off the arc on the synthetic two-family fixture), so
    extremum-bounded sectors put real arc/flank pieces into a neighbouring sector whose
    candidate feature list does not contain them -- points ON the true curve then score
    phantom residuals (measured: up to 13.1 mm, enough to make the true parameter set lose
    to a degenerate one). A flank midpoint is the point of the whole curve FARTHEST from
    both arcs, so region boundaries there tolerate extremum displacement up to half a flank
    length, and each region needs exactly three candidate features (its arc, its two
    half-flanks) instead of a widening neighbourhood."""
    th_pt = np.arctan2(pts_xy[:, 1], pts_xy[:, 0])
    bounds = np.empty(m)
    for j in range(m):
        ka, kb = order[j], order[(j + 1) % m]
        ca = np.array([x[6 * ka], x[6 * ka + 2]])
        cb = np.array([x[6 * kb], x[6 * kb + 2]])
        ra, rb = x[6 * ka + 4], x[6 * kb + 4]
        d = cb - ca
        D = float(np.linalg.norm(d))
        if D < 1e-9:
            return None
        dh = d / D
        q = (sigs[kb] * rb - sigs[ka] * ra) / D
        if abs(q) >= 1.0:
            return None
        sq = math.sqrt(1.0 - q * q)
        pv = np.array([-dh[1], dh[0]])
        n = q * dh + perp_signs[j] * sq * pv
        t_a = ca - sigs[ka] * ra * n
        t_b = cb - sigs[kb] * rb * n
        mid = 0.5 * (t_a + t_b)
        bounds[j] = math.atan2(mid[1], mid[0])
    bidx = np.argsort(bounds)
    bsorted = bounds[bidx]
    pos = (np.searchsorted(bsorted, th_pt, side="right") - 1) % m
    return (bidx[pos] + 1) % m


def _tangent_ring_residuals(x, P, Zc, reg, order, sigs, perp_signs, m):
    """Distance from each 2D point `P[i]` (at centered height `Zc[i]`) to the bounded
    piecewise tangent-fillet curve of its corner region: the region's arc (bounded by both
    of its tangent points) and its two adjacent flank segments, whichever is nearest --
    evaluated with every corner's (cx, cy, r) linear in z.

    `x`: 6*m parameters, corner k (reference order) at x[6k:6k+6] =
    (cx0, cx_slope, cy0, cy_slope, r0, r_slope). `reg[i]`: the point's corner region
    (`_corner_regions` -- boundaries at flank midpoints, see there for why). `order` maps
    region position to reference corner index. `sigs`: +-1 signed side of each corner's
    center relative to its flank tangent lines (alternating for a star -- every flank is an
    internal common tangent). `perp_signs`: which of the two internal tangents is the real
    flank, per flank (`resolve_signs`' closed-form test).

    Distances are to BOUNDED features (arc clamped to its tangent span, flank clamped to
    its segment). Bounding is what makes the arc/flank boundary an honest solved unknown:
    past a tangent point an extended circle hugs the flank only quadratically (d ~ s^2/2r),
    so at 0.5 mm noise an UNbounded fit could trade ~sqrt(2*r*noise) ~ 5 mm of arc for
    flank at sub-noise cost -- measured radius walks of several mm in both directions --
    while bounded endpoints price that trade linearly in s."""
    def corner(kk):
        base = 6 * kk
        cx = x[base] + x[base + 1] * Zc
        cy = x[base + 2] + x[base + 3] * Zc
        r = x[base + 4] + x[base + 5] * Zc
        return np.column_stack([cx, cy]), r

    def flank(flank_idx):
        """Per-point flank segment for flank index array `flank_idx` (flank j connects
        region-position j and j+1): clamped-segment distance and both tangent points."""
        ka = order[flank_idx % m]
        kb = order[(flank_idx + 1) % m]
        ca, ra = corner(ka)
        cb, rb = corner(kb)
        sa = sigs[ka]
        sb = sigs[kb]
        d = cb - ca
        D = np.maximum(np.linalg.norm(d, axis=1), 1e-12)
        dh = d / D[:, None]
        q = np.clip((sb * rb - sa * ra) / D, -1.0, 1.0)
        sq = np.sqrt(np.maximum(0.0, 1.0 - q * q))
        perp = np.column_stack([-dh[:, 1], dh[:, 0]])
        psn = perp_signs[flank_idx % m]
        nvec = q[:, None] * dh + (psn * sq)[:, None] * perp
        t_a = ca - (sa * ra)[:, None] * nvec
        t_b = cb - (sb * rb)[:, None] * nvec
        seg = t_b - t_a
        L2 = np.einsum("ij,ij->i", seg, seg)
        tpar = np.clip(np.einsum("ij,ij->i", P - t_a, seg) / np.maximum(L2, 1e-12),
                       0.0, 1.0)
        foot = t_a + tpar[:, None] * seg
        return np.linalg.norm(P - foot, axis=1), t_a, t_b

    d_fl_prev, _t_pa, t_in = flank(reg - 1)   # incoming flank: its t_b is ON this arc
    d_fl_next, t_out, _t_nb = flank(reg)      # outgoing flank: its t_a is ON this arc

    k1 = order[reg]
    c1, r1 = corner(k1)

    def arc_dist(c, r, t_first, t_second):
        """Radial distance where the point projects inside the arc's span (the wedge
        between its two tangent radii), else distance to the nearer tangent point. The
        interior reference direction is the arc's OWN tangent-radius bisector
        (u1_hat + u2_hat), never the corner's extremum ray: when a corner's r(theta)
        extremum sits at a flank's perpendicular foot, the extremum ray is COLLINEAR with
        one tangent radius (measured on the synthetic fixture: side-test cross product
        0.012 against a point term of -154, a coin flip) and the test misclassified points
        well inside the arc, costing them a 4.2 mm phantom residual. The bisector is
        degenerate only for a span of ~pi, which no fillet-sized corner has."""
        v = P - c
        nv = np.linalg.norm(v, axis=1)
        rad = np.abs(nv - r)
        u1 = t_first - c
        u2 = t_second - c
        e = u1 / np.maximum(np.linalg.norm(u1, axis=1), 1e-12)[:, None] \
            + u2 / np.maximum(np.linalg.norm(u2, axis=1), 1e-12)[:, None]
        inside = np.ones(len(P), dtype=bool)
        d_end = np.full(len(P), np.inf)
        for u_t in (u1, u2):
            s_ref = u_t[:, 0] * e[:, 1] - u_t[:, 1] * e[:, 0]
            cr = u_t[:, 0] * v[:, 1] - u_t[:, 1] * v[:, 0]
            inside &= (cr * s_ref >= 0.0)
        for t_on in (t_first, t_second):
            d_end = np.minimum(d_end, np.linalg.norm(P - t_on, axis=1))
        return np.where(inside, rad, d_end)

    d_arc = arc_dist(c1, r1, t_in, t_out)
    return np.minimum(d_arc, np.minimum(d_fl_prev, d_fl_next))


def _theil_sen_line(z, v):
    """Median-of-pairwise-slopes linear fit -> (intercept, slope). Robust to the one-sided
    per-station radius outliers a shallow noisy arc produces (median-based, tolerates ~29%
    contamination) where sigma-clipped least squares measurably was not."""
    z = np.asarray(z, dtype=float)
    v = np.asarray(v, dtype=float)
    slopes = []
    for i in range(len(z)):
        dz = z[i + 1:] - z[i]
        ok = np.abs(dz) > 1e-9
        if ok.any():
            slopes.extend(((v[i + 1:] - v[i])[ok] / dz[ok]).tolist())
    if not slopes:
        return float(np.median(v)), 0.0
    b = float(np.median(slopes))
    a = float(np.median(v - b * z))
    return a, b


class TaperedFilletModel:
    """The solved linear-in-z tangent-fillet family: evaluate `fillets_at(z)` to get the
    ring-ordered fillet list (`center`/`radius`/`t1`/`t2`/`mid`, the same shape
    `fit_fillet_ring` returns) for `solids._fillet_ring_wire`, or `residuals_at` to measure
    how well the model explains an independent point set."""

    def __init__(self, x, zmid, ref_th, ref_ismax, sigs, perp_signs, stats):
        self.x = np.asarray(x, dtype=float)
        self.zmid = float(zmid)
        self.ref_th = np.asarray(ref_th, dtype=float)
        self.ref_ismax = np.asarray(ref_ismax, dtype=bool)
        self.sigs = np.asarray(sigs, dtype=float)
        self.perp_signs = np.asarray(perp_signs, dtype=float)
        self.stats = dict(stats)
        self.m = len(self.ref_th)

    def _params_at(self, z: float):
        zc = z - self.zmid
        m = self.m
        cs = np.empty((m, 2))
        rs = np.empty(m)
        for k in range(m):
            base = 6 * k
            cs[k, 0] = self.x[base] + self.x[base + 1] * zc
            cs[k, 1] = self.x[base + 2] + self.x[base + 3] * zc
            rs[k] = self.x[base + 4] + self.x[base + 5] * zc
        return cs, rs

    def fillets_at(self, z: float, min_flank: float = 1e-6):
        """Ring-ordered (by theta) fillet dicts at height `z`, or None when the evaluated
        parameters do not form a geometrically valid tangent ring there (a non-positive
        radius, a tangent construction with no real solution, a vanishing flank, or a
        degenerate arc) -- the caller treats None as "this rung unavailable"."""
        m = self.m
        cs, rs = self._params_at(z)
        if np.any(rs <= 1e-3):
            return None
        order = np.argsort(self.ref_th)
        tangents = []
        for j in range(m):
            k = order[j]
            kn = order[(j + 1) % m]
            c1, r1, s1 = cs[k], rs[k], self.sigs[k]
            c2, r2, s2 = cs[kn], rs[kn], self.sigs[kn]
            d = c2 - c1
            D = float(np.linalg.norm(d))
            if D < 1e-9:
                return None
            dh = d / D
            q = (s2 * r2 - s1 * r1) / D
            if abs(q) >= 1.0:
                return None
            sq = math.sqrt(1.0 - q * q)
            pv = np.array([-dh[1], dh[0]])
            n = q * dh + self.perp_signs[j] * sq * pv
            tangents.append((c1 - s1 * r1 * n, c2 - s2 * r2 * n))
        fillets = []
        for j in range(m):
            k = order[j]
            t1 = tangents[(j - 1) % m][1]   # previous flank's tangent point ON this circle
            t2 = tangents[j][0]             # next flank's tangent point ON this circle
            c, r = cs[k], rs[k]
            u1 = (t1 - c) / max(np.linalg.norm(t1 - c), 1e-12)
            u2 = (t2 - c) / max(np.linalg.norm(t2 - c), 1e-12)
            bis = u1 + u2
            nb = float(np.linalg.norm(bis))
            if nb < 1e-9:
                return None                 # arc spans ~pi: not a fillet-sized corner
            mid = c + r * (bis / nb)
            # the flank OUT of this corner must have real length on this wire (a zero-length
            # flank would drop an edge and break the matched two-wire topology downstream)
            nt1 = tangents[j][1]
            if float(np.linalg.norm(nt1 - t2)) < min_flank:
                return None
            fillets.append({
                "center": (float(c[0]), float(c[1])),
                "radius": float(r),
                "t1": (float(t1[0]), float(t1[1])),
                "t2": (float(t2[0]), float(t2[1])),
                "mid": (float(mid[0]), float(mid[1])),
            })
        return fillets

    def residuals_at(self, pts_xy, zs):
        """Bounded-curve distances of arbitrary points (each at its own z) to the model."""
        P = np.asarray(pts_xy, dtype=float)
        Zc = np.asarray(zs, dtype=float) - self.zmid
        order = np.argsort(self.ref_th)
        reg = _corner_regions(self.x, P, order, self.sigs, self.perp_signs, self.m)
        if reg is None:
            raise ValueError("model flank construction degenerate; no residuals")
        return _tangent_ring_residuals(self.x, P, Zc, reg, order, self.sigs,
                                       self.perp_signs, self.m)


def fit_tapered_fillet_model(rings, vertices, chord_tol: float,
                             max_nfev: int = 300):
    """Fit the linear-in-z tangent-fillet family to a tapered star bore zone.

    `rings`: [(z, pts_Nx2), ...] closed CCW station rings (segmentation + seeding only).
    `vertices`: (K, 3) mesh vertices already restricted to the bore surface of this zone (the
    fit data -- see the module comment above for why vertices, not slice points).
    Returns a `TaperedFilletModel` or None (structure absent / seed failed / solver failed).
    `model.stats` carries the honest acceptance inputs: conforming-station fraction and the
    pooled vertex residual rms/p99/max -- the CALLER decides whether they clear its own
    thresholds; this function only refuses on structural failure, never on accuracy."""
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix

    if len(rings) < 5 or len(vertices) < 100:
        return None

    # 1) segmentation vote: the corner count must be a stable property of the zone, not of
    # one lucky slice. Conforming stations (modal even count) are the seed set.
    seg = []
    for z, pts in rings:
        corners = detect_ring_extrema(pts, chord_tol)
        seg.append((z, pts, corners))
    counts = [len(c) for _z, _p, c in seg if c]
    if not counts:
        return None
    m = int(np.bincount(counts).argmax())
    conforming = [(z, pts, c) for z, pts, c in seg if len(c) == m]
    frac = len(conforming) / len(rings)
    if m < 6 or len(conforming) < 5:
        return None

    # reference corner set: the median conforming station (an interior ring, least likely to
    # be distorted by an end effect -- the `_pick_best_ring` convention)
    ref_z, _ref_pts, ref_corners = conforming[len(conforming) // 2]
    ref_th = np.array([c[0] for c in ref_corners])
    ref_ismax = np.array([c[1] for c in ref_corners])
    ext = np.column_stack([np.cos(ref_th), np.sin(ref_th)])
    order = np.argsort(ref_th)
    sigs0 = np.where(ref_ismax, 1.0, -1.0)

    zs_conf = np.array([z for z, _p, _c in conforming])
    zmid = 0.5 * (float(zs_conf.min()) + float(zs_conf.max()))

    def kasa(P):
        A = np.column_stack([P[:, 0], P[:, 1], np.ones(len(P))])
        b = P[:, 0] ** 2 + P[:, 1] ** 2
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        cx, cy = sol[0] / 2.0, sol[1] / 2.0
        return cx, cy, math.sqrt(max(sol[2] + cx ** 2 + cy ** 2, 1e-12))

    def seed_circles(pts, corners):
        """One seed circle per corner: consensus circumcircle over a deterministic grid of
        point TRIPLES from the corner's window, with radius and convexity priors, refit by
        Kasa on the winner's inliers.

        Two cheaper schemes were tried and measurably fail on an ASYMMETRIC corner (very
        different neighbouring lobes; synthetic two-family star, 0.2 mm noise): (a) a plain
        window-Kasa fit -- valley seeds of 135-1000 mm against a truth of 36.5, because the
        window catches mostly flank points; (b) candidate circles along the extremum ray
        (center at p_ext +- r*ray, exploiting that a CIRCLE's r(theta) minimum lies on its
        origin-center line) -- 28.8 mm wrong on the same valleys, because with asymmetric
        flanks the CURVE's r(theta) extremum is not on the arc at all: it is the
        perpendicular foot of the shallower flank, ~4 deg away. A bad seed then parks the
        per-ring LM in the 'chamfer' local minimum (a ~170 mm arc grazing the tangent
        region) that a local optimizer cannot leave.

        The triple vote has neither failure: a triple drawn fully from the arc reproduces
        it, while flank-contaminated or flank-only triples give oversized or near-collinear
        circumcircles that the radius prior (0.6x the corner's own chord scale) and the
        convexity prior (a tip's center lies radially inside its arc, a valley's outside)
        reject -- contamination costs candidates rather than biasing a fit. The triple grid
        is deterministic (spread thirds of the theta-ordered window), never randomized."""
        th = np.arctan2(pts[:, 1], pts[:, 0])
        r_all = np.hypot(pts[:, 0], pts[:, 1])
        band = 2.0 * chord_tol
        out = []
        for k in range(m):
            t0, is_max_k = corners[k]
            gap_p = (t0 - corners[(k - 1) % m][0]) % (2.0 * math.pi)
            gap_n = (corners[(k + 1) % m][0] - t0) % (2.0 * math.pi)
            d = np.abs(((th - t0 + math.pi) % (2.0 * math.pi)) - math.pi)
            sel = d <= 0.45 * min(gap_p, gap_n)
            if sel.sum() < 6:
                return None
            dw = ((th[sel] - t0 + math.pi) % (2.0 * math.pi)) - math.pi
            W = pts[sel][np.argsort(dw)]     # theta-ordered: spread indices spread in space
            near = d <= 0.05 * min(gap_p, gap_n)
            r_ext = float(np.median(r_all[near])) if near.any() \
                else float(np.median(r_all[sel]))
            # corner scale: distance between the adjacent extremum points
            t_prev = corners[(k - 1) % m][0]
            t_next = corners[(k + 1) % m][0]
            near_p = np.abs(((th - t_prev + math.pi) % (2.0 * math.pi)) - math.pi) \
                <= 0.05 * gap_p
            near_n = np.abs(((th - t_next + math.pi) % (2.0 * math.pi)) - math.pi) \
                <= 0.05 * gap_n
            r_prev = float(np.median(r_all[near_p])) if near_p.any() else r_ext
            r_next = float(np.median(r_all[near_n])) if near_n.any() else r_ext
            p0 = np.array([r_ext * math.cos(t0), r_ext * math.sin(t0)])
            p_prev = np.array([r_prev * math.cos(t_prev), r_prev * math.sin(t_prev)])
            p_next = np.array([r_next * math.cos(t_next), r_next * math.sin(t_next)])
            scale = 0.5 * (np.linalg.norm(p_prev - p0) + np.linalg.norm(p_next - p0))
            r_cap = max(4.0 * chord_tol, 0.6 * scale)
            n_w = len(W)
            grid = np.unique(np.linspace(0, n_w - 1, 12).astype(int))
            third = max(1, len(grid) // 3)
            best = None
            for i in grid[:third]:
                for j in grid[third:2 * third]:
                    for kk in grid[2 * third:]:
                        a, b, c = W[i], W[j], W[kk]
                        den = 2.0 * ((a[0] - c[0]) * (b[1] - c[1])
                                     - (b[0] - c[0]) * (a[1] - c[1]))
                        if abs(den) < 1e-9:
                            continue
                        ux = ((a[0] ** 2 - c[0] ** 2 + a[1] ** 2 - c[1] ** 2)
                              * (b[1] - c[1])
                              - (b[0] ** 2 - c[0] ** 2 + b[1] ** 2 - c[1] ** 2)
                              * (a[1] - c[1])) / den
                        uy = ((b[0] ** 2 - c[0] ** 2 + b[1] ** 2 - c[1] ** 2)
                              * (a[0] - c[0])
                              - (a[0] ** 2 - c[0] ** 2 + a[1] ** 2 - c[1] ** 2)
                              * (b[0] - c[0])) / den
                        cen = np.array([ux, uy])
                        rc = float(np.linalg.norm(a - cen))
                        if rc < 2.0 * chord_tol or rc > r_cap:
                            continue
                        if is_max_k != (float(np.linalg.norm(cen)) < r_ext):
                            continue
                        resid = np.abs(np.linalg.norm(W - cen, axis=1) - rc)
                        score = int((resid < band).sum())
                        if best is None or score > best[0]:
                            best = (score, cen, rc, resid < band)
            if best is None:
                return None
            score, cen, rc, inl = best
            if score >= 5:
                cx, cy, R = kasa(W[inl])
                # a refit that runs away from its own candidate is contaminated after all
                # -- keep the voted candidate circle instead
                if not (0.3 * rc <= R <= 3.0 * rc):
                    cx, cy, R = float(cen[0]), float(cen[1]), float(rc)
            else:
                cx, cy, R = float(cen[0]), float(cen[1]), float(rc)
            out.append((cx, cy, R))
        return out

    def ring_residual(x, pts, reg, sigs, ps_vec):
        return _tangent_ring_residuals(x, pts, np.zeros(len(pts)), reg, order, sigs,
                                       ps_vec, m)

    def resolve_signs(x0, pts):
        """The remaining discrete convention: one perp sign PER FLANK -- which of the two
        internal common tangents of its circle pair is the real flank. (The other apparent
        freedom, a global flip of every `sig`, is redundant: (-sig, -perp) produces the
        identical line, so `sf` is fixed at +1 and only the perp branch is chosen.)

        A single GLOBAL perp sign is measurably wrong on an asymmetric star: on the
        synthetic two-family fixture some flanks need the opposite branch, and forcing one
        branch everywhere corrupted those flank segments and the arc bounds derived from
        them badly enough that the TRUE parameter set scored a 2.1 mm p99 against its own
        noise-free curve -- the fit then preferred a wrong small-radius 'chamfer' solution.
        And choosing per flank by nearest-to-mid-sector-data was fragile against seed error
        (one mischosen flank cost 1.19 mm rms on the same fixture). The closed-form test
        needs neither data nor good radii: the true flank's tangent point lies on the ARC's
        side of its circle -- the outer half of a tip circle, the inner half of a valley
        (worked example: tip branch tangent points at |t| = 516 vs 437.6 on a circle
        spanning 437-529; dot(t - c, mid_dir) separates them with margin) -- while the
        mirrored branch lands on the opposite half. Both endpoints vote; they agree on any
        remotely sane seed."""
        ps_vec = np.ones(m)
        for j in range(m):
            ka, kb = order[j], order[(j + 1) % m]
            ca = np.array([x0[6 * ka], x0[6 * ka + 2]])
            cb = np.array([x0[6 * kb], x0[6 * kb + 2]])
            ra, rb = x0[6 * ka + 4], x0[6 * kb + 4]
            d = cb - ca
            D = max(float(np.linalg.norm(d)), 1e-12)
            dh = d / D
            q = float(np.clip((sigs0[kb] * rb - sigs0[ka] * ra) / D, -1.0, 1.0))
            sq = math.sqrt(max(0.0, 1.0 - q * q))
            pv = np.array([-dh[1], dh[0]])
            e_a = ext[ka] * (1.0 if ref_ismax[ka] else -1.0)
            e_b = ext[kb] * (1.0 if ref_ismax[kb] else -1.0)
            score = []
            for cand in (1.0, -1.0):
                n = q * dh + cand * sq * pv
                t_a = ca - sigs0[ka] * ra * n
                t_b = cb - sigs0[kb] * rb * n
                score.append(float((t_a - ca) @ e_a) + float((t_b - cb) @ e_b))
            ps_vec[j] = 1.0 if score[0] >= score[1] else -1.0
        return 1.0, ps_vec

    # 2) per-ring joint fits (intercepts only) on a spread of conforming stations -- these
    # exist to seed the global stage, so ~20 stations are plenty and keep this stage ~2 s.
    step = max(1, len(conforming) // 20)
    seeds_z, seeds_c, seeds_r = [], [], []
    signs = None
    intercept_free = np.zeros(6 * m, dtype=bool)
    intercept_free[0::6] = intercept_free[2::6] = intercept_free[4::6] = True
    for z, pts, corners in conforming[::step]:
        th_c = np.array([c[0] for c in corners])
        circ = seed_circles(pts, corners)
        if circ is None:
            continue
        # correspondence: this station's corner nearest (mod 2*pi) each reference corner --
        # valid because this bore class does not twist (the ruled loft has no rotation term;
        # a collision, i.e. two reference corners claiming one local corner, drops the ring)
        idx = []
        for k in range(m):
            d = np.abs(((th_c - ref_th[k] + math.pi) % (2.0 * math.pi)) - math.pi)
            idx.append(int(np.argmin(d)))
        if len(set(idx)) != m:
            continue
        x0 = np.zeros(6 * m)
        for k in range(m):
            cx, cy, R = circ[idx[k]]
            x0[6 * k], x0[6 * k + 2], x0[6 * k + 4] = cx, cy, R
        if signs is None:
            signs = resolve_signs(x0, pts)
            if signs is None:
                return None
        sf, ps_vec = signs
        # region assignment from this ring's own seeds, fixed for the whole LM solve --
        # boundaries at flank midpoints move negligibly over an LM's parameter walk
        reg_ring = _corner_regions(x0, pts, order, sigs0 * sf, ps_vec, m)
        if reg_ring is None:
            continue

        def res_free(xf, _x0=x0, _pts=pts, _reg=reg_ring):
            xx = _x0.copy()
            xx[intercept_free] = xf
            return ring_residual(xx, _pts, _reg, sigs0 * sf, ps_vec)

        # radius intercepts bounded positive: the tangent formula only ever uses sig*r, so
        # a NEGATIVE radius mimics the mirror-signed corner -- a parasitic solution the
        # residual barely penalizes (the vanished arc's points get absorbed by the two
        # flanks' extended segments at a shallow corner; measured on the synthetic blend
        # fixture, one valley converged to r = -33.06 with the pooled rms unchanged).
        # Bounding r >= 2*chord_tol removes that basin; a fillet under 2*chord_tol is below
        # the tessellation's own resolution and unrecoverable regardless.
        lb = np.full(intercept_free.sum(), -np.inf)
        lb[2::3] = 2.0 * chord_tol       # free vector is [cx0, cy0, r0] per corner
        try:
            sol = least_squares(res_free, np.maximum(x0[intercept_free],
                                np.where(np.isfinite(lb), lb + 1e-9, -np.inf)),
                                bounds=(lb, np.inf), method="trf",
                                x_scale="jac", ftol=1e-9, xtol=1e-9, max_nfev=120)
        except Exception:
            continue
        xr = x0.copy()
        xr[intercept_free] = sol.x
        seeds_z.append(z)
        seeds_c.append(np.column_stack([xr[0::6], xr[2::6]]))
        seeds_r.append(xr[4::6])
    if len(seeds_z) < 5 or signs is None:
        return None
    sf, ps_vec = signs
    sigs = sigs0 * sf
    perp_signs = np.asarray(ps_vec, dtype=float)
    seeds_z = np.array(seeds_z)
    seeds_c = np.array(seeds_c)
    seeds_r = np.array(seeds_r)

    # 3) Theil-Sen the per-ring intercepts into a linear-in-z warm start
    x0 = np.empty(6 * m)
    for k in range(m):
        for j, series in enumerate((seeds_c[:, k, 0], seeds_c[:, k, 1], seeds_r[:, k])):
            a, b = _theil_sen_line(seeds_z - zmid, series)
            x0[6 * k + 2 * j] = a
            x0[6 * k + 2 * j + 1] = b

    # 4) the global solve, on vertices. Region assignment comes from the warm start and is
    # fixed for the solve (boundaries sit at flank midpoints -- half a flank of margin).
    V = np.asarray(vertices, dtype=float)
    P = V[:, :2].copy()
    Zc = V[:, 2] - zmid
    reg = _corner_regions(x0, P, order, sigs, perp_signs, m)
    if reg is None:
        return None

    # sparsity: a residual sees its region's corner and both neighbours (the two flanks)
    k_prev = order[(reg - 1) % m]
    k_own = order[reg]
    k_next = order[(reg + 1) % m]
    S = lil_matrix((len(P), 6 * m), dtype=np.uint8)
    for kk in range(m):
        rows = np.where((k_prev == kk) | (k_own == kk) | (k_next == kk))[0]
        for col in range(6):
            S[rows, 6 * kk + col] = 1

    # Feasibility barriers, appended as extra residual rows. A shallow valley's radius is
    # weakly identified (its miter offset is f*(1/sin_h - 1) ~ 0.016*f at ~160 deg interior
    # angles, so tens of mm of radius move the curve by ~0.1 mm), and unconstrained the
    # solver parks such radii anywhere in the noise-equivalence class -- measured on the
    # synthetic blend fixture: a valley slope that ran r(z) NEGATIVE 400 mm inside the
    # fitted span, and 'chamfer' solutions whose r_a + r_b exceeded the center distance so
    # the internal tangent stopped existing; either way `fillets_at` later refuses a wire
    # the data never disqualified. The barriers keep the whole fitted span constructible --
    # r(z) >= 2*chord_tol and (r_a + r_b) <= 0.95 * D at both span ends -- while costing
    # exactly nothing wherever the data already decides (they are zero off the boundary).
    # The 10x weight makes a 1 mm violation cost 10 mm of residual, dominating noise.
    z_ends = (float(zs_conf.min()) - zmid, float(zs_conf.max()) - zmid)
    pen_w = 10.0

    def feasibility_pen(x):
        pens = np.empty(2 * (m + m))
        i = 0
        for z_e in z_ends:
            r_e = x[4::6] + x[5::6] * z_e
            for k in range(m):
                pens[i] = pen_w * max(0.0, 2.0 * chord_tol - r_e[k])
                i += 1
            cx_e = x[0::6] + x[1::6] * z_e
            cy_e = x[2::6] + x[3::6] * z_e
            for j in range(m):
                ka, kb = order[j], order[(j + 1) % m]
                D = math.hypot(cx_e[kb] - cx_e[ka], cy_e[kb] - cy_e[ka])
                pens[i] = pen_w * max(0.0, (r_e[ka] + r_e[kb]) - 0.95 * D)
                i += 1
        return pens

    def global_residual(x):
        return np.concatenate([
            _tangent_ring_residuals(x, P, Zc, reg, order, sigs, perp_signs, m),
            feasibility_pen(x)])

    # sparsity rows for the barriers: each depends on its corner pair's parameters
    n_pen = 4 * m
    S_full = lil_matrix((len(P) + n_pen, 6 * m), dtype=np.uint8)
    S_full[: len(P)] = S
    row = len(P)
    for z_e in z_ends:
        for k in range(m):
            S_full[row, 6 * k + 4] = 1
            S_full[row, 6 * k + 5] = 1
            row += 1
        for j in range(m):
            for kk in (order[j], order[(j + 1) % m]):
                for col in range(6):
                    S_full[row, 6 * kk + col] = 1
            row += 1

    # positive-radius hard bound as in the per-ring stage (the sign-flip parasitic basin --
    # see there); the intercept is r at zmid, so this pins every radius positive mid-span
    # and the barriers extend that to the span ends.
    lb = np.full(6 * m, -np.inf)
    lb[4::6] = 2.0 * chord_tol
    x0 = np.maximum(x0, np.where(np.isfinite(lb), lb + 1e-9, -np.inf))
    try:
        sol = least_squares(global_residual, x0, jac_sparsity=S_full.tocsr(),
                            method="trf", bounds=(lb, np.inf), x_scale="jac",
                            ftol=1e-10, xtol=1e-10, max_nfev=max_nfev)
    except Exception:
        return None
    res = np.abs(sol.fun[: len(P)])   # data rows only -- the barriers are not residuals
    stats = dict(
        n_corners=m,
        conforming_frac=float(frac),
        n_seed_rings=int(len(seeds_z)),
        n_vertices=int(len(P)),
        rms=float(np.sqrt(np.mean(res ** 2))),
        p99=float(np.percentile(res, 99)),
        max=float(res.max()),
    )
    return TaperedFilletModel(sol.x, zmid, ref_th, ref_ismax, sigs, perp_signs, stats)
