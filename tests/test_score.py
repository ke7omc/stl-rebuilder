"""Unit tests for harness/score.py against the M1 milestone."""
import shutil
from pathlib import Path

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


def test_full_chain_passes_when_pipeline_emits_truth_step(monkeypatch):
    def fake_run_pipeline(stl_path, out_step, spec, cwd):
        shutil.copyfile(TRUTH_STEP, out_step)
        return {"returncode": 0, "stderr_tail": "", "timed_out": False, "runtime_s": 0.01}

    monkeypatch.setattr(score_mod, "_run_pipeline", fake_run_pipeline)
    result = score_mod.score("M1", keep_dir=None)
    assert result["pass"] is True
    assert result["progress"] == 1.0
    assert result["first_failure"] is None
    names = [c["name"] for c in result["checks"]]
    assert names == [
        "input_watertight", "pipeline_exit", "output_step_exists", "step_readable",
        "n_solids", "brep_valid", "volume_err_pct", "surface_deviation_max_mm",
        "surface_deviation_p99_mm", "face_count_max", "step_roundtrip", "gmsh_tet",
    ]
    assert all(c["pass"] for c in result["checks"])


def test_json_serializable_and_contract_keys():
    import json

    result = score_mod.score("M1", keep_dir=None)
    text = json.dumps(result)  # must not raise
    reparsed = json.loads(text)
    for key in ("milestone", "pass", "progress", "stage_reached", "first_failure",
                "checks", "metrics", "artifacts"):
        assert key in reparsed
