"""Tests for the decoupled roundness-tolerance fix
(docs/plans/decoupled_roundness_tolerance.md, incident 2026-09-09 — Brady's first real burnback
STL). Pure `tol.py`/estimator tests build a small synthetic perturbed tube directly (no OCP, no
harness generator — fast); the M15-mesh tests use `harness/generators.make("M15")` (cached like
every other milestone fixture in this suite, see `tests/test_severed.py`'s M14 precedent) and are
slower (full station loop + a real rebuild)."""
import math

import numpy as np
import pytest
import trimesh

from harness import generators
from pipeline import engine, tol


# ---------------------------------------------------------------------------
# tol.circle_max_resid — the M1-M14 no-behavior-change invariant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ct", [0.01, 0.5, 5.0, 26.675])
def test_circle_max_resid_legacy_invariant(ct):
    assert tol.circle_max_resid(ct) == pytest.approx(1.5 * ct)
    assert tol.circle_max_resid(ct, 0.0) == pytest.approx(1.5 * ct)


def test_circle_max_resid_floor_wins_when_larger():
    assert tol.circle_max_resid(0.01, 0.8) == pytest.approx(0.8)


def test_circle_max_resid_chord_tol_side_wins_when_larger():
    assert tol.circle_max_resid(1.0, 0.1) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# _estimate_roundness_noise — synthetic perturbed-tube probe
# ---------------------------------------------------------------------------

def _perturbed_tube(R=200.0, L=800.0, ovality_mm=0.8, wobble_mm=0.0,
                    n_theta=180, n_z=161) -> trimesh.Trimesh:
    """A fine tube (no end caps needed -- every probe z used below is interior, so the sliced
    ring is always closed) whose radius is displaced by the same smooth m=2-ovality-with-phase-
    twist + center-wobble field `harness/generators.py::_make_m15` applies to the real milestone,
    at a MUCH smaller scale for test speed. Facet dihedral stays tiny (long-wavelength field, per
    the plan's own measured mechanism), so this reproduces the decoupling property directly."""
    theta = np.linspace(0.0, 2.0 * math.pi, n_theta, endpoint=False)
    zs = np.linspace(0.0, L, n_z)
    TH, Z = np.meshgrid(theta, zs)  # (n_z, n_theta)
    dr = ovality_mm * np.cos(2.0 * TH + math.pi * Z / L)
    wob_cx = wobble_mm * np.sin(1.4 * math.pi * Z / L)
    wob_cy = wobble_mm * np.cos(1.8 * math.pi * Z / L)
    Rp = R + dr
    X = Rp * np.cos(TH) + wob_cx
    Y = Rp * np.sin(TH) + wob_cy
    verts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    idx = np.arange(n_z * n_theta).reshape(n_z, n_theta)
    j = np.arange(n_theta)
    j2 = (j + 1) % n_theta
    faces = []
    for i in range(n_z - 1):
        a, b, c, d = idx[i, j], idx[i, j2], idx[i + 1, j], idx[i + 1, j2]
        faces.append(np.column_stack([a, b, c]))
        faces.append(np.column_stack([b, d, c]))
    return trimesh.Trimesh(vertices=verts, faces=np.vstack(faces), process=False)


def test_estimate_roundness_noise_measures_the_ovality_amplitude():
    """The plan's own probe construction (§1.5): a floor of ~1.2x the ovality amplitude, within
    a generous +/-25% band (this is a much coarser/smaller mesh than the plan's measured probe,
    so an exact match isn't expected -- only the right order of magnitude and mechanism)."""
    mesh = _perturbed_tube(ovality_mm=0.8, wobble_mm=0.25)
    floor, info = engine._estimate_roundness_noise(mesh, chord_tol=0.05, z_min=0.0, z_max=800.0)
    expected = 1.2 * 0.8
    assert expected * 0.75 <= floor <= expected * 1.25
    assert info["capped"] is False
    assert info["n_probes"] >= 6


def test_estimate_roundness_noise_clean_cylinder_is_inert():
    """No ovality/wobble at all: the floor should be dominated by pure facet noise, far below
    the roundness scale that would ever move `circle_max_resid` -- i.e. inert, same as every
    milestone before M15 (`1.5*chord_tol` alone decides the gate)."""
    mesh = _perturbed_tube(ovality_mm=0.0, wobble_mm=0.0)
    ct = 0.5
    floor, info = engine._estimate_roundness_noise(mesh, chord_tol=ct, z_min=0.0, z_max=800.0)
    assert floor < 1.5 * ct
    assert tol.circle_max_resid(ct, floor) == pytest.approx(1.5 * ct)


def test_estimate_roundness_noise_discards_one_garbage_probe(monkeypatch):
    """A single wildly-off circle-fit residual (a poorly-conditioned near-tip-style sample) must
    not itself set the gate -- the 3x-median discard should drop it, and the floor should still
    reflect the real, small ovality signal, not the injected outlier."""
    mesh = _perturbed_tube(ovality_mm=0.05, wobble_mm=0.0)
    real_fit = engine.fit_circle_robust
    calls = {"n": 0}

    def fake_fit(pts, *a, **kw):
        cx, cy, R, resid, rms = real_fit(pts, *a, **kw)
        calls["n"] += 1
        if calls["n"] == 3:
            resid = 500.0  # a single garbage probe, orders of magnitude over the real signal
        return cx, cy, R, resid, rms

    monkeypatch.setattr(engine, "fit_circle_robust", fake_fit)
    floor, info = engine._estimate_roundness_noise(mesh, chord_tol=0.02, z_min=0.0, z_max=800.0)
    assert floor < 5.0  # nowhere near the 500 mm outlier
    assert info["n_probes"] == 11  # exactly one probe discarded


def test_estimate_roundness_noise_too_few_survivors_returns_zero(monkeypatch):
    """If fewer than 6 of the 12 probes survive (most stations severed/degenerate), the
    estimator refuses to guess -- returns the inert 0.0 floor rather than a measurement built
    from too little data."""
    mesh = _perturbed_tube(ovality_mm=0.05, wobble_mm=0.0)
    real_slice = engine.slice_station
    calls = {"n": 0}

    def fake_slice(mesh_, z, chord_tol):
        calls["n"] += 1
        if calls["n"] <= 8:
            from shapely.geometry import Polygon
            # Two disjoint polygons: len(polys) != 1, so the estimator must skip this probe
            # entirely (same convention as _classify_severed_station -- a severed/degenerate
            # band contributes no radius sample).
            return [Polygon([(0, 0), (1, 0), (1, 1)]),
                   Polygon([(2, 2), (3, 2), (3, 3)])], z
        return real_slice(mesh_, z, chord_tol)

    monkeypatch.setattr(engine, "slice_station", fake_slice)
    floor, info = engine._estimate_roundness_noise(mesh, chord_tol=0.02, z_min=0.0, z_max=800.0)
    assert floor == 0.0
    assert info["n_probes"] < 6


def test_estimate_roundness_noise_caps_at_two_percent_of_radius():
    """A genuinely non-axisymmetric envelope (ovality cranked to 5% of R) must not silently
    launder itself into an enormous floor -- the 2%-of-radius anti-laundering cap kicks in and
    `floor_info["capped"]` says so."""
    R = 200.0
    mesh = _perturbed_tube(R=R, ovality_mm=0.05 * R, wobble_mm=0.0)
    floor, info = engine._estimate_roundness_noise(mesh, chord_tol=0.05, z_min=0.0, z_max=800.0)
    assert info["capped"] is True
    assert floor <= 0.021 * R  # the 2% cap, with a hair of slack for the measured median radius


# ---------------------------------------------------------------------------
# Hint text — must name --roundness-tol, never --chord-tol, for a roundness failure
# ---------------------------------------------------------------------------

def test_non_axisymmetric_hint_roundness_branch_names_roundness_tol_not_chord_tol():
    resid_gate = 0.975
    floor_info = {"floor_mm": 0.975, "capped": False}
    msg = engine._non_axisymmetric_hint(
        zz=100.0, cx=0.01, cy=0.01, max_resid=1.2, chord_tol=0.01,
        resid_gate=resid_gate, floor_info=floor_info)
    assert "--roundness-tol" in msg
    assert "--chord-tol" not in msg


def test_non_axisymmetric_hint_capped_branch_says_genuinely_non_axisymmetric():
    resid_gate = 20.0
    floor_info = {"floor_mm": 20.0, "capped": True, "cap_pct": 2.0}
    msg = engine._non_axisymmetric_hint(
        zz=100.0, cx=0.01, cy=0.01, max_resid=25.0, chord_tol=0.01,
        resid_gate=resid_gate, floor_info=floor_info)
    assert "genuinely non-axisymmetric" in msg
    assert "--roundness-tol" in msg  # still offers the force-override escape hatch


def test_non_axisymmetric_hint_off_axis_branch_unchanged():
    msg = engine._non_axisymmetric_hint(
        zz=100.0, cx=5.0, cy=5.0, max_resid=0.1, chord_tol=0.01,
        resid_gate=0.015, floor_info={"floor_mm": 0.0, "capped": False})
    assert "--axis" in msg
    assert "--roundness-tol" not in msg


# ---------------------------------------------------------------------------
# M15 mesh — the real milestone, slower (full station loop / a real rebuild)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def m15_truth():
    return generators.make("M15")


def test_roundness_tol_zero_on_m15_reproduces_the_legacy_trap(m15_truth, tmp_path):
    """The escape hatch (`--roundness-tol 0`) must reduce `resid_gate` to exactly
    `1.5*chord_tol` again -- i.e. M15's own mesh, run with the floor forced off, still hits the
    trap this milestone exists to make permanently testable (some TopologyError, not necessarily
    the exact same station or hint branch as the un-forced pre-fix run -- see
    harness/milestones.py::_m15's docstring for the measured pre-fix repro)."""
    from harness import milestones as ms
    spec = ms.get("M15")
    opts = engine.RebuildOptions(
        input_stl=str(m15_truth.stl_path), output=str(tmp_path / "out.step"),
        axis="z", sections=40, chord_tol=spec.chord_tol, roundness_tol=0.0,
    )
    with pytest.raises(engine.TopologyError):
        engine.rebuild(opts)


def test_engine_rebuild_m15_succeeds_with_auto_roundness_floor(m15_truth, tmp_path):
    """The acceptance test at unit-test scale: M15's pinned args (chord_tol at the mesh's own
    honest fine estimate, roundness_tol on auto/None) must build a clean, valid solid whose
    report carries a measured roundness floor and whose verification deviation check passes
    under the widened tolerance (§3.4) instead of spuriously failing on the input's own real
    (not reconstruction-error) out-of-roundness."""
    from harness import milestones as ms
    spec = ms.get("M15")
    out_step = tmp_path / "out.step"
    out_report = tmp_path / "out.report.json"
    opts = engine.RebuildOptions(
        input_stl=str(m15_truth.stl_path), output=str(out_step),
        axis="z", sections=60, chord_tol=spec.chord_tol, roundness_tol=None,
        report=str(out_report),
    )
    result = engine.rebuild(opts)
    assert out_step.exists()
    assert result.report is not None
    roundness = result.report.get("roundness")
    assert roundness is not None
    assert roundness["source"] == "auto"
    assert roundness["floor_mm"] > 10.0 * (1.5 * spec.chord_tol)  # decoupled, per the plan
    dev = (result.report.get("verification") or {}).get("deviation") or {}
    assert dev.get("pass") is True
