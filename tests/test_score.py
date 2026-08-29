"""Unit tests for harness/score.py against the M1 milestone."""
import shutil
from pathlib import Path

from harness import generators, metrics, milestones as ms
from harness import score as score_mod

TRUTH_STEP = Path(__file__).parent.parent / "harness" / "truth" / "M1.step"


def test_stub_pipeline_fails_fast_at_pipeline_exit():
    result = score_mod.score("M1", keep_dir=None)
    assert result["milestone"] == "M1"
    assert result["pass"] is False
    assert result["first_failure"]["check"] == "pipeline_exit"
    assert 0.0 <= result["progress"] < 1.0
    assert result["checks"][0]["name"] == "input_watertight"
    assert result["checks"][0]["pass"] is True


def _reexport_pipeline(stl_path, out_step, spec, cwd):
    """Stand-in pipeline that re-exports the truth shape: geometrically perfect, but not a
    byte-copy, so it is allowed past `not_truth_copy`."""
    shape, _ = metrics.read_step(TRUTH_STEP)
    generators._write_step(shape, out_step)
    return {"returncode": 0, "stderr_tail": "", "timed_out": False, "runtime_s": 0.01}


def test_full_chain_passes_when_pipeline_emits_truth_step(monkeypatch):
    monkeypatch.setattr(score_mod, "_run_pipeline", _reexport_pipeline)
    result = score_mod.score("M1", keep_dir=None)
    assert result["pass"] is True
    assert result["progress"] == 1.0
    assert result["first_failure"] is None
    names = [c["name"] for c in result["checks"]]
    assert names == score_mod.check_plan(ms.get("M1"))
    assert names == [
        "input_watertight", "pipeline_exit", "output_step_exists", "not_truth_copy",
        "step_readable", "n_solids", "brep_valid", "volume_err_pct", "bbox_err_pct",
        "surface_deviation_max_mm", "surface_deviation_p99_mm",
        "surface_deviation_p99_by_region", "face_count_max",
        "step_roundtrip", "gmsh_tet",
    ]
    assert all(c["pass"] for c in result["checks"])


def test_byte_copy_of_truth_is_rejected(monkeypatch):
    """Anti-gaming: a pipeline that just copies harness/truth/M1.step must not pass."""
    def cheating_pipeline(stl_path, out_step, spec, cwd):
        shutil.copyfile(TRUTH_STEP, out_step)
        return {"returncode": 0, "stderr_tail": "", "timed_out": False, "runtime_s": 0.01}

    monkeypatch.setattr(score_mod, "_run_pipeline", cheating_pipeline)
    result = score_mod.score("M1", keep_dir=None)
    assert result["pass"] is False
    assert result["first_failure"]["check"] == "not_truth_copy"


def test_skipped_checks_are_recorded_and_progress_uses_full_plan():
    """MISSION §7: unreached checks appear with pass=null. The progress denominator is the full
    plan, so failing check 2 of 14 must score ~1/14, not 1/2."""
    result = score_mod.score("M1", keep_dir=None)  # stub rebuild.py fails at pipeline_exit
    plan = score_mod.check_plan(ms.get("M1"))
    assert [c["name"] for c in result["checks"]] == plan
    skipped = [c for c in result["checks"] if c["pass"] is None]
    assert all(c["skipped"] == "prior failure" for c in skipped)
    assert len(skipped) == len(plan) - 2
    assert result["progress"] == round(1 / len(plan), 4)


def test_partial_credit_is_direction_aware():
    """gmsh SICN is higher-is-better; the lower-is-better formula saturated at 1.0, so a badly
    failing gmsh check scored the same as passing every check before it."""
    assert score_mod._partial("gmsh_tet", 0.05, 0.1) == 0.5
    assert score_mod._partial("gmsh_tet", 0.09, 0.1) > score_mod._partial("gmsh_tet", 0.05, 0.1)
    assert score_mod._partial("volume_err_pct", 0.5, 0.05) == 0.1
    assert score_mod._partial("volume_err_pct", 0.05, 0.5) == 0.999  # never a full point
    assert score_mod._partial("volume_err_pct", float("nan"), 0.05) == 0.0
    assert score_mod._partial("n_solids", 3, 1) == 0.0  # equality check: no partial credit


def test_json_serializable_and_contract_keys():
    import json

    result = score_mod.score("M1", keep_dir=None)
    text = json.dumps(result)  # must not raise
    reparsed = json.loads(text)
    for key in ("milestone", "pass", "progress", "stage_reached", "first_failure",
                "checks", "metrics", "artifacts"):
        assert key in reparsed
