"""Tests for the M14 severed-station fix (MISSION §6.2;
docs/plans/m14_multilobe_dome_breakthrough.md). Pure classification/validation/end-curvature
tests use fabricated inputs (no mesh needed); the mesh-touching ones use `harness/truth/M14.stl`
via `generators.make("M14")` (cached/regenerated on demand, same precedent as
`tests/test_stations.py`'s M7 fixture / `app/smoke.py`)."""
import math

import numpy as np
import pytest
import trimesh
from shapely.geometry import Polygon

from harness import generators
from pipeline import engine, tol
from pipeline.slicing import slice_station

CT = 0.5  # chord_tol used throughout -- matches M14's own committed rebuild_args


def _hexagon(cx, cy, r, n=64):
    """A near-circular n-gon polygon centered at (cx, cy), radius r. Good enough for the
    classifier tests below -- only interior-count and (for the single-loop case) roundness
    matter to `_classify_severed_station`, not the exact shape of a real star-bore island."""
    ang = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    coords = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in ang]
    return Polygon(coords)


def _six_islands(R=800.0, half_angle_deg=20.0, n_pts=40, r_inner_frac=0.5):
    """Six disjoint, 0-interior 'islands' whose outer arc lies on a circle of radius R centered
    on the axis -- a synthetic stand-in for one severed M14 station (6-point star, MISSION
    §6.2)."""
    polys = []
    for k in range(6):
        center_deg = k * 60.0
        a0 = math.radians(center_deg - half_angle_deg)
        a1 = math.radians(center_deg + half_angle_deg)
        angs = np.linspace(a0, a1, n_pts)
        outer = [(R * math.cos(a), R * math.sin(a)) for a in angs]
        inner = [(r_inner_frac * R * math.cos(a), r_inner_frac * R * math.sin(a))
                 for a in angs[::-1]]
        polys.append(Polygon(outer + inner))
    return polys


# ---- _classify_severed_station -----------------------------------------------------------

def test_six_disjoint_zero_interior_islands_is_severed_with_env():
    polys = _six_islands(R=800.0)
    result = engine._classify_severed_station(polys, CT)
    assert result is not None
    assert result["n_islands"] == 6
    assert result["env"] is not None
    Ro, max_resid = result["env"]
    assert abs(Ro - 800.0) < 1.0
    assert max_resid <= tol.circle_max_resid(CT)


def test_single_loop_with_hole_is_not_severed():
    outer = _hexagon(0.0, 0.0, 500.0)
    hole = _hexagon(0.0, 0.0, 100.0)
    poly = Polygon(outer.exterior.coords, [list(hole.exterior.coords)])
    assert engine._classify_severed_station([poly], CT) is None


def test_mixed_topology_is_not_severed():
    """One polygon has a hole, one does not -- a genuinely mixed topology `_classify_severed_
    station` must reject (the caller falls through to `_multi_loop_hint`'s typed error)."""
    hole = _hexagon(300.0, 0.0, 20.0)
    with_hole = Polygon(_hexagon(300.0, 0.0, 100.0).exterior.coords,
                         [list(hole.exterior.coords)])
    no_hole = _hexagon(-300.0, 0.0, 100.0)
    assert engine._classify_severed_station([with_hole, no_hole], CT) is None


def test_quarter_arc_only_islands_env_is_none():
    """Only 2 of the would-be 6 islands present (e.g. a real scan's sliver filter ate the rest
    right at a tip): still classified as severed (station still counts, feeds the run-position
    validation), but the angular-coverage gate must reject the envelope fit -- an ill-conditioned
    arc through 2 adjacent islands must not feed the dome model."""
    two = _six_islands(R=800.0)[:2]
    result = engine._classify_severed_station(two, CT)
    assert result is not None
    assert result["n_islands"] == 2
    assert result["env"] is None


def test_single_simply_connected_poly_is_severed_candidate():
    """N == 1, zero interiors -- the degenerate single-island-left case (MISSION §6.2 M14 plan
    §A.1): still a severed-candidate, not immediately routed to the old 'expected at least 1
    interior hole' error. Whether it's ultimately ACCEPTED depends on the post-loop
    run-position validation, tested separately below."""
    poly = _hexagon(0.0, 0.0, 500.0)
    result = engine._classify_severed_station([poly], CT)
    assert result is not None
    assert result["n_islands"] == 1


def test_empty_slice_is_not_severed():
    """The caller's own empty-slice handling (a separate branch in the station loop) covers
    `polys == []`, not the classifier."""
    assert engine._classify_severed_station([], CT) is None


# ---- post-loop run-position validation (_severed_fore_aft_runs / _severed_run_error) -----

def test_fore_only_run_is_valid():
    all_zz = list(range(10))
    severed_idx = {0: {"n_islands": 6, "env": None}, 1: {"n_islands": 6, "env": None}}
    fore_run, aft_run = engine._severed_fore_aft_runs(len(all_zz), severed_idx)
    assert fore_run == {0, 1}
    assert aft_run == set()
    assert engine._severed_run_error(all_zz, severed_idx, fore_run, aft_run) is None


def test_fore_and_aft_runs_are_valid():
    all_zz = list(range(10))
    severed_idx = {0: {"n_islands": 6, "env": None}, 9: {"n_islands": 6, "env": None}}
    fore_run, aft_run = engine._severed_fore_aft_runs(len(all_zz), severed_idx)
    assert fore_run == {0}
    assert aft_run == {9}
    assert engine._severed_run_error(all_zz, severed_idx, fore_run, aft_run) is None


def test_mid_part_severed_run_is_rejected_with_actionable_message():
    """The exact case the plan's post-loop validation exists for: a severed station in the
    MIDDLE of the sampled sequence (not touching either end) must be a hard error, not silently
    accepted as end-of-burn."""
    all_zz = [float(i) * 100.0 for i in range(10)]
    severed_idx = {5: {"n_islands": 6, "env": None}}
    fore_run, aft_run = engine._severed_fore_aft_runs(len(all_zz), severed_idx)
    assert fore_run == set() and aft_run == set()
    msg = engine._severed_run_error(all_zz, severed_idx, fore_run, aft_run)
    assert msg is not None
    assert "middle of the part" in msg
    assert "500.000" in msg  # the actual z of the stray severed station, for a concrete hint


def test_every_station_severed_is_rejected():
    all_zz = list(range(5))
    severed_idx = {i: {"n_islands": 6, "env": None} for i in range(5)}
    fore_run, aft_run = engine._severed_fore_aft_runs(len(all_zz), severed_idx)
    msg = engine._severed_run_error(all_zz, severed_idx, fore_run, aft_run)
    assert msg is not None
    assert "no connected cross-section" in msg


def test_too_few_connected_stations_is_rejected():
    all_zz = list(range(5))
    # 4 of 5 severed (a fore run of 3 + an aft run of 1), leaving only 1 connected station.
    severed_idx = {0: {"n_islands": 6, "env": None}, 1: {"n_islands": 6, "env": None},
                   2: {"n_islands": 6, "env": None}, 4: {"n_islands": 6, "env": None}}
    fore_run, aft_run = engine._severed_fore_aft_runs(len(all_zz), severed_idx)
    assert fore_run == {0, 1, 2} and aft_run == {4}
    msg = engine._severed_run_error(all_zz, severed_idx, fore_run, aft_run)
    assert msg is not None
    assert "fewer than 2 connected" in msg


# ---- _curved_end ---------------------------------------------------------------------------

def test_curved_end_does_not_fire_on_a_flat_profile():
    """M1-shaped `outer_pts`: constant radius, no curvature at all -- `_curved_end` must return
    False so a flat (non-pinch) end keeps taking the old, exact straight-chord path."""
    flat_pts = [(float(z), 1000.0) for z in range(0, 2000, 100)]
    min_dz, resid_tol = 5.0 * CT, tol.circle_max_resid(CT)
    z0, coef, _window_z = engine._fit_r2_quadratic(flat_pts, True, min_dz, resid_tol)
    assert engine._curved_end(z0, coef, 0.0, resid_tol) is False


@pytest.fixture(scope="module")
def m14_mesh():
    truth = generators.make("M14")
    return trimesh.load(str(truth.stl_path), process=True, force="mesh")


def test_curved_end_fires_on_m14s_clipped_dome_end(m14_mesh):
    """The real, measured failure mode this check exists for (M14 plan §B.2): the fore end is a
    flat CAP (no pinch -- `bore_pts` is empty) sitting well inside real dome curvature, so
    `_curved_end` must fire and trigger the dome-chord resample over the excluded end-inset gap."""
    from pipeline import stations
    from pipeline.fitting import fit_circle_robust

    mesh = m14_mesh
    z_min, z_max = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    L = z_max - z_min
    station_eps = min(max(tol.eps_end(CT, L), 200.0 * CT), 0.02 * L)
    zs = stations.adaptive_stations(mesh, z_min, z_max, 300, station_eps,
                                     vertex_zs=mesh.vertices[:, 2], chord_tol=CT)
    outer_pts = []
    for z in zs:
        polys, zz = slice_station(mesh, z, CT)
        severed = engine._classify_severed_station(polys, CT)
        if severed is not None:
            if severed["env"] is not None:
                outer_pts.append((zz, severed["env"][0]))
            continue
        if not polys or len(polys) != 1:
            continue
        ext = np.asarray(polys[0].exterior.coords)
        _cx, _cy, Ro, _mr, _ = fit_circle_robust(ext)
        outer_pts.append((zz, Ro))

    min_dz, resid_tol = 5.0 * CT, tol.circle_max_resid(CT)
    fz0, fcoef, _window_z, _shoulder = engine._dome_model(outer_pts, mesh, True, min_dz,
                                                           resid_tol, CT)
    assert engine._curved_end(fz0, fcoef, z_min, resid_tol) is True


# ---- envelope fit against the real M14 truth ------------------------------------------------

def test_envelope_fit_matches_measured_m14_z200(m14_mesh):
    """Measured on the final M14 truth (docs/plans/m14_multilobe_dome_breakthrough.md §1.3): at
    z=200 (deep in the fore severed band) the 6-island envelope fit gives R=799.840 against the
    true case radius 800.000, resid 0.290 -- comfortably inside `circle_max_resid(0.5)=0.75`."""
    polys, _zz = slice_station(m14_mesh, 200.0, CT)
    assert len(polys) == 6
    result = engine._classify_severed_station(polys, CT)
    assert result is not None
    assert result["env"] is not None
    Ro, max_resid = result["env"]
    assert abs(Ro - 800.0) < 0.5
    assert max_resid < 0.75


def test_bisect_severed_edge_finds_the_measured_fore_plane(m14_mesh):
    """Measured fore severing plane on the final M14 truth: z=250.422 (docs/plans/
    m14_multilobe_dome_breakthrough.md §1.3/§D.1)."""
    z_bt = engine._bisect_severed_edge(m14_mesh, 200.0, 300.0, CT)
    assert abs(z_bt - 250.422) < 1.0


def test_bisect_severed_edge_finds_the_measured_aft_plane(m14_mesh):
    z_bt = engine._bisect_severed_edge(m14_mesh, 9800.0, 9700.0, CT)
    assert abs(z_bt - 9749.578) < 1.0


# ---- full engine.rebuild() on M14 (the actual crash fix, end to end) -----------------------

@pytest.fixture(scope="module")
def m14_stl():
    truth = generators.make("M14")
    return str(truth.stl_path)


def test_rebuild_m14_end_to_end(m14_stl, tmp_path):
    """The crash Brady hit (`TopologyError: expected 1 outer loop ... got 6`) must be fixed on
    the real geometry, at BOTH ends, with a sane single watertight solid -- MISSION §6.2 M14 /
    the fix plan's Phase E.1 slow test. Uses the same rebuild_args `harness/milestones.py::_m14`
    commits to."""
    input_mesh = trimesh.load(m14_stl, process=True, force="mesh")
    opts = engine.RebuildOptions(
        input_stl=m14_stl, output=str(tmp_path / "m14.step"), axis="z", units="mm",
        sections=300, adaptive=True, chord_tol=CT, report=str(tmp_path / "m14.report.json"),
    )
    result = engine.rebuild(opts)  # must not raise TopologyError (or any RebuildError)

    events = sorted(result.report["topology_events_z_mm"])
    assert len(events) == 2
    topo_tol = tol.topo_tol(CT, input_mesh.bounds[1][2] - input_mesh.bounds[0][2])
    expected = [250.42, 9749.58]
    for e, ex in zip(events, expected):
        assert abs(e - ex) <= topo_tol

    v = result.report["verification"]
    assert v["bodies"] == {"expected": 1, "solid_bodies": 1, "pass": True}
    assert v["volume"]["pass"] is True
    assert v["volume"]["delta_pct"] < 0.3
    assert v["bounds"]["z"]["pass"] is True
    # Both ends land at the truth's own clip planes (fore z_min AND aft z_max) -- the whole point
    # of this milestone is that BOTH dome tips are severed, not just one.
    assert v["bounds"]["z"]["input_mm"][0] == pytest.approx(112.4177, abs=0.01)
    assert v["bounds"]["z"]["input_mm"][1] == pytest.approx(9887.582, abs=0.01)
