"""Unit tests for harness/meshcheck.py against the M1 truth STEP."""
from pathlib import Path

from harness import meshcheck

TRUTH_DIR = Path(__file__).parent.parent / "harness" / "truth"


def test_truth_step_meshes_ok():
    r = meshcheck.check_meshability(str(TRUTH_DIR / "M1.step"), hmax=100.0, timeout_s=60)
    assert r["ok"] is True
    assert r["n_tet"] > 0
    assert r["min_quality"] > 0.1
    assert r["errors"] == []


def test_missing_file_fails_gracefully():
    r = meshcheck.check_meshability(str(TRUTH_DIR / "does_not_exist.step"), hmax=100.0, timeout_s=30)
    assert r["ok"] is False
    assert "reason" in r


def test_timeout_reported_gracefully(monkeypatch):
    import subprocess

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="gmsh", timeout=k.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = meshcheck.check_meshability(str(TRUTH_DIR / "M1.step"), hmax=100.0, timeout_s=1)
    assert r["ok"] is False
    assert "timeout" in r["reason"]
