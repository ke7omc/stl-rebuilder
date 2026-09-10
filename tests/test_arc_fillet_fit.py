"""Unit tests for the joint tangent-fillet family fit (`fitting.fit_tapered_fillet_model`,
`fitting.detect_ring_extrema`) and its analytic loft builder
(`solids.build_tapered_fillet_loft_solid`) -- the `"loft_arcs"` rung.

Synthetic fixtures here are built by an INDEPENDENT construction (sharp polygon + classic
corner-fillet geometry, the same math `harness/generators._two_family_star_wire` encodes via
BRepFilletAPI) so the fit is never tested against its own machinery. The end-to-end selector
behavior on real milestone data lives in tests/test_taper_loft.py."""
import math

import numpy as np
from OCP.BRepCheck import BRepCheck_Analyzer

from pipeline import fitting, solids


def _star_fillets(z, n_pairs=3, R_tipA0=480.0, dRA=140.0, R_tipB0=400.0, dRB=60.0,
                  R_val0=380.0, dRV=30.0, f_tipA0=40.0, dfA=12.0, f_tipB0=30.0, dfB=4.0,
                  f_val0=35.0, dfV=3.0, z0=0.0, z1=1000.0):
    """Tangent-fillet description of a two-family star at height z, all parameters linear in
    z -- an independent reimplementation of the truth generator's sharp-polygon-plus-fillet
    construction."""
    t = (z - z0) / (z1 - z0)
    n_v = 4 * n_pairs
    verts = []
    for i in range(n_v):
        ang = 2.0 * math.pi * i / n_v
        if i % 2 == 0:
            is_a = (i // 2) % 2 == 0
            R = (R_tipA0 + dRA * t) if is_a else (R_tipB0 + dRB * t)
            fr = (f_tipA0 + dfA * t) if is_a else (f_tipB0 + dfB * t)
        else:
            R = R_val0 + dRV * t
            fr = f_val0 + dfV * t
        verts.append((R * math.cos(ang), R * math.sin(ang), fr))
    out = []
    for i in range(n_v):
        x0_, y0_, fr = verts[i]
        v = np.array([x0_, y0_])
        e1 = np.array(verts[(i - 1) % n_v][:2]) - v
        e2 = np.array(verts[(i + 1) % n_v][:2]) - v
        e1 = e1 / np.linalg.norm(e1)
        e2 = e2 / np.linalg.norm(e2)
        bis = e1 + e2
        bis = bis / np.linalg.norm(bis)
        cos_h = float(np.clip(e1 @ bis, -1.0, 1.0))
        sin_h = math.sqrt(max(1e-12, 1.0 - cos_h ** 2))
        s = fr / sin_h
        cen = v + s * bis
        t_off = s * cos_h
        t1 = v + e1 * t_off
        t2 = v + e2 * t_off
        u1 = (t1 - cen) / np.linalg.norm(t1 - cen)
        u2 = (t2 - cen) / np.linalg.norm(t2 - cen)
        mid = cen + fr * (u1 + u2) / np.linalg.norm(u1 + u2)
        out.append({
            "center": (float(cen[0]), float(cen[1])), "radius": float(fr),
            "t1": (float(t1[0]), float(t1[1])), "t2": (float(t2[0]), float(t2[1])),
            "mid": (float(mid[0]), float(mid[1])),
        })
    return out


def _sample_ring(fillets, n_per=30, noise=0.0, rng=None):
    """Points along the closed arc/line curve, CCW."""
    pts = []
    m = len(fillets)
    for i, f in enumerate(fillets):
        c = np.asarray(f["center"])
        r = f["radius"]
        t1 = np.asarray(f["t1"])
        t2 = np.asarray(f["t2"])
        a1 = math.atan2(t1[1] - c[1], t1[0] - c[0])
        a2 = math.atan2(t2[1] - c[1], t2[0] - c[0])
        da = (a2 - a1 + math.pi) % (2.0 * math.pi) - math.pi
        for s in np.linspace(0.0, 1.0, n_per, endpoint=False):
            a = a1 + s * da
            pts.append(c + r * np.array([math.cos(a), math.sin(a)]))
        nt = np.asarray(fillets[(i + 1) % m]["t1"])
        for s in np.linspace(0.0, 1.0, n_per, endpoint=False):
            pts.append(t2 + s * (nt - t2))
    pts = np.asarray(pts)
    if noise > 0.0:
        pts = pts + rng.normal(0.0, noise, pts.shape)
    return pts


def test_detect_ring_extrema_finds_all_star_corners():
    fillets = _star_fillets(500.0)
    pts = _sample_ring(fillets, n_per=25)
    corners = fitting.detect_ring_extrema(pts, chord_tol=0.5)
    assert len(corners) == 12  # 4 * n_pairs, alternating
    assert all(corners[i][1] != corners[i + 1][1] for i in range(len(corners) - 1))


def test_detect_ring_extrema_refuses_a_circle():
    th = np.linspace(0.0, 2.0 * math.pi, 400, endpoint=False)
    pts = np.column_stack([300.0 * np.cos(th), 300.0 * np.sin(th)])
    assert fitting.detect_ring_extrema(pts, chord_tol=0.5) == []


def _sample_blend_ring(z, n_per=25, noise=0.0, rng=None, z0=0.0, z1=1000.0):
    """Ring points of the RULED BLEND between the two end wires -- point-wise linear
    interpolation of matched-parameter samples, which is exactly what a ruled
    `ThruSections` loft's intermediate cross-section is, and therefore exactly the shape
    class `fit_tapered_fillet_model`'s linear-(c, r) family represents. A ring rebuilt by
    re-filleting linearly-INTERPOLATED sharp-polygon parameters is a DIFFERENT family
    (fillet centers travel v(z) + f(z)/sin_h(z)*bis(z), nonlinear in z) and sits up to
    ~1 mm off the blend mid-span on this geometry -- the truth-vs-model comparison on M16
    measured the same distinction on the real milestone (2.1 mm there)."""
    t = (z - z0) / (z1 - z0)
    pa = _sample_ring(_star_fillets(z0), n_per=n_per)
    pb = _sample_ring(_star_fillets(z1), n_per=n_per)
    pts = (1.0 - t) * pa + t * pb
    if noise > 0.0:
        pts = pts + rng.normal(0.0, noise, pts.shape)
    return pts


def test_fit_recovers_linear_fillet_family():
    """Synthetic ruled-blend two-family star (the model's own shape class, built
    independently): despite strongly asymmetric corners that displace every r(theta)
    extremum onto a flank, the fitted model's CURVE must track the noise-free truth within
    a fraction of the injected noise (0.2 mm on rings, 0.05 mm on vertices), and the
    sharp-corner (tip) radii must come back right.

    The SURFACE is the design contract, deliberately not every radius: a shallow valley's
    radius is weakly identifiable by nature -- its miter offset from the sharp corner is
    f*(1/sin_h - 1) ~ 0.016*f at these ~160 deg interior angles, so +-10 mm of valley
    radius moves the curve only ~0.15 mm (measured on this fixture: valley radii fitted
    tens of mm off while the curve stayed within 0.11 mm of truth everywhere). The engine's
    acceptance gates, like the scorer's, measure the surface."""
    rng = np.random.default_rng(7)
    zs = np.linspace(50.0, 950.0, 19)
    rings = [(float(z), _sample_blend_ring(z, n_per=25, noise=0.2, rng=rng)) for z in zs]
    verts = []
    for z in np.linspace(30.0, 970.0, 40):
        p = _sample_blend_ring(z, n_per=12, noise=0.05, rng=rng)
        verts.append(np.column_stack([p, np.full(len(p), z)]))
    verts = np.vstack(verts)

    model = fitting.fit_tapered_fillet_model(rings, verts, chord_tol=0.5)
    assert model is not None
    assert model.stats["n_corners"] == 12
    assert model.stats["p99"] <= 0.25          # ~the injected vertex noise
    for z in (100.0, 500.0, 900.0):
        fl = model.fillets_at(z)
        assert fl is not None and len(fl) == 12
        # the emitted curve vs the noise-free truth (measured 0.085-0.108 across the span)
        truth_pts = _sample_blend_ring(z, n_per=60)
        dev = model.residuals_at(truth_pts, np.full(len(truth_pts), z))
        assert float(dev.max()) <= 0.25, (z, float(dev.max()))
        # sharp corners ARE identifiable: family-A tip fillets (truth 40 -> 52 linearly)
        rs = np.array([f["radius"] for f in fl])
        tips_out = np.array([math.hypot(*f["mid"]) for f in fl])
        a_mean = rs[np.argsort(-tips_out)[:3]].mean()
        t = z / 1000.0
        assert abs(a_mean - (40.0 + 12.0 * t)) < 1.5, (z, a_mean)


def test_fit_refuses_structureless_ring():
    """An oval has no alternating fillet-corner structure -- the segmentation vote must
    refuse it rather than force a star model onto it."""
    th = np.linspace(0.0, 2.0 * math.pi, 300, endpoint=False)
    rings = []
    for z in np.linspace(0.0, 100.0, 8):
        r = 300.0 + 20.0 * np.cos(2.0 * th)
        rings.append((float(z), np.column_stack([r * np.cos(th), r * np.sin(th)])))
    verts = np.column_stack([rings[0][1], np.zeros(len(th))])
    model = fitting.fit_tapered_fillet_model(rings, verts, chord_tol=0.5)
    # an oval IS 2 maxima + 2 minima of r(theta), but below the 6-corner floor
    assert model is None


def test_build_tapered_fillet_loft_solid_valid():
    f0 = _star_fillets(0.0)
    f1 = _star_fillets(1000.0)
    shape = solids.build_tapered_fillet_loft_solid(0.0, f0, 1000.0, f1)
    assert BRepCheck_Analyzer(shape).IsValid()
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    v = props.Mass()
    # crude area x height plausibility: mean star area ~ pi * 430^2-ish
    assert 3.0e8 < v < 1.2e9
