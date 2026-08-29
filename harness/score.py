"""Scorer CLI: run rebuild.py on a milestone's truth STL and grade the result.

    .venv/bin/python harness/score.py --milestone M1 --out out/score.json [--keep]

Exit 0 = pass, 1 = fail, 2 = harness/internal error. See MISSION.md §7 for the full contract.
Checks run cheap -> expensive, fail-fast: the first failing check stops evaluation and every
later check is recorded with pass=null, skipped="prior failure".
"""
import argparse
import contextlib
import hashlib
import json
import math
import os
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
    # Report-derived checks are a JSON parse plus arithmetic — they belong ahead of the
    # 100k-sample deviation metric in the cheap→expensive ladder.
    ("dome_stations_min", "dome_stations_min"),
    ("topo_event_z", "topo_event_z_tolerance_mm"),
    ("adaptive_efficiency", "adaptive_efficiency"),
    ("surface_deviation_max_mm", "surface_deviation_max_mm"),
    ("surface_deviation_p99_mm", "surface_deviation_p99_mm"),
    ("surface_deviation_p99_by_region", "surface_deviation_p99_mm"),
    ("face_count_max", "face_count_max"),
    ("step_roundtrip", "step_roundtrip_vol_err"),
    ("gmsh_tet", "gmsh_min_sicn"),
]

# Direction matters for the partial credit term. For a lower-is-better check, being closer to
# the threshold means threshold/value → 1; for a higher-is-better one (gmsh SICN) that formula
# is inverted and saturates at 1, so a *failing* gmsh check scored the same as a passing one.
_LOWER_IS_BETTER = {"volume_err_pct", "bbox_err_pct", "surface_deviation_max_mm",
                    "surface_deviation_p99_mm", "surface_deviation_p99_by_region",
                    "face_count_max", "step_roundtrip",
                    "topo_event_z", "adaptive_efficiency"}
_HIGHER_IS_BETTER = {"gmsh_tet", "dome_stations_min"}

# The deviation metric compares two *tessellations*, so each side carries its own chordal error.
# At the scoring tolerance (0.5 mm) a tessellation sits up to 0.25 mm inside the true surface —
# measured — which is 62 % of M1's 0.4 mm p99 budget before the pipeline has done anything wrong.
# Both sides are therefore re-tessellated finer for the comparison only; the pipeline's *input*
# STL stays at CHORD_TOL, as MISSION §6 requires.
#
# The factor is 2, not 5. Deviation is the scorer's dominant cost and it scales with the *face
# count* of both meshes (trimesh's proximity query does an r-tree lookup per query point), which
# grows as 1/deflection². M1 is small enough to mislead — 872 faces at 0.5 mm — but M2 has 29 k
# faces at 0.5 mm, 58 k at 0.25 mm and 154 k at 0.1 mm, and its single deviation call at 0.1 mm
# alone can exceed the driver's whole 1500 s SCORE_TIMEOUT_S. That is what actually happened:
# selftest at /5 timed out inside M2, which reads to the driver as "M0 gate not met" forever
# (see PROGRESS iter 11). The harness must fit that budget with margin or it can never be frozen.
#
# /2 keeps most of what /5 bought: a tessellation sits up to deflection/2 inside the true surface,
# so the noise floor is ~0.125 mm — 31 % of M1's 0.4 mm p99 budget, versus 62 % at CHORD_TOL and
# 12 % at /5. The pipeline's *input* STL still ships at CHORD_TOL, as MISSION §6 requires.
DEVIATION_DEFLECTION = ms.CHORD_TOL / 2.0

# --- pipeline report contract (MISSION §5.3's `--report`) ---------------------------------
# The scorer always passes `--report <path>`. Three gates (dome_stations_min, topo_event_z,
# adaptive_efficiency) grade *how* the pipeline worked and can only be read from its own account
# of what it did, so these keys are part of the frozen contract:
#   n_stations           int          total slice stations used
#   stations_z_mm        list[float]  each station's z, in the input STL's coordinates (mm)
#   paths_used           dict[str,str] chain name -> "revolve" | "prism" | "loft" | ...
#   topology_events_z_mm list[float]  z of each detected chain birth/death event
# A missing file or key fails only the checks that need it, with a hint naming the key — it never
# fails `pipeline_exit`, so a milestone with no report-derived gate is unaffected.
REPORT_KEYS = ("n_stations", "stations_z_mm", "paths_used", "topology_events_z_mm")


def _load_report(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _report_hint(key: str) -> str:
    return (f"pipeline report is missing '{key}'. rebuild.py must honour --report and write "
            f"JSON with keys {list(REPORT_KEYS)} (see harness/score.py REPORT_KEYS).")


_UNIFORM_BASELINE_CACHE: dict = {}


def _uniform_baseline(milestone: str, truth, tol: float) -> int:
    """Cached uniform-station baseline for the adaptive-efficiency gate. Deterministic (a pure
    function of the truth geometry and the tolerance), so caching it cannot change any verdict."""
    key = (milestone, round(tol, 9))
    if key not in _UNIFORM_BASELINE_CACHE:
        mesh = metrics.load_mesh(truth.stl_path)
        _UNIFORM_BASELINE_CACHE[key] = metrics.uniform_stations_needed(
            mesh, truth.bbox[2], truth.bbox[5], tol)
    return _UNIFORM_BASELINE_CACHE[key]


@contextlib.contextmanager
def _truth_hidden():
    """Move harness/truth/ aside for the duration of the pipeline subprocess.

    cwd isolation alone cannot stop rebuild.py from reading the answer: it lives in the same repo
    and can open harness/truth/Mk.step by absolute path, then re-export it (which defeats the
    byte-hash `not_truth_copy` check). Hiding the directory makes that impossible rather than
    merely detectable. Truth files are regenerated deterministically when missing, so an
    interrupted run costs a regeneration, not correctness.
    """
    src = generators.TRUTH_DIR
    if not src.is_dir():
        yield
        return
    hidden = src.parent / f".truth_hidden_{os.getpid()}"
    src.rename(hidden)
    try:
        yield
    finally:
        # If the pipeline recreated harness/truth/ while it was hidden (e.g. by importing
        # generators itself), discard what it wrote — the scorer's own truth is authoritative.
        if src.exists():
            shutil.rmtree(src, ignore_errors=True)
        hidden.rename(src)


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


#: A region bin needs at least this many pooled sample points before its p99 is gated. The truth
#: mesh is sampled independently of the result, so every band always receives truth-side points —
#: a pipeline cannot starve a region below this floor to dodge the check.
_MIN_BIN_POINTS = 200


def _region_at(spec, z: float, z_min: float, z_max: float):
    """Label of the region band containing `z`, for localizing a failure hint (MISSION §7's
    `first_failure.location.region`). None if z falls outside every band."""
    span = z_max - z_min
    for rb in spec.regions:
        if z_min + rb.z_frac_lo * span <= z <= z_min + rb.z_frac_hi * span:
            return rb.label
    return None


def _mesh_from_step(step_path: Path, out_stl: Path, deflection: float = DEVIATION_DEFLECTION):
    """Re-read a STEP file and tessellate it at `deflection`, returning a trimesh.

    Both sides of the deviation comparison go through this same function so neither is favoured by
    the tessellator; `deflection` is far finer than CHORD_TOL so the measurement reflects the
    geometry rather than the faceting (see DEVIATION_DEFLECTION).
    """
    shape, _ = metrics.read_step(step_path)
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.3, True).Perform()
    StlAPI_Writer().Write(shape, str(out_stl))
    return metrics.load_mesh(out_stl)


class FailFast(Exception):
    """Raised internally to stop check evaluation once one has failed."""


def _run_pipeline(stl_path: Path, out_step: Path, spec, cwd: Path) -> dict:
    """Run rebuild.py as a subprocess in `cwd` on a neutrally-named copy of the STL. Never
    raises: subprocess failures/timeouts are reported in the returned dict."""
    input_stl = cwd / "input.stl"
    shutil.copyfile(stl_path, input_stl)

    cmd = [sys.executable, str(REPO_ROOT / "rebuild.py"), str(input_stl)] + spec.rebuild_args \
        + ["-o", str(out_step), "--report", str(cwd / "report.json")]
    log_path = cwd / "pipeline.log"
    t0 = time.monotonic()
    try:
        with _truth_hidden():
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

        # --- checks derived from the pipeline's own report --------------------------------
        report = _load_report(work_dir / "report.json")
        if report is not None:
            for key in ("n_stations", "paths_used"):
                if key in report:
                    metrics_out[key] = report[key]

        stations = report.get("stations_z_mm") if report else None
        stations = [float(z) for z in stations] if isinstance(stations, list) else None

        # --- check: dome_stations_min (MISSION §6 M2/M5: >= 8 stations in EACH dome) ------
        if "dome_stations_min" in spec.gates:
            threshold = spec.gates["dome_stations_min"]
            if stations is None:
                add("dome_stations_min", False, value=None,
                    threshold=threshold, reason=_report_hint("stations_z_mm"))
                fail_here("dome_stations_min", threshold=threshold,
                          hint=_report_hint("stations_z_mm"))
            z_min, z_max = truth.bbox[2], truth.bbox[5]
            span = z_max - z_min
            per_dome = {}
            for rb in spec.regions:
                if "dome" not in rb.label:
                    continue
                z0, z1 = z_min + rb.z_frac_lo * span, z_min + rb.z_frac_hi * span
                per_dome[rb.label] = sum(1 for z in stations if z0 <= z <= z1)
            metrics_out["dome_stations"] = per_dome
            worst_label = min(per_dome, key=per_dome.get) if per_dome else None
            worst = per_dome[worst_label] if per_dome else 0
            ok = bool(per_dome) and worst >= threshold
            add("dome_stations_min", ok, value=worst, threshold=threshold, per_dome=per_dome)
            if not ok:
                fail_here("dome_stations_min", value=worst, threshold=threshold,
                          location={"region": worst_label},
                          hint=f"only {worst} station(s) in {worst_label} (gate >= {threshold}); "
                               "cosine-cluster stations toward the dome apex")

        # --- check: topo_event_z (MISSION §6 M4/M5: an event detected at fin_z_start) ------
        if "topo_event_z_tolerance_mm" in spec.gates:
            threshold = spec.gates["topo_event_z_tolerance_mm"]
            expected_z = spec.params["fin_z_start"]
            events = report.get("topology_events_z_mm") if report else None
            events = [float(z) for z in events] if isinstance(events, list) else None
            if events is None:
                add("topo_event_z", False, value=None, threshold=threshold,
                    reason=_report_hint("topology_events_z_mm"))
                fail_here("topo_event_z", threshold=threshold,
                          hint=_report_hint("topology_events_z_mm"))
            best = min((abs(z - expected_z) for z in events), default=None)
            metrics_out["topology_events_z_mm"] = events
            ok = best is not None and best <= threshold
            add("topo_event_z", ok, value=best, threshold=threshold, expect_z_mm=expected_z)
            if not ok:
                fail_here("topo_event_z", value=best, threshold=threshold,
                          location={"z_mm": expected_z, "region": "fin_zone"},
                          hint=f"no topology event detected within {threshold} mm of "
                               f"z={expected_z} (reported: {events}); the fin slots are born at "
                               "that plane — bisect on loop-count change to localize it")

        # --- check: adaptive_efficiency (MISSION §6 M5) -----------------------------------
        if "adaptive_efficiency" in spec.gates:
            threshold = spec.gates["adaptive_efficiency"]
            if stations is None and (report is None or "n_stations" not in report):
                add("adaptive_efficiency", False, value=None, threshold=threshold,
                    reason=_report_hint("n_stations"))
                fail_here("adaptive_efficiency", threshold=threshold,
                          hint=_report_hint("n_stations"))
            n_used = int(report.get("n_stations", len(stations or [])))
            dev_tol = spec.gates.get("surface_deviation_p99_mm",
                                     spec.gates.get("surface_deviation_max_mm", ms.CHORD_TOL))
            # The truth STL's vertices lie exactly ON the analytic surface (a tessellation is
            # inscribed), so a max-radius-per-z-bin profile taken from vertices alone is exact —
            # no fine re-tessellation is needed for the baseline.
            n_uniform = _uniform_baseline(milestone, truth, dev_tol)
            metrics_out["uniform_stations_needed"] = n_uniform
            ratio = n_used / n_uniform if n_uniform else float("inf")
            ok = ratio <= threshold
            add("adaptive_efficiency", ok, value=ratio, threshold=threshold,
                n_stations=n_used, n_uniform=n_uniform)
            if not ok:
                fail_here("adaptive_efficiency", value=ratio, threshold=threshold,
                          hint=f"used {n_used} stations; a uniform pass needs {n_uniform} to hit "
                               f"the same {dev_tol} mm gate, so the budget is "
                               f"{int(threshold * n_uniform)} — concentrate stations where "
                               "|dA/dz| is large instead of spreading them uniformly")

        # --- checks: surface deviation ----------------------------------------------------
        needs_deviation = any(k in spec.gates for k in
                               ("surface_deviation_max_mm", "surface_deviation_p99_mm"))
        if needs_deviation:
            truth_mesh = _mesh_from_step(truth.step_path, work_dir / "truth_fine.stl")
            result_mesh = _mesh_from_step(out_step, work_dir / "result_fine.stl")
            z_min, z_max = truth.bbox[2], truth.bbox[5]
            dev = metrics.surface_deviation(
                truth_mesh, result_mesh, regions=spec.regions, z_min=z_min, z_max=z_max,
            )
            metrics_out["by_z_bin"] = dev.get("by_z_bin")

            if "surface_deviation_max_mm" in spec.gates:
                threshold = spec.gates["surface_deviation_max_mm"]
                ok = dev["max_mm"] < threshold
                argmax_region = _region_at(spec, dev["argmax_z_mm"], z_min, z_max)
                add("surface_deviation_max_mm", ok, value=dev["max_mm"], threshold=threshold)
                if not ok:
                    fail_here("surface_deviation_max_mm", value=dev["max_mm"], threshold=threshold,
                              location={"z_mm": dev["argmax_z_mm"], "xyz_mm": dev["argmax_xyz_mm"],
                                        "region": argmax_region},
                              hint=f"max deviation {dev['max_mm']:.3f} mm at z={dev['argmax_z_mm']:.1f} "
                                   f"({argmax_region}) (gate < {threshold} mm)")

            if "surface_deviation_p99_mm" in spec.gates:
                threshold = spec.gates["surface_deviation_p99_mm"]
                ok = dev["p99_mm"] < threshold
                add("surface_deviation_p99_mm", ok, value=dev["p99_mm"], threshold=threshold)
                if not ok:
                    fail_here("surface_deviation_p99_mm", value=dev["p99_mm"], threshold=threshold,
                              hint=f"p99 deviation {dev['p99_mm']:.3f} mm (gate < {threshold} mm)")

            # --- check: surface_deviation_p99_by_region ------------------------------------
            # MISSION §6 (M2, inherited by M5): "the deviation gate must hold PER Z-BIN including
            # dome bins". The global p99 is dominated by the cylinder, which carries ~90 % of the
            # surface area, so a dome that is wrong across 5 % of all sampled points still sits
            # below the global 99th percentile and passes. Re-applying the same p99 threshold
            # inside each labelled region is what the milestone actually asks for; the global max
            # already implies the per-region max, so only p99 needs its own check.
            if "surface_deviation_p99_mm" in spec.gates:
                threshold = spec.gates["surface_deviation_p99_mm"]
                bins = [b for b in (dev.get("by_z_bin") or [])
                        if b["p99_mm"] is not None and b["n_points"] >= _MIN_BIN_POINTS]
                worst = max(bins, key=lambda b: b["p99_mm"]) if bins else None
                ok = worst is not None and worst["p99_mm"] < threshold
                add("surface_deviation_p99_by_region", ok,
                    value=worst["p99_mm"] if worst else None, threshold=threshold,
                    region=worst["region"] if worst else None)
                if not ok:
                    if worst is None:
                        hint = (f"no region band collected >= {_MIN_BIN_POINTS} sample points, so "
                                "the per-region deviation gate could not be evaluated — check "
                                "harness/milestones.py region bands against the truth's z extent")
                    else:
                        hint = (f"p99 deviation {worst['p99_mm']:.3f} mm inside region "
                                f"'{worst['region']}' (z {worst['z0']:.0f}-{worst['z1']:.0f}, gate "
                                f"< {threshold} mm). The global p99 is {dev['p99_mm']:.3f} mm, so "
                                "the error is concentrated in this band, not spread over the part "
                                "— add stations there.")
                    fail_here("surface_deviation_p99_by_region",
                              value=worst["p99_mm"] if worst else None, threshold=threshold,
                              location={"region": worst["region"], "z_mm": worst["z0"]} if worst
                                       else None,
                              hint=hint)

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
