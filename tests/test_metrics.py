"""Unit tests for harness/metrics.py against the M1 truth artifacts."""
from pathlib import Path

import pytest

from harness import metrics
from harness import milestones as ms

TRUTH_DIR = Path(__file__).parent.parent / "harness" / "truth"


@pytest.fixture(scope="module")
def m1_mesh():
    return metrics.load_mesh(TRUTH_DIR / "M1.stl")


def test_self_deviation_near_zero(m1_mesh):
    spec = ms.get("M1")
    dev = metrics.surface_deviation(
        m1_mesh, m1_mesh, n=2000, seed=0,
        regions=spec.regions, z_min=0.0, z_max=spec.params["L"],
    )
    assert dev["max_mm"] < 1e-4
    assert dev["p99_mm"] < 1e-4
    assert len(dev["by_z_bin"]) == 1
    assert dev["by_z_bin"][0]["region"] == "cylinder"


def test_self_volume_com_inertia(m1_mesh):
    vci = metrics.volume_com_inertia(m1_mesh, m1_mesh)
    assert vci["volume_err_pct"] == 0.0
    assert vci["com_err_frac"] == 0.0
    assert vci["inertia_err_frac"] == 0.0


def test_scaled_mesh_detected(m1_mesh):
    scaled = m1_mesh.copy()
    scaled.apply_scale(1.01)
    dev = metrics.surface_deviation(m1_mesh, scaled, n=2000, seed=0)
    assert dev["max_mm"] > 50.0  # ~1% of R_o=1000mm
    vci = metrics.volume_com_inertia(m1_mesh, scaled)
    assert vci["volume_err_pct"] > 1.0


def test_volume_com_inertia_rejects_non_volume(m1_mesh):
    import trimesh
    broken = m1_mesh.copy()
    broken.faces = broken.faces[:-1]  # drop a face -> not watertight
    assert not broken.is_volume
    with pytest.raises(ValueError):
        metrics.volume_com_inertia(m1_mesh, broken)


def test_step_roundtrip_matches_closed_form():
    spec = ms.get("M1")
    shape, volume = metrics.read_step(TRUTH_DIR / "M1.step")
    rel_err = abs(volume - spec.closed_form_volume) / spec.closed_form_volume
    assert rel_err < 1e-9

    rt = metrics.step_roundtrip_check(TRUTH_DIR / "M1.step", volume)
    assert rt["ok"] is True
    assert rt["rel_vol_err"] < 1e-9


def test_step_roundtrip_missing_file_reports_failure():
    rt = metrics.step_roundtrip_check(TRUTH_DIR / "does_not_exist.step", 1.0)
    assert rt["ok"] is False
    assert rt["reason"]
