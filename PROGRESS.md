# PROGRESS — lab notebook of the loop (agent-maintained)

## Current state
- Milestone: M0 — both M0 gate conditions now hold. `harness/milestones.py` (all 5 specs),
  `harness/generators.py` (M1 only), `harness/metrics.py`, `harness/meshcheck.py`,
  `harness/score.py` (full CLI contract), and now `harness/selftest.py` are done.
  `.venv/bin/python harness/selftest.py` exits 0 (prints a PASS/FAIL line per check + summary).
  `harness/score.py --milestone M1 --out out/score.json` against the stub `rebuild.py` exits 1
  with contract-valid JSON, `first_failure.check == "pipeline_exit"` — exactly MISSION §6's M0
  gate. Only M2-M5 generators remain before harness-freeze (still inside M0).
- `harness/selftest.py` iterates `ms.MILESTONES`, skipping any whose generator raises
  `NotImplementedError` (currently M2-M5) so it needs no edits as those land. For each
  implemented milestone (M1) it checks: closed-form volume match, truth STEP passes every gate
  (via `score.score()` with `_run_pipeline` monkeypatched to copy a given STEP — same pattern
  `tests/test_score.py` already used), a 1.01x-scaled copy fails on `volume_err_pct`, a
  bore-filled copy fails on `volume_err_pct` (filler builders registered per-milestone in
  `_BORE_FILLERS`, only M1 so far), and gmsh can mesh the truth STEP. Plus one
  milestone-independent determinism check: score the same stub-pipeline inputs twice, diff
  after stripping `runtime_s` (the only nondeterministic field).
- Truth files written: `harness/truth/M1.step`, `harness/truth/M1.stl`.
- Unit tests: `tests/test_metrics.py` (6) + `tests/test_meshcheck.py` (3) + `tests/test_score.py`
  (3) + `tests/test_selftest.py` (1) — 13 total, all pass.
- Still needed: M2–M5 generators (each with truth volume recorded in `harness/truth/Mk.json`
  per MISSION §7), then wire their milestone-specific gates into score.py, then one Opus
  review pass, then `harness-frozen` tag.

## Do not retry
- `BRepAlgoAPI_Cut.HasErrors()` does not exist in OCP 7.9.3. Use `IsDone()` only.

## Log
(newest first — one block per iteration, format in MISSION.md §8)

### iter 5 — M0 — sonnet/medium — 2026-08-29T08:30
- Score before: score.py done and committed; PROGRESS.md pointed at selftest.py as the next
  step (the other half of the M0 gate).
- Change: built `harness/selftest.py` per MISSION §7. `check_milestone()` runs per-milestone
  (closed-form volume, truth-STEP-passes-all-gates, scaled-copy-fails, bore-filled-fails,
  gmsh-can-mesh), skipping milestones whose generator isn't built yet via a
  `NotImplementedError` catch so the file doesn't need touching again as M2-M5 land.
  `_score_with_step()` reuses `score.score()` by monkeypatching `_run_pipeline` to copy a given
  STEP file in as the "pipeline output" — the same trick `tests/test_score.py` already used, so
  no changes to score.py were needed. Scaled copy built with
  `BRepBuilderAPI_Transform(shape, gp_Trsf().SetScale(origin, 1.01), True)`. Bore-filled copy is
  a milestone-specific builder registered in `_BORE_FILLERS` (only `_make_bore_filled_m1` so
  far: a solid R_o cylinder with no inner cut). `check_determinism()` scores the stub pipeline
  twice and diffs after stripping `runtime_s` (the only field expected to vary run-to-run).
  Added `tests/test_selftest.py` (1 test: `main()` exits 0 with no FAILURES).
- Score after (local): `harness/selftest.py` → exit 0, all 6 implemented checks PASS (M1
  closed-form rel_err=0; truth STEP passes all gates; scaled copy fails volume_err_pct at
  3.03%; bore-filled fails volume_err_pct at 9.89%; gmsh meshes truth at min_quality=0.234;
  determinism holds). M2-M5 correctly SKIP. `pytest tests/` → 13 passed (was 12). This is the
  second (and last remaining) M0 gate condition from MISSION §6 — both now hold with the stub
  pipeline in place.
- Learned: `gp_Trsf().SetScale(gp_Pnt, factor)` + `BRepBuilderAPI_Transform(shape, trsf, True)`
  is the OCP way to uniformly scale a shape (confirmed live: 1.01 scale factor → 1.030301x
  volume, matches (1.01)^3 exactly as expected for a solid).
- Next: M2 generator (`harness/generators.py::_make_m2` — 2:1 ellipsoidal domes on the outer
  surface both ends, straight bore through, per MISSION §7 build-order step 5 and
  `milestones.py::_m2`'s params/regions/gates already defined). After M2-M5 generators land and
  their gates are wired into score.py, do the review-harness pass MISSION §11 describes before
  the freeze.

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
