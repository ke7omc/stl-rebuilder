"""Self-test: proves the harness is trustworthy WITHOUT a real pipeline (MISSION.md §7).

    .venv/bin/python harness/selftest.py

For each milestone with a built generator:
  1. closed-form volume check (where a formula exists),
  2. the truth STEP itself scored through the full metric stack must pass every gate,
  3. a 1.01x-scaled copy of the truth STEP must fail on volume_err_pct,
  4. a bore-filled (no-cut) copy must fail on volume_err_pct,
  5. gmsh must be able to mesh the truth STEP.
Plus one milestone-independent check:
  6. determinism — scoring the same (stub-pipeline) inputs twice yields identical results.

Exit 0 only if every check across every implemented milestone holds; prints a PASS/FAIL line
per check and a summary. Milestones whose generator is not yet implemented are skipped (noted,
not failed) so this file needs no changes as M2-M5 land.
"""
import copy
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Pnt, gp_Trsf

from harness import generators, metrics, meshcheck as mc
from harness import milestones as ms
from harness import score as score_mod

FAILURES = []


def _report(ok: bool, label: str, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def _score_with_step(milestone: str, step_src: Path):
    """Score `milestone` with `_run_pipeline` monkeypatched to emit `step_src` as the pipeline
    output, so the metric stack runs on a known STEP without needing a real rebuild.py.

    The stand-in *re-exports* the shape rather than copying the file: a byte-for-byte copy of the
    truth would (correctly) trip the scorer's `not_truth_copy` anti-gaming check. Re-exporting
    also proves that check has no false positives on a geometrically perfect result.
    """
    orig = score_mod._run_pipeline

    def fake_run_pipeline(stl_path, out_step, spec, cwd):
        shape, _ = metrics.read_step(step_src)
        generators._write_step(shape, out_step)
        (cwd / "pipeline.log").write_text("selftest stand-in pipeline\n")
        return {"returncode": 0, "stderr_tail": "", "timed_out": False, "runtime_s": 0.0}

    score_mod._run_pipeline = fake_run_pipeline
    try:
        return score_mod.score(milestone, keep_dir=None)
    finally:
        score_mod._run_pipeline = orig


def _make_bore_filled_m1(spec, work_dir: Path) -> Path:
    """M1 without the inner cut: a solid cylinder at R_o, same length — should badly fail
    volume_err_pct against the annular truth."""
    L, R_o = spec.params["L"], spec.params["R_o"]
    shape = BRepPrimAPI_MakeCylinder(R_o, L).Shape()
    path = work_dir / "M1_bore_filled.step"
    generators._write_step(shape, path)
    return path


def _make_scaled_copy(truth, work_dir: Path, factor: float = 1.01) -> Path:
    trsf = gp_Trsf()
    trsf.SetScale(gp_Pnt(0.0, 0.0, 0.0), factor)
    scaled = BRepBuilderAPI_Transform(truth.shape, trsf, True).Shape()
    path = work_dir / f"{truth.milestone}_scaled.step"
    generators._write_step(scaled, path)
    return path


def _make_bore_filled_m2(spec, work_dir: Path) -> Path:
    """M2 without the bore: the solid capsule (domes + cylinder), no hole — should badly fail
    volume_err_pct against the bored truth.

    Built as Fuse(capsule, bore) rather than the bare capsule: the raw revolve's meridian wire
    includes an edge lying exactly on the rotation axis (apex-to-apex), which produces a
    degenerate result that BRepCheck_Analyzer accepts in memory but that a STEP write/read
    round-trip corrupts into an invalid shape (fails `brep_valid` before the scorer ever reaches
    `volume_err_pct`, hiding the check this filler exists to exercise). Fusing the bore back in
    routes the shape through OCC's boolean solver, which produces a boundary that survives the
    round-trip cleanly, same as the real (cut) truth shape does.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    L, R_o = spec.params["L"], spec.params["R_o"]
    R_i = spec.params["R_i"]
    dome_h = spec.params["dome_semi_axial"]
    outer = generators._capsule_outer_shape(L, R_o, dome_h)
    bore = generators._straight_bore(R_i, L)
    fuse = BRepAlgoAPI_Fuse(outer, bore)
    fuse.Build()
    if not fuse.IsDone():
        raise RuntimeError("M2 bore-filler fuse failed")
    path = work_dir / "M2_bore_filled.step"
    generators._write_step(fuse.Shape(), path)
    return path


_BORE_FILLERS = {"M1": _make_bore_filled_m1, "M2": _make_bore_filled_m2}


def check_milestone(name: str, work_dir: Path) -> None:
    spec = ms.get(name)
    try:
        truth = generators.make(name)
    except NotImplementedError as e:
        # NOT a skip. The driver freezes harness/ the moment this selftest passes, and a frozen
        # harness with a missing generator makes that milestone permanently unscoreable — the
        # loop would stall at it forever with no legal way to fix the harness. An incomplete
        # harness must never certify itself as trustworthy.
        _report(False, f"{name}: generator implemented",
                f"{e} — harness cannot be frozen until every milestone can be generated")
        return

    # 1. closed-form volume
    if spec.closed_form_volume is not None:
        rel_err = abs(truth.V_truth - spec.closed_form_volume) / abs(spec.closed_form_volume)
        _report(rel_err < 1e-6, f"{name}: closed-form volume", f"rel_err={rel_err:.2e}")

    # 2. truth STEP passes every gate
    result = _score_with_step(name, truth.step_path)
    _report(result["pass"], f"{name}: truth STEP passes all gates",
            "" if result["pass"] else f"first_failure={result['first_failure']}")

    # 3. scaled copy fails on volume_err_pct
    scaled_path = _make_scaled_copy(truth, work_dir)
    result = _score_with_step(name, scaled_path)
    ok = (not result["pass"]) and result["first_failure"] is not None \
        and result["first_failure"]["check"] == "volume_err_pct"
    _report(ok, f"{name}: 1.01x-scaled copy fails on volume_err_pct",
            f"first_failure={result['first_failure']}")

    # 4. bore-filled copy fails on volume_err_pct
    if name in _BORE_FILLERS:
        filled_path = _BORE_FILLERS[name](spec, work_dir)
        result = _score_with_step(name, filled_path)
        ok = (not result["pass"]) and result["first_failure"] is not None \
            and result["first_failure"]["check"] == "volume_err_pct"
        _report(ok, f"{name}: bore-filled copy fails on volume_err_pct",
                f"first_failure={result['first_failure']}")
    else:
        _report(False, f"{name}: bore-filled copy fails on volume_err_pct",
                "no filler builder registered in _BORE_FILLERS — MISSION §7 requires a "
                "bore-filled perturbation per milestone")

    # 5. gmsh can mesh the truth STEP
    hmax = spec.params.get("R_o", 1000.0) / 10.0
    mesh_res = mc.check_meshability(str(truth.step_path), hmax, timeout_s=spec.runtime_cap_s)
    _report(bool(mesh_res.get("ok")), f"{name}: gmsh can mesh truth STEP",
            f"n_tet={mesh_res.get('n_tet')} min_quality={mesh_res.get('min_quality')}")


def check_determinism() -> None:
    """Score identical inputs twice; results must be identical modulo timing.

    This runs the *full* metric stack (via the truth STEP), not the stub pipeline that fails at
    `pipeline_exit` after two checks — the real nondeterminism risks are the seeded surface
    sampling in `surface_deviation` and gmsh's tet count, and neither is exercised otherwise.
    """
    def strip_timing(d):
        d = copy.deepcopy(d)
        d.get("metrics", {}).pop("runtime_s", None)
        for c in d.get("checks", []):
            c.pop("runtime_s", None)
        return d

    try:
        truth = generators.make("M1")
    except NotImplementedError:
        _report(False, "determinism: M1 generator required")
        return

    r1 = strip_timing(_score_with_step("M1", truth.step_path))
    r2 = strip_timing(_score_with_step("M1", truth.step_path))
    detail = ""
    if r1 != r2:
        diffs = [k for k in r1 if r1[k] != r2.get(k)]
        detail = f"differing keys: {diffs}"
    _report(r1 == r2, "determinism: scoring identical inputs twice yields identical results",
            detail)


def main() -> int:
    work_dir = Path(tempfile.mkdtemp(prefix="selftest_"))
    try:
        for name in ms.MILESTONES:
            check_milestone(name, work_dir)
        check_determinism()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    print()
    if FAILURES:
        print(f"SELFTEST FAILED ({len(FAILURES)} check(s)):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("SELFTEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
