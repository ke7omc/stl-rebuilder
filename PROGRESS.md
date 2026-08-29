# PROGRESS — lab notebook of the loop (agent-maintained)

## Current state
- Milestone: M0 — harness under construction. `harness/milestones.py` (all 5 specs),
  `harness/generators.py` (M1 only), `harness/metrics.py` (volume/CoM/inertia, symmetric
  surface deviation with by_z_bin, STEP round-trip), `harness/meshcheck.py` (gmsh headless
  meshability check, run in a subprocess), and `harness/score.py` (full CLI contract) are done
  and verified against M1 truth (score.py both fails correctly on the stub pipeline and passes
  every check when the pipeline emits the truth STEP itself).
- `harness/score.py --milestone M1 --out out/score.json` against the current stub `rebuild.py`
  exits 1 and writes contract-valid JSON with `first_failure.check == "pipeline_exit"` — this
  is exactly the M0 gate condition in MISSION §6 (score.py just needs to run to completion with
  valid JSON, pass or fail; the stub correctly fails, not errors — exit 1, never 2).
- Truth files written: `harness/truth/M1.step`, `harness/truth/M1.stl`.
- Unit tests: `tests/test_metrics.py` (6) + `tests/test_meshcheck.py` (3) + `tests/test_score.py`
  (3) — 12 total, all pass. test_score.py covers: stub pipeline fails fast at pipeline_exit with
  progress in [0,1); a monkeypatched pipeline that emits the truth STEP passes every check in
  order (input_watertight → ... → gmsh_tet); result is JSON-serializable with all contract keys.
- Still needed: `harness/selftest.py` (per MISSION §7: closed-form check, truth-STEP-passes,
  perturbed-copy-fails-expected-check, determinism), and M2–M5 generators.

## Do not retry
- `BRepAlgoAPI_Cut.HasErrors()` does not exist in OCP 7.9.3. Use `IsDone()` only.

## Log
(newest first — one block per iteration, format in MISSION.md §8)

### iter 4 — M0 — sonnet/medium — 2026-08-29T08:25
- Score before: no score.py; meshcheck.py just landed. PROGRESS.md pointed at score.py as the
  next step.
- Change: built `harness/score.py` per MISSION §7's contract: `score(milestone, keep_dir)`
  runs cheap→expensive fail-fast checks — input_watertight, pipeline_exit (subprocess in a temp
  cwd on a neutrally-named `input.stl` copy, milestone `runtime_cap_s` timeout),
  output_step_exists, step_readable, n_solids (TopExp.MapShapes_s + TopTools_IndexedMapOfShape,
  gated on `spec.gates`), brep_valid (BRepCheck_Analyzer), volume_err_pct (closed-form when
  present else generator's V_truth), surface_deviation_max/p99 (re-tessellates the result STEP
  at CHORD_TOL via BRepMesh_IncrementalMesh + StlAPI_Writer so it's comparable to the truth STL,
  then metrics.surface_deviation with by_z_bin), face_count_max, step_roundtrip, gmsh_tet (via
  meshcheck). Each gate check is only run `if <gate_name> in spec.gates`, so M2-M5's
  milestone-specific gates (dome_stations_min, topo_event_z_tolerance_mm, adaptive_efficiency)
  are silently skipped for now — they need generator/pipeline logic that doesn't exist yet and
  will be wired in when those milestones' generators land. `main()` writes `out/score.json`
  (mkdir -p parent), returns 0/1/2, and wraps the whole `score()` call in try/except so a bug in
  the harness itself is reported as stage `harness_error` with exit 2 instead of a bare crash.
  `progress` follows the spec formula exactly: (index_of_first_failure + partial)/n_checks,
  partial = clamp(threshold/value, 0, 1) for numeric checks. Added `tests/test_score.py` (3
  tests, monkeypatches `_run_pipeline` to fake a pipeline that copies the truth STEP through, to
  test the full pass path without a real pipeline).
- Score after (local): `harness/score.py --milestone M1 --out out/score.json` against the
  current stub rebuild.py → exit 1, `pass: false`, `progress: 0.5`, `first_failure.check:
  "pipeline_exit"`, `stderr_tail` shows the stub's exit-3 message — exactly the M0 gate target.
  Manually swapped in a fake rebuild.py that copies `harness/truth/M1.step` to the output path
  (not committed, reverted after) to prove the rest of the chain: exit 0, `pass: true, progress:
  1.0`, all 12 checks pass in order (volume_err_pct ~2e-12%, surface deviation ~7e-8mm, gmsh
  min_quality 0.234). `pytest tests/` → 12 passed (was 9).
- Learned: `TopExp.MapShapes_s(shape, TopAbs_SOLID, TopTools_IndexedMapOfShape())` is the OCP way
  to count/dedupe sub-shapes of a type (no plain `TopExp_Explorer` iteration needed for a simple
  count). `_run_pipeline`'s subprocess cwd matters — the milestone's `rebuild_args` are relative
  strings, and rebuild.py must be invoked with `cwd=work_dir` on a copy named `input.stl` so
  paths are neutral per MISSION §7 ("a neutrally named copy of the STL").
- Next: `harness/selftest.py` — the M0 gate's other half. Needs: closed-form volume check for
  M1; score the truth STEP itself through score.py and assert it passes every gate; score a
  1.01×-scaled copy and a bore-filled copy and assert each fails on `volume_err_pct` with a
  sensible hint; run score.py twice on the same inputs and diff for determinism. Exit 0 only if
  all of that holds. After that, M0's two gate conditions (`selftest.py` exit 0 +
  `score.py --milestone M1` contract-valid) are both met and the harness can be frozen.

### iter 3 — M0 — sonnet/medium — 2026-08-29T08:23
- Score before: no score.py yet, so no formal harness score; PROGRESS.md pointed at
  meshcheck.py as the next step after metrics.py.
- Change: built `harness/meshcheck.py` per docs/research/02-...md §6 — `check_meshability(
  step_path, hmax, timeout_s=120.0) -> dict` spawns `python meshcheck.py <step> <hmax>` as a
  subprocess (gmsh crash/hang cannot kill the scorer), parses its JSON stdout. The in-subprocess
  `_run()` does the gmsh import/mesh/quality-check exactly per the research doc snippet: OCC
  importShapes, Mesh.MeshSizeMax/Min, generate(3), getElementQualities minSICN, checks BOTH the
  exception path and the 0-tet/logger-Error path (gmsh can complete generate(3) after only
  surface meshing without raising). Subprocess crash, timeout, or unparseable stdout all return
  `{"ok": False, "reason": ...}` instead of raising. Added `tests/test_meshcheck.py`.
- Score after (local): no score.py so no formal harness score; manually verified —
  `check_meshability("harness/truth/M1.step", hmax=100)` → ok=True, n_tet=133384,
  min_quality=0.234 (> gate 0.1), 0 errors. Missing-file path returns ok=False with the gmsh
  error text, no exception raised. `pytest tests/` → 9 passed (6 metrics + 3 meshcheck).
- Learned: gmsh 4.15.2 (pip wheel) ships OCCT compiled in — STEP import works with zero extra
  installs. `gmsh.model.occ.importShapes` raises a plain `Exception` (not a gmsh-specific type)
  on a bad path, with the real reason in `gmsh.logger.getLastError()`'s text embedded in the
  message. Subprocess approach means `gmsh.finalize()` not reached on that raised-exception path
  in the child, but that's fine — the child process exits and is fully torn down either way.
- Next: `harness/score.py` — wire milestones+generators+metrics+meshcheck into the fail-fast
  CLI contract from MISSION.md §7 (run rebuild.py as subprocess in a temp cwd on a neutrally
  named STL copy, cheap→expensive checks, progress formula, hints, exit 0/1/2, out/score.json).
  Then `harness/selftest.py`.

### iter 2 — M0 — sonnet/medium — 2026-08-29T08:15
- Score before: no score.json yet; PROGRESS.md pointed at metrics.py as the next step.
- Change: built `harness/metrics.py` — `load_mesh`, `volume_com_inertia` (gates on `.is_volume`,
  returns volume_err_pct/com_err_frac/inertia_err_frac), `surface_deviation` (100k-sample +
  vertices, both directions, max/p99/rms/argmax, optional `by_z_bin` region table per MISSION §7
  contract), `read_step`/`step_roundtrip_check` (STEPControl_Reader + BRepGProp, never raises,
  reports a failed check dict on read errors). Added `tests/test_metrics.py`.
- Score after (local): no score.py yet so no formal harness score; manually verified against
  `harness/truth/M1.*`: self-deviation max=7e-8mm, STEP volume vs closed-form rel err <1e-15,
  1%-scaled mesh correctly flagged (deviation ~100mm, volume_err_pct ~3%). `pytest
  tests/test_metrics.py -q` → 6 passed.
- Learned: `STEPControl_Reader.ReadFile` returns `IFSelect_ReturnStatus.IFSelect_RetDone` (enum,
  compare directly, no `.value` needed) in OCP 7.9.3; `TransferRoots()` returns the count
  transferred. `trimesh.proximity.ProximityQuery(mesh).on_surface(pts)` returns
  `(closest_points, distance, triangle_id)` as documented — confirmed live.
- Next: `harness/meshcheck.py` (gmsh subprocess check per docs/research/02 §6), then
  `harness/score.py` wiring milestones+generators+metrics+meshcheck into the fail-fast CLI
  contract, then `harness/selftest.py`.

### iter 1 — M0 — sonnet/medium — 2026-08-29T08:09
- Score before: no score.json (first iteration)
- Change: built `harness/milestones.py` (all 5 specs with params/regions/gates/args/runtime caps)
  and `harness/generators.py` (M1 annular cylinder only; M2-M5 raise NotImplementedError).
  Fixed: `BRepAlgoAPI_Cut.HasErrors()` absent in OCP 7.9.3 — removed that check.
- Score after (local): M1 BRepGProp volume = 2.858849e+10 mm³, closed-form same → 0 % error.
  STEP and STL written successfully.
- Learned: OCP 7.9.3 BRepAlgoAPI_Cut exposes only IsDone(); no HasErrors(). BRepGProp
  returns exact volume for boolean-cut cylinders (no floating-point error at all).
- Next: build `harness/metrics.py` (volume/CoM/inertia from trimesh; symmetric deviation;
  STEP round-trip) and `harness/meshcheck.py` (gmsh subprocess check).
