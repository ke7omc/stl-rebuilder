"""Scorer CLI: run rebuild.py on a milestone's truth STL and grade the result.

    .venv/bin/python harness/score.py --milestone M1 --out out/score.json [--keep]

Exit 0 = pass, 1 = fail, 2 = harness/internal error. See MISSION.md §7 for the full contract.
Checks run cheap -> expensive, fail-fast: the first failing check stops evaluation and every
later check is recorded with pass=null, skipped="prior failure".
"""
import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape

from harness import generators, metrics
from harness import meshcheck as mc
from harness import milestones as ms


def _n_shapes_of_type(shape, shape_type) -> int:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, shape_type, m)
    return m.Size()


# Checks that are *always* run, in evaluation order, followed by the gate-conditional ones.
# `progress` is (index of first failure + partial) / len(plan), so the denominator must be the
# full plan — not the number of checks that happened to run before fail-fast stopped, which
# would make failing check 2 of 2 score 0.5 and failing check 12 of 12 score 0.92.
_ALWAYS = ["input_watertight", "pipeline_exit", "output_step_exists", "not_truth_copy",
           "step_readable"]
_GATED = [  # (check name, gate key that enables it) — order = evaluation order, cheap→expensive
    ("n_solids", "n_solids"),
    ("brep_valid", "brep_valid"),
    ("volume_err_pct", "volume_err_pct"),
    ("bbox_err_pct", "bbox_err_pct"),
    ("surface_deviation_max_mm", "surface_deviation_max_mm"),
    ("surface_deviation_p99_mm", "surface_deviation_p99_mm"),
    ("face_count_max", "face_count_max"),
    ("step_roundtrip", "step_roundtrip_vol_err"),
    ("gmsh_tet", "gmsh_min_sicn"),
]

# Direction matters for the partial credit term. For a lower-is-better check, being closer to
# the threshold means threshold/value → 1; for a higher-is-better one (gmsh SICN) that formula
# is inverted and saturates at 1, so a *failing* gmsh check scored the same as a passing one.
_LOWER_IS_BETTER = {"volume_err_pct", "bbox_err_pct", "surface_deviation_max_mm",
                    "surface_deviation_p99_mm", "face_count_max", "step_roundtrip"}
_HIGHER_IS_BETTER = {"gmsh_tet"}


def check_plan(spec) -> list:
    return _ALWAYS + [name for name, gate in _GATED if gate in spec.gates]


def _partial(check_name, value, threshold) -> float:
    """Partial credit in [0, 0.999) for a failing numeric check. Capped strictly below 1 so a
    failing check can never score identically to passing it."""
    if not (isinstance(value, (int, float)) and isinstance(threshold, (int, float))):
        return 0.0
    if isinstance(value, bool) or isinstance(threshold, bool):
        return 0.0
    if not (math.isfinite(value) and math.isfinite(threshold)):
        return 0.0
    if check_name in _LOWER_IS_BETTER:
        ratio = abs(threshold) / abs(value) if value else 0.0
    elif check_name in _HIGHER_IS_BETTER:
        ratio = abs(value) / abs(threshold) if threshold else 0.0
    else:
        return 0.0   # equality/boolean checks earn no partial credit
    return max(0.0, min(0.999, ratio))


def _sanitize(obj):
    """Replace non-finite floats with None so out/score.json is always *valid* JSON (NaN/Infinity
    are not) — the driver parses this file and must never choke on a metric that went bad."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _bbox_err_pct(shape, truth_bbox) -> float:
    """Max, over the 6 bounding-box faces, of |result - truth| as a percentage of the truth's
    extent along that axis. Catches unit errors (1000x), uniform scaling and bulk translation."""
    b = generators._bbox(shape)
    worst = 0.0
    for axis in range(3):
        extent = truth_bbox[axis + 3] - truth_bbox[axis]
        if extent <= 0:
            continue
        for side in (0, 3):
            worst = max(worst, abs(b[axis + side] - truth_bbox[axis + side]) / extent * 100.0)
    return worst


def _mesh_from_step(step_path: Path, work_dir: Path, spec):
    """Tessellate a STEP-read shape at the milestone's chord_tol and load it as a trimesh, so
    surface_deviation can compare it against the truth STL on equal footing."""
    shape, _ = metrics.read_step(step_path)
    chord_tol = ms.CHORD_TOL
    BRepMesh_IncrementalMesh(shape, chord_tol, False, 0.3, True).Perform()
    stl_path = work_dir / "result.stl"
    StlAPI_Writer().Write(shape, str(stl_path))
    return metrics.load_mesh(stl_path)


class FailFast(Exception):
    """Raised internally to stop check evaluation once one has failed."""


def _run_pipeline(stl_path: Path, out_step: Path, spec, cwd: Path) -> dict:
    """Run rebuild.py as a subprocess in `cwd` on a neutrally-named copy of the STL. Never
    raises: subprocess failures/timeouts are reported in the returned dict."""
    input_stl = cwd / "input.stl"
    shutil.copyfile(stl_path, input_stl)

    cmd = [sys.executable, str(REPO_ROOT / "rebuild.py"), str(input_stl)] + spec.rebuild_args \
        + ["-o", str(out_step)]
    log_path = cwd / "pipeline.log"
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=spec.runtime_cap_s,
        )
        log_path.write_text(f"$ {' '.join(cmd)}\n\n--- stdout ---\n{proc.stdout}"
                            f"\n--- stderr ---\n{proc.stderr}")
        return {
            "returncode": proc.returncode,
            "stderr_tail": "\n".join(proc.stderr.strip().splitlines()[-20:]),
            "timed_out": False,
            "runtime_s": time.monotonic() - t0,
        }
    except subprocess.TimeoutExpired:
        log_path.write_text(f"$ {' '.join(cmd)}\n\nTIMEOUT after {spec.runtime_cap_s}s\n")
        return {
            "returncode": None,
            "stderr_tail": f"pipeline exceeded {spec.runtime_cap_s}s timeout",
            "timed_out": True,
            "runtime_s": time.monotonic() - t0,
        }


def score(milestone: str, keep_dir: Path | None) -> dict:
    spec = ms.get(milestone)
    checks = []
    metrics_out = {}
    stage_reached = "setup"
    first_failure = None
    result_shape = None
    result_step_volume = None
    out_step = None

    def add(name, passed, **extra):
        checks.append({"name": name, "pass": passed, **extra})

    def fail_here(name, value=None, threshold=None, location=None, hint=""):
        nonlocal first_failure
        entry = {"check": name, "hint": hint}
        if value is not None:
            entry["value"] = value
        if threshold is not None:
            entry["threshold"] = threshold
        if location is not None:
            entry["location"] = location
        first_failure = entry
        raise FailFast(name)

    work_dir = Path(tempfile.mkdtemp(prefix=f"score_{milestone}_"))
    try:
        # --- stage: truth ---------------------------------------------------
        stage_reached = "truth"
        truth = generators.make(milestone)

        # --- check: input_watertight ------------------------------------------
        input_mesh = metrics.load_mesh(truth.stl_path)
        watertight = bool(input_mesh.is_volume)
        add("input_watertight", watertight, value=watertight)
        if not watertight:
            fail_here("input_watertight", value=watertight, threshold=True,
                      hint="truth STL is not a valid volume; regenerate the truth mesh")

        # --- stage: pipeline ------------------------------------------------
        stage_reached = "pipeline"
        out_step = work_dir / f"{milestone}.step"
        run_info = _run_pipeline(truth.stl_path, out_step, spec, work_dir)
        metrics_out["runtime_s"] = run_info["runtime_s"]

        pipeline_ok = (not run_info["timed_out"]) and run_info["returncode"] == 0
        add("pipeline_exit", pipeline_ok, value=run_info["returncode"],
            stderr_tail=run_info["stderr_tail"])
        if not pipeline_ok:
            reason = "timed out" if run_info["timed_out"] else f"exit {run_info['returncode']}"
            fail_here("pipeline_exit", value=run_info["returncode"], threshold=0,
                      hint=f"rebuild.py {reason}: {run_info['stderr_tail'] or '(no stderr)'}")

        # --- check: output_step_exists ---------------------------------------
        step_exists = out_step.is_file()
        add("output_step_exists", step_exists, value=step_exists)
        if not step_exists:
            fail_here("output_step_exists", value=step_exists, threshold=True,
                      hint=f"rebuild.py exited 0 but did not write {out_step.name}")

        # --- check: not_truth_copy ---------------------------------------------
        # rebuild.py lives in the repo, so cwd isolation cannot stop it from reading
        # harness/truth/. It can at least never pass by *copying* the answer.
        is_copy = _sha256(out_step) == _sha256(truth.step_path)
        add("not_truth_copy", not is_copy, value=is_copy)
        if is_copy:
            fail_here("not_truth_copy", value=is_copy, threshold=False,
                      hint="output STEP is byte-identical to harness/truth/: the pipeline must "
                           "rebuild the solid from the STL, not copy the ground truth")

        # --- stage: validate --------------------------------------------------
        stage_reached = "validate"
        try:
            result_shape, result_step_volume = metrics.read_step(out_step)
        except Exception as e:
            add("step_readable", False, value=str(e))
            fail_here("step_readable", hint=f"output STEP could not be read back: {e}")
        add("step_readable", True)

        # --- check: n_solids ----------------------------------------------------
        n_solids = _n_shapes_of_type(result_shape, TopAbs_SOLID)
        want_solids = spec.gates.get("n_solids", 1)
        ok = n_solids == want_solids
        add("n_solids", ok, value=n_solids, expect=want_solids)
        if not ok:
            fail_here("n_solids", value=n_solids, threshold=want_solids,
                      hint=f"expected exactly {want_solids} solid(s), found {n_solids}")

        # --- check: brep_valid ----------------------------------------------------
        valid = BRepCheck_Analyzer(result_shape).IsValid()
        add("brep_valid", bool(valid))
        if not valid:
            fail_here("brep_valid", value=False, threshold=True,
                      hint="BRepCheck_Analyzer reports the result shape is not a valid BRep")

        # --- check: volume_err_pct ----------------------------------------------------
        if "volume_err_pct" in spec.gates:
            v_truth = spec.closed_form_volume if spec.closed_form_volume is not None else truth.V_truth
            volume_err_pct = abs(result_step_volume - v_truth) / abs(v_truth) * 100.0
            threshold = spec.gates["volume_err_pct"]
            ok = volume_err_pct < threshold
            add("volume_err_pct", ok, value=volume_err_pct, threshold=threshold)
            if not ok:
                fail_here("volume_err_pct", value=volume_err_pct, threshold=threshold,
                          hint=f"result volume differs from truth by {volume_err_pct:.4f}% "
                               f"(gate < {threshold}%)")

        # --- check: bbox_err_pct ----------------------------------------------------
        if "bbox_err_pct" in spec.gates:
            bbox_err = _bbox_err_pct(result_shape, truth.bbox)
            metrics_out["bbox_err_pct"] = bbox_err
            threshold = spec.gates["bbox_err_pct"]
            ok = bbox_err < threshold
            add("bbox_err_pct", ok, value=bbox_err, threshold=threshold)
            if not ok:
                fail_here("bbox_err_pct", value=bbox_err, threshold=threshold,
                          hint=f"result bounding box differs from truth by {bbox_err:.3f}% "
                               f"(gate < {threshold}%); check units (mm end to end) and that the "
                               "axis transform was undone before export")

        # --- checks: surface deviation ----------------------------------------------------
        needs_deviation = any(k in spec.gates for k in
                               ("surface_deviation_max_mm", "surface_deviation_p99_mm"))
        if needs_deviation:
            truth_mesh = metrics.load_mesh(truth.stl_path)
            result_mesh = _mesh_from_step(out_step, work_dir, spec)
            z_min, z_max = truth.bbox[2], truth.bbox[5]
            dev = metrics.surface_deviation(
                truth_mesh, result_mesh, regions=spec.regions, z_min=z_min, z_max=z_max,
            )
            metrics_out["by_z_bin"] = dev.get("by_z_bin")

            if "surface_deviation_max_mm" in spec.gates:
                threshold = spec.gates["surface_deviation_max_mm"]
                ok = dev["max_mm"] < threshold
                add("surface_deviation_max_mm", ok, value=dev["max_mm"], threshold=threshold)
                if not ok:
                    fail_here("surface_deviation_max_mm", value=dev["max_mm"], threshold=threshold,
                              location={"z_mm": dev["argmax_z_mm"], "xyz_mm": dev["argmax_xyz_mm"]},
                              hint=f"max deviation {dev['max_mm']:.3f} mm at z={dev['argmax_z_mm']:.1f} "
                                   f"(gate < {threshold} mm)")

            if "surface_deviation_p99_mm" in spec.gates:
                threshold = spec.gates["surface_deviation_p99_mm"]
                ok = dev["p99_mm"] < threshold
                add("surface_deviation_p99_mm", ok, value=dev["p99_mm"], threshold=threshold)
                if not ok:
                    fail_here("surface_deviation_p99_mm", value=dev["p99_mm"], threshold=threshold,
                              hint=f"p99 deviation {dev['p99_mm']:.3f} mm (gate < {threshold} mm)")

        # --- check: face_count_max ----------------------------------------------------
        if "face_count_max" in spec.gates:
            n_faces = _n_shapes_of_type(result_shape, TopAbs_FACE)
            metrics_out["n_faces"] = n_faces
            threshold = spec.gates["face_count_max"]
            ok = n_faces <= threshold
            add("face_count_max", ok, value=n_faces, threshold=threshold)
            if not ok:
                fail_here("face_count_max", value=n_faces, threshold=threshold,
                          hint=f"result has {n_faces} faces (gate <= {threshold}); "
                               "unify coplanar/coaxial faces before export")

        # --- check: step_roundtrip ----------------------------------------------------
        if "step_roundtrip_vol_err" in spec.gates:
            # Re-*export* the shape we read and read it back. Comparing the output file against
            # a volume that was itself read from that same file is a tautology that can never
            # fail; a write→read cycle actually exercises STEP fidelity of the geometry.
            reexport = work_dir / f"{milestone}.roundtrip.step"
            generators._write_step(result_shape, reexport)
            rt = metrics.step_roundtrip_check(reexport, result_step_volume)
            threshold = spec.gates["step_roundtrip_vol_err"]
            ok = rt["ok"] and rt["rel_vol_err"] is not None and rt["rel_vol_err"] < threshold
            add("step_roundtrip", ok, value=rt.get("rel_vol_err"), threshold=threshold,
                reason=rt.get("reason"))
            if not ok:
                fail_here("step_roundtrip", value=rt.get("rel_vol_err"), threshold=threshold,
                          hint=rt.get("reason") or
                               f"STEP round-trip volume error {rt.get('rel_vol_err')} >= {threshold}")

        # --- check: gmsh_min_sicn ----------------------------------------------------
        if "gmsh_min_sicn" in spec.gates:
            hmax = spec.params.get("R_o", 1000.0) / 10.0
            mesh_res = mc.check_meshability(str(out_step), hmax, timeout_s=spec.mesh_timeout_s)
            threshold = spec.gates["gmsh_min_sicn"]
            ok = bool(mesh_res.get("ok")) and (mesh_res.get("min_quality") or 0.0) > threshold
            add("gmsh_tet", ok, value=mesh_res.get("min_quality"), threshold=threshold,
                n_tet=mesh_res.get("n_tet"), reason=mesh_res.get("reason"))
            if not ok:
                fail_here("gmsh_tet", value=mesh_res.get("min_quality"), threshold=threshold,
                          hint=mesh_res.get("reason") or
                               f"gmsh min SICN {mesh_res.get('min_quality')} <= {threshold}")

        stage_reached = "done"

    except FailFast:
        pass
    except Exception as e:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))[-4000:]
        add("exception", False, error=str(e), traceback_tail=tb)
        first_failure = {"check": "exception", "hint": str(e)}
    finally:
        artifacts = {"step": None, "report": None, "pipeline_log": None}
        if keep_dir is not None:
            keep_dir.mkdir(parents=True, exist_ok=True)
            for key, src in (("step", out_step),
                             ("report", work_dir / "report.json"),
                             ("pipeline_log", work_dir / "pipeline.log")):
                if src is not None and Path(src).is_file():
                    dest = keep_dir / f"{milestone}{Path(src).suffix}" if key == "step" \
                        else keep_dir / f"{milestone}.{Path(src).name}"
                    shutil.copyfile(src, dest)
                    artifacts[key] = str(dest)
        shutil.rmtree(work_dir, ignore_errors=True)

    # MISSION §7: every planned-but-unreached check is recorded with pass=null.
    plan = check_plan(spec)
    recorded = {c["name"] for c in checks}
    for name in plan:
        if name not in recorded:
            checks.append({"name": name, "pass": None, "skipped": "prior failure"})

    passed = first_failure is None
    if passed:
        progress = 1.0
    elif first_failure["check"] in plan:
        idx = plan.index(first_failure["check"])
        progress = (idx + _partial(first_failure["check"],
                                   first_failure.get("value"),
                                   first_failure.get("threshold"))) / len(plan)
    else:
        # An exception (or a harness-side failure) outside the plan: credit only the checks
        # that actually passed, so progress stays monotone in "closer to passing".
        progress = sum(1 for c in checks if c["pass"] is True) / len(plan)

    if not math.isfinite(progress):
        progress = 0.0

    return _sanitize({
        "milestone": milestone,
        "pass": passed,
        "progress": round(progress, 4),
        "stage_reached": stage_reached,
        "first_failure": first_failure,
        "checks": checks,
        "metrics": metrics_out,
        "artifacts": artifacts,
    })


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Score a milestone against rebuild.py.")
    parser.add_argument("--milestone", required=True, choices=list(ms.MILESTONES))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--keep", action="store_true",
                         help="keep the produced STEP artifact under out/ instead of discarding it")
    args = parser.parse_args(argv)

    try:
        result = score(args.milestone, keep_dir=args.out.parent if args.keep else None)
    except Exception as e:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "milestone": args.milestone, "pass": False, "progress": 0.0,
            "stage_reached": "harness_error",
            "first_failure": {"check": "harness_internal_error", "hint": str(e)},
            "checks": [], "metrics": {}, "artifacts": {}, "traceback": tb,
        }, indent=2))
        print(f"harness internal error: {e}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
