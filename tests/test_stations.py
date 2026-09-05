"""Tests for pipeline/stations.py's anchor-guarantee hardening (2026-09-05): `apply_anchor_stations`
and the topology-anchor detection wired into `adaptive_stations`. See PROGRESS.md for the M8
--adaptive non-monotonicity investigation this is insurance against."""
import numpy as np
import pytest
import trimesh

from harness import generators
from pipeline import stations


# ---- apply_anchor_stations -------------------------------------------------------------------

def test_no_anchors_is_a_complete_noop():
    zs = np.linspace(0.0, 1000.0, 40)
    out = stations.apply_anchor_stations(zs, None, chord_tol=0.5, span=1000.0)
    assert out is zs
    out2 = stations.apply_anchor_stations(zs, [], chord_tol=0.5, span=1000.0)
    assert out2 is zs


def test_too_few_stations_is_a_noop():
    zs = np.array([0.0, 500.0, 1000.0])
    out = stations.apply_anchor_stations(zs, [500.0], chord_tol=0.5, span=1000.0)
    assert out is zs


def test_preserves_length_and_strict_ordering():
    rng = np.random.default_rng(0)
    for _ in range(20):
        n = rng.integers(4, 60)
        zs = np.sort(rng.uniform(0.0, 10000.0, n))
        anchors = rng.uniform(0.0, 10000.0, rng.integers(1, 5)).tolist()
        out = stations.apply_anchor_stations(zs, anchors, chord_tol=0.5, span=10000.0)
        assert out.shape == zs.shape
        assert np.all(np.diff(out) > 0)
        assert out[0] >= zs[0] - 1e-9 and out[-1] <= zs[-1] + 1e-9


def test_stations_move_closer_never_farther():
    rng = np.random.default_rng(1)
    for _ in range(20):
        n = rng.integers(4, 60)
        zs = np.sort(rng.uniform(0.0, 10000.0, n))
        anchor = float(rng.uniform(0.0, 10000.0))
        out = stations.apply_anchor_stations(zs, [anchor], chord_tol=0.5, span=10000.0)
        before = np.min(np.abs(zs - anchor))
        after = np.min(np.abs(out - anchor))
        assert after <= before + 1e-9


def test_displacement_never_exceeds_bound_fraction_of_local_gap():
    rng = np.random.default_rng(2)
    for _ in range(20):
        n = rng.integers(6, 60)
        zs = np.sort(rng.uniform(0.0, 10000.0, n))
        anchor = float(rng.uniform(0.0, 10000.0))
        out = stations.apply_anchor_stations(zs, [anchor], chord_tol=0.5, span=10000.0)
        moved = np.flatnonzero(np.abs(out - zs) > 1e-9)
        for i in moved:
            gap = zs[i + 1] - zs[i - 1]
            assert abs(out[i] - zs[i]) <= 0.45 * gap + 1e-6


def test_anchor_actually_gets_covered_when_room_exists():
    zs = np.linspace(0.0, 10000.0, 40)
    anchor = 3217.4  # deliberately not on the grid
    out = stations.apply_anchor_stations(zs, [anchor], chord_tol=0.5, span=10000.0)
    assert np.min(np.abs(out - anchor)) < np.min(np.abs(zs - anchor))


def test_multiple_anchors_each_claim_distinct_stations():
    zs = np.linspace(0.0, 10000.0, 60)
    anchors = [1000.0, 5000.0, 9000.0]
    out = stations.apply_anchor_stations(zs, anchors, chord_tol=0.5, span=10000.0)
    assert out.shape == zs.shape
    assert np.all(np.diff(out) > 0)
    for a in anchors:
        assert np.min(np.abs(out - a)) < np.min(np.abs(zs - a)) + 1e-6


def test_never_raises_on_pathological_input():
    # degenerate/adversarial inputs must fall back to the identity, never crash a rebuild.
    assert stations.apply_anchor_stations(np.array([1.0, 1.0, 1.0, 1.0]), [1.0], 0.5, 1.0) is not None
    assert stations.apply_anchor_stations(np.linspace(0, 1, 10), [float("nan")], 0.5, 1.0) is not None


# ---- adaptive_stations anchor integration ----------------------------------------------------

@pytest.fixture(scope="module")
def m7_mesh():
    """M7's real ground-truth mesh (`harness/truth/M7.stl`, cached/regenerated on demand): a
    central bore + 6 off-axis satellite perforations that all die at a precisely known z=7000mm
    (`harness/milestones.py::_m7`'s `sat_z_end`) -- a genuine, unambiguous loop-count
    transition (7 interior loops -> 1), not a hand-rolled mesh needing a boolean engine this
    environment doesn't have (`manifold3d` isn't installed here)."""
    truth = generators.make("M7")
    return trimesh.load(str(truth.stl_path), process=True, force="mesh")


def test_no_detected_events_on_featureless_cylinder_is_a_noop():
    mesh = trimesh.creation.annulus(r_min=200.0, r_max=500.0, height=10000.0, sections=64)
    mesh.apply_translation([0, 0, 5000.0])
    zs_plain = stations.adaptive_stations(mesh, 0.0, 10000.0, 40, 5.0,
                                          vertex_zs=mesh.vertices[:, 2])
    zs_anchored = stations.adaptive_stations(mesh, 0.0, 10000.0, 40, 5.0,
                                             vertex_zs=mesh.vertices[:, 2], chord_tol=0.5)
    np.testing.assert_array_equal(zs_plain, zs_anchored)


def test_anchor_coverage_holds_across_station_counts(m7_mesh):
    """The headline test: at every requested station count, the nearest station to the KNOWN
    transition z (M7's satellites all dying at z=7000) must land within `pad`. This is the exact
    class of failure Part A exists to close -- a statistical quantile draw alone does not
    guarantee this at every n (M8 measured a 47 mm one-sided bracket at n=40 before this
    change)."""
    true_z = 7000.0
    chord_tol = 0.5
    for n in (20, 30, 40, 60, 80):
        zs = stations.adaptive_stations(m7_mesh, 0.0, 10000.0, n, 5.0,
                                        vertex_zs=m7_mesh.vertices[:, 2], chord_tol=chord_tol)
        nearest_dist = float(np.min(np.abs(zs - true_z)))
        pad = min(max(3.0 * 4.0 * chord_tol, 1e-4 * 10000.0), 0.4 * (10000.0 / n))
        assert nearest_dist <= pad * 1.5, f"n={n}: nearest station {nearest_dist:.2f} mm from event"


def test_caller_supplied_anchor_zs_is_covered(m7_mesh):
    target = 8123.0  # an arbitrary z with no real feature there
    zs_plain = stations.adaptive_stations(m7_mesh, 0.0, 10000.0, 40, 5.0,
                                          vertex_zs=m7_mesh.vertices[:, 2], chord_tol=0.5)
    zs_anchored = stations.adaptive_stations(m7_mesh, 0.0, 10000.0, 40, 5.0,
                                             vertex_zs=m7_mesh.vertices[:, 2], chord_tol=0.5,
                                             anchor_zs=[target])
    assert np.min(np.abs(zs_anchored - target)) < np.min(np.abs(zs_plain - target))


def test_uniform_stations_anchor_zs_hook():
    zs_plain = stations.uniform_stations(0.0, 10000.0, 40, 5.0)
    target = 3141.5
    zs_anchored = stations.uniform_stations(0.0, 10000.0, 40, 5.0, chord_tol=0.5,
                                            anchor_zs=[target])
    assert np.min(np.abs(zs_anchored - target)) < np.min(np.abs(zs_plain - target))
    # No anchor_zs -> byte-identical to before this parameter existed.
    zs_default = stations.uniform_stations(0.0, 10000.0, 40, 5.0)
    np.testing.assert_array_equal(zs_plain, zs_default)


def test_noise_burst_does_not_outrank_real_transition():
    """M9-shaped scenario: a burst of tiny extra loops (simulated directly against
    _detect_topology_anchors, since building a real noisy mesh is expensive) must not outrank
    a genuine, much-larger-area transition just because it changes the loop count more."""
    scan_zs = np.linspace(0.0, 10000.0, 400)
    areas = np.full(400, 500_000.0)
    # Real transition: area halves at index 200.
    areas[200:] = 250_000.0
    change_idx = np.array([200])
    # Now also inject a "noise burst" transition (huge loop-count jump, tiny area change) at
    # index 350 by hand-crafting change_idx to include it with a negligible area delta.
    areas_noise = areas.copy()
    areas_noise[349] = 250_100.0  # 100 mm^2 blip, vs the real transition's 250,000 mm^2 swing
    change_idx_both = np.array([200, 349])
    anchors = stations._detect_topology_anchors(scan_zs, areas_noise, change_idx_both,
                                                n=80, chord_tol=0.5, span=10000.0, dz=scan_zs[1])
    assert len(anchors) >= 1
    # The real transition (near z=5000) must be present; a noise-only run would rank differently.
    assert min(abs(a - 5012.5) for a in anchors) < 200.0
