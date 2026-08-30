"""Self-test: proves the harness is trustworthy WITHOUT a real pipeline (MISSION.md §7).

    .venv/bin/python harness/selftest.py [--milestone M2 ...] [--skip-gmsh]

`--milestone`/`--skip-gmsh` narrow the run so a targeted change can be checked in a couple of
minutes instead of the full sweep. They are a *developer* convenience only: a narrowed run
prints `SELFTEST PASSED (PARTIAL)` and is not evidence for the M0 freeze. The driver always
invokes this file with no arguments, which runs everything.

For each milestone with a built generator:
  1. closed-form volume check (where a formula exists),
  2. the truth STEP itself scored through the full metric stack must pass every gate,
  3. a 1.01x-scaled copy of the truth STEP must fail on volume_err_pct,
  4. a bore-filled (no-cut) copy must fail on volume_err_pct,
  5. gmsh must be able to mesh the truth STEP.
Plus two milestone-independent checks:
  6. the exact point→mesh distance kernel every deviation gate rests on agrees with trimesh's
     reference query (it is hand-written; an under-reported distance would silently fake a pass),
  7. determinism — scoring the same (stub-pipeline) inputs twice yields identical results.

Exit 0 only if every check across every implemented milestone holds; prints a PASS/FAIL line
per check and a summary. Milestones whose generator is not yet implemented are skipped (noted,
not failed) so this file needs no changes as M2-M5 land.
"""
import argparse
import copy
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import trimesh

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Pnt, gp_Trsf, gp_Ax2, gp_Dir

from harness import generators, metrics, meshcheck as mc
from harness import milestones as ms
from harness import score as score_mod

FAILURES = []
_T0 = time.time()
_T_LAST = [_T0]


def _report(ok: bool, label: str, detail: str = "") -> None:
    """Print one PASS/FAIL line prefixed with this check's elapsed time and the running total.

    The timing is not decoration. The driver runs this file under a hard SCORE_TIMEOUT_S
    and keeps only the last 3000 characters of output, which OCC's STEP writer floods with banner
    noise. When selftest went over budget in iter 10 the tail therefore held no check lines at
    all, the overrun was misread as the environment killing the process, and the M0 gate stalled
    for five iterations. Flushed per-check times make a future overrun readable from that tail.
    """
    now = time.time()
    dt, total = now - _T_LAST[0], now - _T0
    _T_LAST[0] = now
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] [{dt:6.1f}s |{total:7.1f}s] {label}"
          + (f" — {detail}" if detail else ""), flush=True)
    if not ok:
        FAILURES.append(label)


def _ideal_report(spec, truth) -> dict:
    """The report an ideal pipeline would write for `spec` (see score.REPORT_KEYS): dense stations
    in every dome band, a sparse pass elsewhere, and a topology event exactly at the fin plane."""
    # Stations live in the scorer's canonical axial coordinate, not raw world z: for M10/M13,
    # whose motor axis is +x, raw z is a *radial* coordinate. Identical to the bbox for Round 1.
    _mat, z_min, z_max = score_mod._canonical(spec, truth)
    span = z_max - z_min
    stations = [z_min + span * i / 11.0 for i in range(12)]
    station_bands = spec.station_bands or {}
    for rb in spec.regions:
        if "dome" in rb.label:
            z0, z1 = z_min + rb.z_frac_lo * span, z_min + rb.z_frac_hi * span
            stations += [z0 + (z1 - z0) * i / 9.0 for i in range(10)]
        if rb.label in station_bands:
            # Densify every band the spec's `station_bands` gate actually checks a minimum
            # count for (M8/M9/M10/M12/M13's fore_wall/aft_wall etc, not just dome bands) — a
            # sparse-everywhere-else report would otherwise fail the ideal pipeline's own report
            # on the exact gate it exists to enforce.
            n = station_bands[rb.label] + 2
            z0, z1 = z_min + rb.z_frac_lo * span, z_min + rb.z_frac_hi * span
            stations += [z0 + (z1 - z0) * i / (n - 1) for i in range(n)]
    # topo_events_z_mm (M7/M8/M9/M12/M13, harness/milestones.py) is the general Round 2 list of
    # expected events; the older topo_event_z_tolerance_mm gate (M4/M5) only ever expects one, at
    # fin_z_start. Emit whichever the spec actually declares — a milestone with both would emit
    # the union, but no spec does today.
    events = list(spec.topo_events_z_mm) or (
        [spec.params["fin_z_start"]] if "fin_z_start" in spec.params else [])
    return {
        "n_stations": len(stations),
        "stations_z_mm": sorted(stations),
        "paths_used": {"outer": "revolve", "bore": "revolve"},
        "topology_events_z_mm": events,
        # Round 2 frame keys (MISSION §7.2). M10/M13 gate on these; for Round 1 they are simply
        # the canonical identity frame and no gate reads them.
        "frame": {
            "axis": list(spec.frame.axis),
            "origin_mm": list(spec.frame.origin_mm),
            "units": spec.frame.units,
            "scale_to_mm": spec.frame.scale_to_mm,
        },
        "axial_extent_mm": span,
    }


def _stations_uniform(rep: dict, n: int) -> dict:
    """Replace the report's stations with `n` uniformly spaced ones over the same axial extent,
    keeping `n_stations` consistent so the mutation isolates the gate under test."""
    zs = rep["stations_z_mm"]
    lo, hi = min(zs), max(zs)
    out = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    return {**rep, "stations_z_mm": out, "n_stations": n}


def _stations_cosine(rep: dict, n: int) -> dict:
    """Round 1's cosine end-clustering warp, at `n` stations over the same axial extent.

    This is the counter-example behind MISSION §6.2's claim that Round 1's station placement
    cannot pass M8: clustering at both ends satisfies `dome_stations_min` while leaving the
    mid-barrel feature bands nearly empty, so it must fail `station_bands` — and it does so from
    *within* the `n_stations_max` budget, which is what makes the gate a real requirement for
    feature-aware placement rather than something brute refinement can buy its way out of.
    """
    zs = rep["stations_z_mm"]
    lo, hi = min(zs), max(zs)
    out = [lo + (hi - lo) * (1.0 - np.cos(np.pi * i / (n - 1))) / 2.0 for i in range(n)]
    return {**rep, "stations_z_mm": [float(z) for z in out], "n_stations": n}


def _frame_axis_rotated(rep: dict, deg: float) -> dict:
    """Tilt the report's frame axis by `deg` degrees about an arbitrary perpendicular."""
    axis = np.asarray(rep["frame"]["axis"], dtype=float)
    axis = axis / np.linalg.norm(axis)
    seed = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    perp = np.cross(axis, seed)
    perp = perp / np.linalg.norm(perp)
    theta = np.radians(deg)
    tilted = axis * np.cos(theta) + perp * np.sin(theta)
    return {**rep, "frame": {**rep["frame"], "axis": [float(c) for c in tilted]}}


def _score_with_step(milestone: str, step_src: Path, truth, report_mutate=None):
    """Score `milestone` with `_run_pipeline` monkeypatched to emit `step_src` as the pipeline
    output, so the metric stack runs on a known STEP without needing a real rebuild.py.

    The stand-in *re-exports* the shape rather than copying the file: a byte-for-byte copy of the
    truth would (correctly) trip the scorer's `not_truth_copy` anti-gaming check. Re-exporting
    also proves that check has no false positives on a geometrically perfect result.

    It also writes the `--report` JSON that the report-derived gates (dome_stations_min,
    topo_event_z, adaptive_efficiency) read. `report_mutate` lets a caller corrupt one field to
    prove a specific gate fails on it.
    """
    orig = score_mod._run_pipeline

    def fake_run_pipeline(stl_path, out_step, spec, cwd):
        shape, _ = metrics.read_step(step_src)
        generators._write_step(shape, out_step)
        (cwd / "pipeline.log").write_text("selftest stand-in pipeline\n")
        rep = _ideal_report(spec, truth)
        if report_mutate is not None:
            rep = report_mutate(dict(rep))
        (cwd / "report.json").write_text(json.dumps(rep))
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


def _make_bore_filled_m3(spec, work_dir: Path) -> Path:
    """M3 without the star bore: a solid cylinder at R_o, same length — should badly fail
    volume_err_pct against the star-bored truth."""
    L, R_o = spec.params["L"], spec.params["R_o"]
    shape = BRepPrimAPI_MakeCylinder(R_o, L).Shape()
    path = work_dir / "M3_bore_filled.step"
    generators._write_step(shape, path)
    return path


def _make_bore_filled_m4(spec, work_dir: Path) -> Path:
    """M4 with neither bore nor fin slots: a solid cylinder at R_o — should badly fail
    volume_err_pct against the finocyl truth."""
    L, R_o = spec.params["L"], spec.params["R_o"]
    shape = BRepPrimAPI_MakeCylinder(R_o, L).Shape()
    path = work_dir / "M4_bore_filled.step"
    generators._write_step(shape, path)
    return path


def _make_bore_filled_m5(spec, work_dir: Path) -> Path:
    """M5 with neither bore nor fin slots: the solid domed capsule.

    Fused with the bore rather than left bare, for the same reason as `_make_bore_filled_m2`:
    the raw revolve's on-axis meridian edge survives in memory but not a STEP round-trip, so the
    bare capsule would fail `brep_valid` before the scorer ever reached `volume_err_pct`.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    L, R_o = spec.params["L"], spec.params["R_o"]
    dome_h = spec.params["dome_semi_axial"]
    outer = generators._capsule_outer_shape(L, R_o, dome_h)
    bore = generators._straight_bore(spec.params["R_bore"], L)
    fuse = BRepAlgoAPI_Fuse(outer, bore)
    fuse.Build()
    if not fuse.IsDone():
        raise RuntimeError("M5 bore-filler fuse failed")
    path = work_dir / "M5_bore_filled.step"
    generators._write_step(fuse.Shape(), path)
    return path


def _make_bore_filled_m6(spec, work_dir: Path) -> Path:
    """M6 without the star loft cut: a solid cylinder at R_o, same length — should badly fail
    volume_err_pct against the lofted-bore truth."""
    L, R_o = spec.params["L"], spec.params["R_o"]
    shape = BRepPrimAPI_MakeCylinder(R_o, L).Shape()
    path = work_dir / "M6_bore_filled.step"
    generators._write_step(shape, path)
    return path


def _make_bore_filled_m7(spec, work_dir: Path) -> Path:
    """M7 with neither central bore nor satellite perforations: a solid cylinder at R_o, same
    length — should badly fail volume_err_pct against the multi-cutter truth."""
    L, R_o = spec.params["L"], spec.params["R_o"]
    shape = BRepPrimAPI_MakeCylinder(R_o, L).Shape()
    path = work_dir / "M7_bore_filled.step"
    generators._write_step(shape, path)
    return path


def _make_bore_filled_m11(spec, work_dir: Path) -> Path:
    """M11 with each segment's bore filled: 3 disjoint solid cylinders, same z-spans and gaps
    (n_solids stays 3) — should badly fail volume_err_pct against the annular-segment truth."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    R_o = spec.params["R_o"]
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for seg in spec.params["segments"]:
        ax = gp_Ax2(gp_Pnt(0.0, 0.0, seg["z_lo"]), gp_Dir(0.0, 0.0, 1.0))
        solid = BRepPrimAPI_MakeCylinder(ax, R_o, seg["z_hi"] - seg["z_lo"]).Shape()
        builder.Add(compound, solid)
    path = work_dir / "M11_bore_filled.step"
    generators._write_step(compound, path)
    return path


def _make_m11_mass_shifted(spec, work_dir: Path) -> Path:
    """M11 with the SAME total volume as truth (so volume_err_pct alone can't catch it) but mass
    shifted between segments A and C: A's bore shrunk, C's bore grown by the matching amount.
    A and C share R_o, R_i and length, so shrinking A's cross-section area by `shift` and growing
    C's by `shift` leaves the sum exactly unchanged while each segment is off by
    shift/base_area (1%) — this is what per_solid_volume_err_pct exists to catch."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    R_o = spec.params["R_o"]
    segs = {s["name"]: s for s in spec.params["segments"]}
    base_area = R_o**2 - segs["A"]["R_i"]**2
    # Must stay well under segs["A"]["R_i"]**2 (90000) or R_i_new**2 = R_i**2 - shift goes
    # negative; 1% of base_area (9100) is comfortably inside that and still >> the 0.05% gate.
    shift = 0.01 * base_area

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for seg in spec.params["segments"]:
        if seg["name"] == "A":
            r_i = (R_o**2 - (base_area + shift)) ** 0.5
        elif seg["name"] == "C":
            r_i = (R_o**2 - (base_area - shift)) ** 0.5
        else:
            r_i = seg["R_i"]
        solid = generators._annular_segment(R_o, r_i, seg["z_lo"], seg["z_hi"])
        builder.Add(compound, solid)
    path = work_dir / "M11_mass_shifted.step"
    generators._write_step(compound, path)
    return path


def _make_bore_filled_m8(spec, work_dir: Path) -> Path:
    """M8 with neither central bore nor obround slots: the plain domed capsule outer shape —
    should badly fail volume_err_pct against the dilated-cavity truth.

    Fused (not bare) with a straight bore that sits entirely inside the capsule, same trick as
    `_make_bore_filled_m5`: the fuse doesn't change the volume (the bore adds no material outside
    the capsule) but routes the shape through a boolean op, which OCCT's algorithm implicitly
    heals via ShapeFix. The bare revolved capsule alone reads back from a STEP round-trip as an
    invalid BRep (a periodic-seam tolerance quirk), which would trip `brep_valid` before the
    check under test (`volume_err_pct`) is ever reached.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    L = spec.params["L"]
    outer = generators._capsule_outer_shape(L, spec.params["R_o"], spec.params["dome_semi_axial"])
    bore = generators._straight_bore(spec.params["R_bore"], L)
    fuse = BRepAlgoAPI_Fuse(outer, bore)
    fuse.Build()
    if not fuse.IsDone():
        raise RuntimeError("M8 bore-filler fuse failed")
    path = work_dir / "M8_bore_filled.step"
    generators._write_step(fuse.Shape(), path)
    return path


def _make_bore_filled_m9(spec, work_dir: Path) -> Path:
    """M9 reuses M8's exact params (`ms._m9()` copies `dict(m8.params)`), so the same
    bore-filler trick as `_make_bore_filled_m8` applies unchanged: fuse (not bare) a straight
    bore into the plain domed capsule to heal the periodic-seam STEP round-trip quirk while
    leaving the volume wrong versus the dilated-cavity truth."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    L = spec.params["L"]
    outer = generators._capsule_outer_shape(L, spec.params["R_o"], spec.params["dome_semi_axial"])
    bore = generators._straight_bore(spec.params["R_bore"], L)
    fuse = BRepAlgoAPI_Fuse(outer, bore)
    fuse.Build()
    if not fuse.IsDone():
        raise RuntimeError("M9 bore-filler fuse failed")
    path = work_dir / "M9_bore_filled.step"
    generators._write_step(fuse.Shape(), path)
    return path


def _make_bore_filled_m12(spec, work_dir: Path) -> Path:
    """M12 with neither central bore nor obround slots: the plain domed capsule outer shape —
    should badly fail volume_err_pct against the near-burnout dilated-cavity truth.

    Fused (not bare) with a straight bore, same trick as `_make_bore_filled_m8`/`_m5`: heals the
    bare revolve's periodic-seam STEP round-trip quirk without changing the volume.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    L = spec.params["L"]
    outer = generators._capsule_outer_shape(L, spec.params["R_o"], spec.params["dome_semi_axial"])
    bore = generators._straight_bore(spec.params["R_bore"], L)
    fuse = BRepAlgoAPI_Fuse(outer, bore)
    fuse.Build()
    if not fuse.IsDone():
        raise RuntimeError("M12 bore-filler fuse failed")
    path = work_dir / "M12_bore_filled.step"
    generators._write_step(fuse.Shape(), path)
    return path


def _make_bore_filled_m10(spec, work_dir: Path) -> Path:
    """M10 with neither central bore nor obround slots (same trick as `_make_bore_filled_m8`),
    built at M8's full scale then scaled/rotated/translated into M10's frame via
    `generators._place_in_frame` (see its docstring for why full-scale-then-shrink beats
    building directly at 1/40 scale) so the mutation is a fair apples-to-apples comparison
    against the transformed truth."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    m8_params = ms.get("M8").params
    L = m8_params["L"]
    outer = generators._capsule_outer_shape(L, m8_params["R_o"], m8_params["dome_semi_axial"])
    bore = generators._straight_bore(m8_params["R_bore"], L)
    fuse = BRepAlgoAPI_Fuse(outer, bore)
    fuse.Build()
    if not fuse.IsDone():
        raise RuntimeError("M10 bore-filler fuse failed")
    scale = spec.params["L"] / L
    placed = generators._place_in_frame(fuse.Shape(), spec.frame, scale=scale)
    path = work_dir / "M10_bore_filled.step"
    generators._write_step(placed, path)
    return path


def _make_bore_filled_m13(spec, work_dir: Path) -> Path:
    """M13 reuses M12's exact params (`ms._m13()` copies `dict(m12.params)`), so the same
    bore-filler trick as `_make_bore_filled_m12` applies, then placed in M13's frame (same
    rotate+translate, scale=1.0, as `_make_bore_filled_m10` does for M10's frame) so the
    mutation is a fair apples-to-apples comparison against the transformed truth."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    L = spec.params["L"]
    outer = generators._capsule_outer_shape(L, spec.params["R_o"], spec.params["dome_semi_axial"])
    bore = generators._straight_bore(spec.params["R_bore"], L)
    fuse = BRepAlgoAPI_Fuse(outer, bore)
    fuse.Build()
    if not fuse.IsDone():
        raise RuntimeError("M13 bore-filler fuse failed")
    placed = generators._place_in_frame(fuse.Shape(), spec.frame, scale=1.0)
    path = work_dir / "M13_bore_filled.step"
    generators._write_step(placed, path)
    return path


_BORE_FILLERS = {"M1": _make_bore_filled_m1, "M2": _make_bore_filled_m2,
                  "M3": _make_bore_filled_m3, "M4": _make_bore_filled_m4,
                  "M5": _make_bore_filled_m5, "M6": _make_bore_filled_m6,
                  "M7": _make_bore_filled_m7, "M8": _make_bore_filled_m8,
                  "M9": _make_bore_filled_m9,
                  "M10": _make_bore_filled_m10,
                  "M11": _make_bore_filled_m11, "M12": _make_bore_filled_m12,
                  "M13": _make_bore_filled_m13}


def check_mr(name: str = "MR") -> None:
    """MR (MISSION §6.2) has no analytic truth and never goes through `generators.make` —
    `score.py::_score_mr` handles the real_inputs/*.stl glob (or its absence) itself. Prove only
    the harness-side contract: with `real_inputs/` empty (the default, gitignored) the scorer
    must emit `pass: true, skipped: "no real input"` rather than erroring or hanging."""
    spec = ms.get(name)
    real_inputs = list(REPO_ROOT.glob(spec.input_glob))
    result = score_mod.score(name, keep_dir=None)
    if real_inputs:
        print(f"[INFO] {name}: real_inputs/ is non-empty ({len(real_inputs)} file(s)) — "
              f"scored for real, pass={result['pass']}", flush=True)
        _report(result["pass"], f"{name}: real input scores pass",
                "" if result["pass"] else f"first_failure={result['first_failure']}")
        return
    ok = result.get("pass") is True and result.get("skipped") == "no real input"
    print(f"[SKIP] {name}: no real_inputs/*.stl present", flush=True)
    _report(ok, f"{name}: skips cleanly with no real input",
            "" if ok else f"result={result}")


def check_milestone(name: str, work_dir: Path, skip_gmsh: bool = False) -> None:
    if name == "MR":
        check_mr(name)
        return
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
    result = _score_with_step(name, truth.step_path, truth)
    _report(result["pass"], f"{name}: truth STEP passes all gates",
            "" if result["pass"] else f"first_failure={result['first_failure']}")

    # 2b. the report-derived gates must actually bite: a perfect solid with a report that admits
    # too few dome stations / no topology event / a uniform station count must FAIL. Without this
    # a mis-wired gate would be indistinguishable from a passing one.
    for gate, check, mutate, label in (
        ("dome_stations_min", "dome_stations_min",
         lambda r: {**r, "stations_z_mm": r["stations_z_mm"][:3], "n_stations": 3},
         "too few dome stations"),
        ("topo_event_z_tolerance_mm", "topo_event_z",
         lambda r: {**r, "topology_events_z_mm": []}, "no topology event"),
        ("topo_events", "topo_events",
         lambda r: {**r, "topology_events_z_mm": []}, "no topology events reported"),
        ("topo_events", "topo_events",
         lambda r: {**r, "topology_events_z_mm": r["topology_events_z_mm"]
                    + [r["topology_events_z_mm"][0] + 37.0] * 20},
         "too many spurious topology events"),
        ("adaptive_efficiency", "adaptive_efficiency",
         lambda r: {**r, "n_stations": 100_000}, "station count not adaptive"),
        # --- Round 2 gates. Each mutation is the minimum lie that should trip exactly one gate;
        # if a gate is ever unwired (as frame_axis_err_deg and axial_extent_err_mm were), the
        # first_failure comes back as some *other* check and these checks fail loudly.
        ("station_bands", "stations_consistent",
         lambda r: {**r, "n_stations": len(r["stations_z_mm"]) + 7},
         "n_stations disagrees with stations_z_mm"),
        ("station_bands", "stations_consistent",
         lambda r: {**r, "stations_z_mm": sorted(r["stations_z_mm"]) + [1e6],
                    "n_stations": len(r["stations_z_mm"]) + 1},
         "a station outside the axial extent"),
        # MISSION §6.2's explicit claim, made testable: Round 1's cosine warp at n=80 is inside
        # the station budget and satisfies the dome gate, yet starves the mid-barrel bands.
        ("station_bands", "station_bands",
         lambda r: _stations_cosine(r, 80),
         "Round 1 cosine end-clustering at n=80 starves the feature bands"),
        ("n_stations_max", "n_stations_max",
         lambda r: _stations_uniform(r, 10_000),
         "10 000 uniform stations blow the station budget"),
        ("frame_axis_err_deg", "frame_axis_err_deg",
         lambda r: _frame_axis_rotated(r, 5.0), "motor axis rotated 5 degrees"),
        ("axial_extent_err_mm", "axial_extent_err_mm",
         lambda r: {**r, "axial_extent_mm": r["axial_extent_mm"] - 50.0},
         "axial extent short by 50 mm"),
    ):
        if gate not in spec.gates:
            continue
        result = _score_with_step(name, truth.step_path, truth, report_mutate=mutate)
        ok = (not result["pass"]) and result["first_failure"] is not None \
            and result["first_failure"]["check"] == check
        _report(ok, f"{name}: {label} fails on {check}",
                f"first_failure={result['first_failure']}")

    # 2c. the per-region deviation gate must bite on an error the *global* p99 dilutes away.
    # It cannot be provoked with a perturbed shape: to survive the global p99 the bad band has to
    # hold under 1 % of the pooled sample points, and no region band of these milestones is that
    # small — which is exactly why the check is worth having, and why the only honest way to prove
    # it is wired is to feed the check a by_z_bin table with one region over the gate while the
    # global max/p99 stay clean. A gate that is never exercised is a gate that does not exist.
    if "surface_deviation_p99_mm" in spec.gates:
        real_dev = metrics.surface_deviation

        def dev_with_one_bad_region(*a, **kw):
            dev = real_dev(*a, **kw)
            bins = [b for b in (dev.get("by_z_bin") or [])
                    if b["n_points"] >= score_mod._MIN_BIN_POINTS]
            if bins:
                bins[-1]["p99_mm"] = 5.0   # >> any milestone's p99 gate
            return dev

        metrics.surface_deviation = dev_with_one_bad_region
        try:
            result = _score_with_step(name, truth.step_path, truth)
        finally:
            metrics.surface_deviation = real_dev
        ok = (not result["pass"]) and result["first_failure"] is not None \
            and result["first_failure"]["check"] == "surface_deviation_p99_by_region"
        _report(ok, f"{name}: one bad region fails on surface_deviation_p99_by_region",
                f"first_failure={result['first_failure']}")

    # 2d. per_solid_volume_err_pct must bite when mass is shifted between solids but the total
    # is preserved (so the plain volume_err_pct check alone would pass this shape).
    if "per_solid_volume_err_pct" in spec.gates and name == "M11":
        shifted_path = _make_m11_mass_shifted(spec, work_dir)
        result = _score_with_step(name, shifted_path, truth)
        ok = (not result["pass"]) and result["first_failure"] is not None \
            and result["first_failure"]["check"] == "per_solid_volume_err_pct"
        _report(ok, f"{name}: mass-shifted-but-same-total copy fails on per_solid_volume_err_pct",
                f"first_failure={result['first_failure']}")

    # 3. scaled copy fails on volume_err_pct
    scaled_path = _make_scaled_copy(truth, work_dir)
    result = _score_with_step(name, scaled_path, truth)
    ok = (not result["pass"]) and result["first_failure"] is not None \
        and result["first_failure"]["check"] == "volume_err_pct"
    _report(ok, f"{name}: 1.01x-scaled copy fails on volume_err_pct",
            f"first_failure={result['first_failure']}")

    # 4. bore-filled copy fails on volume_err_pct
    if name in _BORE_FILLERS:
        filled_path = _BORE_FILLERS[name](spec, work_dir)
        result = _score_with_step(name, filled_path, truth)
        ok = (not result["pass"]) and result["first_failure"] is not None \
            and result["first_failure"]["check"] == "volume_err_pct"
        _report(ok, f"{name}: bore-filled copy fails on volume_err_pct",
                f"first_failure={result['first_failure']}")
    else:
        _report(False, f"{name}: bore-filled copy fails on volume_err_pct",
                "no filler builder registered in _BORE_FILLERS — MISSION §7 requires a "
                "bore-filled perturbation per milestone")

    # 5. gmsh can mesh the truth STEP
    if skip_gmsh:
        print(f"[SKIP] {name}: gmsh can mesh truth STEP (--skip-gmsh)", flush=True)
        return
    hmax = spec.params.get("R_o", 1000.0) / 10.0
    mesh_res = mc.check_meshability(str(truth.step_path), hmax, timeout_s=spec.runtime_cap_s)
    _report(bool(mesh_res.get("ok")), f"{name}: gmsh can mesh truth STEP",
            f"n_tet={mesh_res.get('n_tet')} min_quality={mesh_res.get('min_quality')}")


def check_distance_kernel() -> None:
    """`metrics.point_mesh_distance` must agree exactly with trimesh's reference query.

    It is hand-written: it replaced `ProximityQuery.on_surface`, which sizes its search box by the
    distance to the nearest mesh *vertex* and therefore exhausts memory on OCC's strip
    tessellations (iteration 11). Every deviation gate in the scorer rests on it, and the failure
    that matters is silent — a search radius that is too small under-reports the distance, so a
    wrong solid would score as a good one and the loop would optimise toward nothing. Nothing else
    in the harness would notice, so validate it here, on M1 (872 faces: small enough for the
    reference implementation to be affordable) across the regimes the scorer actually hits.
    """
    try:
        truth = generators.make("M1")
    except NotImplementedError:
        _report(False, "distance kernel: M1 generator required")
        return

    mesh = metrics.load_mesh(truth.stl_path)
    rng = np.random.default_rng(12345)
    on = trimesh.sample.sample_surface(mesh, 2000, seed=3)[0]
    lo, hi = mesh.bounds
    pad = 0.5 * (hi - lo)
    cases = {
        "on-surface": on,
        "near (sigma 0.3 mm)": on + rng.normal(0.0, 0.3, on.shape),
        # The far case is the one that exercises the radius bound: a big box means many candidate
        # triangles and the chunked reduction that keeps peak memory bounded.
        "far (bbox padded 50%)": rng.uniform(lo - pad, hi + pad, (2000, 3)),
        "on the motor axis (r=0)": np.column_stack([
            np.zeros(500), np.zeros(500), np.linspace(lo[2], hi[2], 500)]),
    }

    worst_abs, worst_under, where = 0.0, 0.0, ""
    for label, pts in cases.items():
        mine = metrics.point_mesh_distance(mesh, pts)
        ref = trimesh.proximity.ProximityQuery(mesh).on_surface(pts)[1]
        diff = mine - ref
        if float(np.abs(diff).max()) > worst_abs:
            worst_abs, where = float(np.abs(diff).max()), label
        worst_under = min(worst_under, float(diff.min()))

    ok = worst_abs < 1e-6 and np.isfinite(worst_abs)
    _report(ok, "distance kernel matches trimesh's exact query",
            f"max |diff|={worst_abs:.2e} mm ({where}), max under-report={worst_under:.2e} mm")


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

    r1 = strip_timing(_score_with_step("M1", truth.step_path, truth))
    r2 = strip_timing(_score_with_step("M1", truth.step_path, truth))
    detail = ""
    if r1 != r2:
        diffs = [k for k in r1 if r1[k] != r2.get(k)]
        detail = f"differing keys: {diffs}"
    _report(r1 == r2, "determinism: scoring identical inputs twice yields identical results",
            detail)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--milestone", action="append", choices=list(ms.MILESTONES),
                        help="only check these milestones (repeatable); default: all")
    parser.add_argument("--skip-gmsh", action="store_true",
                        help="skip the gmsh meshability check on each truth STEP")
    args = parser.parse_args(argv)
    selected = args.milestone or list(ms.MILESTONES)
    partial = len(selected) < len(ms.MILESTONES) or args.skip_gmsh

    work_dir = Path(tempfile.mkdtemp(prefix="selftest_"))
    try:
        # Runs even for a narrowed selection: it is a unit check on the metric kernel every
        # milestone's deviation gate depends on, and it costs ~2 s.
        check_distance_kernel()
        for name in selected:
            check_milestone(name, work_dir, skip_gmsh=args.skip_gmsh)
        if not partial:
            check_determinism()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # The driver's SCORE_TIMEOUT_S is 3600 s and it restores harness/ from a tag once frozen, so
    # a selftest that creeps up on that budget is a latent permanent stall. Report the margin.
    print(f"\ntotal {time.time() - _T0:.1f}s (driver SCORE_TIMEOUT_S = 3600 s)", flush=True)
    if FAILURES:
        print(f"SELFTEST FAILED ({len(FAILURES)} check(s)):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    if partial:
        print("SELFTEST PASSED (PARTIAL — narrowed by CLI flags; not evidence for the M0 freeze)")
    else:
        print("SELFTEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
