"""Engine-side tests for the report's additive `verification` block (input mesh vs produced
solid: volume, per-axis bounds, body count, approximate sampled deviation). Informational
only — never read by the frozen scorer, never changes an exit code."""
import json

import pytest

from harness import generators
from pipeline import engine


@pytest.fixture(scope="module")
def m2_stl():
    truth = generators.make("M2")
    return str(truth.stl_path)


def test_rebuild_report_carries_passing_verification(m2_stl, tmp_path):
    opts = engine.RebuildOptions(
        input_stl=m2_stl, output=str(tmp_path / "m2.step"), axis="z", units="mm",
        sections=20, chord_tol=0.5, report=str(tmp_path / "m2.report.json"))
    result = engine.rebuild(opts)
    v = result.report.get("verification")
    assert isinstance(v, dict)

    vol = v["volume"]
    assert vol["pass"] is True
    assert vol["input_mm3"] > 0 and vol["solid_mm3"] > 0
    assert vol["tol_pct"] == 0.5
    assert vol["delta_pct"] <= vol["tol_pct"]

    for ax in ("x", "y", "z"):
        b = v["bounds"][ax]
        assert b["pass"] is True
        assert b["max_dev_mm"] <= b["tol_mm"]
        assert len(b["input_mm"]) == 2 and len(b["solid_mm"]) == 2

    assert v["bodies"] == {"expected": 1, "solid_bodies": 1, "pass": True}

    # tapered_bore_dome_pinch_and_surface_area.md §5.3: an additive surface-area check,
    # independent of volume, comparing the repaired input mesh's own triangle-sum area against
    # the exact BRep area.
    area = v["surface_area"]
    assert area["pass"] is True
    assert area["input_mm2"] > 0.0 and area["solid_mm2"] > 0.0
    assert area["tol_pct"] == 1.0
    assert area["delta_pct"] <= area["tol_pct"]

    # the approximate deviation check is present whenever the solid tessellation could be
    # built, with clearly approx-named keys
    if "deviation" in v and v["deviation"].get("pass") is not None:
        assert v["deviation"]["approx_p95_mm"] >= 0.0
        assert v["deviation"]["approx_max_mm"] >= v["deviation"]["approx_p95_mm"]
        assert v["deviation"]["tol_mm"] == pytest.approx(2.0 * 0.5)

    # the same block must be on disk, not just in the returned Result
    on_disk = json.loads((tmp_path / "m2.report.json").read_text())
    assert on_disk["verification"]["volume"]["pass"] is True


def test_surface_area_amber_hint_on_noisy_but_accurate_input(tmp_path):
    """tapered_bore_dome_pinch_and_surface_area.md §5.2/§5.3: a noisy marching-cubes input
    (M9) legitimately FAILS the surface-area check (its own crumpled facets carry excess area,
    measured +1.27% in the plan) even on an accurate rebuild -- deviation passes with real
    margin, so the benign "informational" hint fires, not the missing-geometry one."""
    truth = generators.make("M9")
    opts = engine.RebuildOptions(
        input_stl=str(truth.stl_path), output=str(tmp_path / "m9.step"), axis="z", units="mm",
        sections=80, adaptive=True, chord_tol=5.0, report=str(tmp_path / "m9.report.json"))
    result = engine.rebuild(opts)
    v = result.report["verification"]
    area = v["surface_area"]
    assert area["input_mm2"] > area["solid_mm2"]  # noise inflates the INPUT's own area
    if not area["pass"]:
        assert "informational" in area["hint"]
        assert "not missing geometry" in area["hint"] or "not the solid" in area["hint"]


def test_verification_absent_on_failure(tmp_path):
    """Verification is a success-path feature: a failed run's report has no block (and the
    failure path never pays for it)."""
    import trimesh
    bad = trimesh.creation.box(extents=(10, 10, 10))
    bad.faces = bad.faces[:-1]  # break watertightness
    bad_stl = tmp_path / "bad.stl"
    bad.export(bad_stl)
    report_path = tmp_path / "out.report.json"
    opts = engine.RebuildOptions(
        input_stl=str(bad_stl), output=str(tmp_path / "out.step"), report=str(report_path))
    with pytest.raises(engine.InputError):
        engine.rebuild(opts)
    rep = json.loads(report_path.read_text())
    assert "verification" not in rep
