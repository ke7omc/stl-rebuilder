# PROGRESS — lab notebook of the loop (agent-maintained)

## Current state
- Milestone: M0, phase **build** (the review pass ran at iter 6 and deliberately pushed the
  phase back — see below). `harness/` modules all exist; `harness/selftest.py` now **exits 1**
  by design because the M4–M5 generators are still `NotImplementedError` (M2 landed iter 7, M3
  landed iter 8).
- **The one thing that matters right now: build the M4–M5 generators.** The driver tags
  `harness-frozen` the instant `selftest.py` exits 0 and the M1 scorer emits contract-valid
  JSON (`loop.py::evaluate`, ~line 529). Until iter 6, `selftest.py` *skipped* unimplemented
  generators, so it would have exited 0 and frozen a harness that can only score 1 of 5
  milestones — the loop would then have reached M2 and stalled forever with no legal way to
  fix `harness/`. The selftest now FAILS on any unimplemented generator, which is what keeps
  the phase in `build` until the harness is genuinely complete.
- Order to build them in (one per iteration, MISSION §2.1): M4 (finocyl, 8 fin slots aft of
  z=6000 with a flat fore wall) → M5 (M2 domes + M4 fins). Each needs, in `generators.py`, a
  `_make_mK()` returning a `Truth` (mirror `_make_m1`/`_make_m2`/`_make_m3`), and in
  `selftest.py` a bore-filled perturbation registered in `_BORE_FILLERS` — a missing filler is
  now also a selftest FAILURE (MISSION §7 requires the perturbation test per milestone).
  `milestones.py` already has all five specs, so no spec work is needed.
- `harness/score.py` runs 14 checks for M1 (`score_mod.check_plan(spec)` is the authoritative
  ordered list). Against the stub `rebuild.py` it exits 1 with contract-valid JSON,
  `first_failure.check == "pipeline_exit"`, `progress 0.0714`.
- Truth files written: `harness/truth/M1.step`, `harness/truth/M1.stl` (and M2/M3 truth files
  when `selftest.py`/`generators.make` regenerate them — all gitignored, deterministic).
- Unit tests: `tests/test_metrics.py` (6) + `test_meshcheck.py` (3) + `test_score.py` (6) +
  `test_selftest.py` (1) — 16 total, all pass (~340 s; the scorer runs gmsh several times per
  milestone, and M1–M3 are all exercised now).
- **Expect the driver to report a stall every M2–M5 generator iteration, and do not read it as
  a real regression.** `loop.py::m0_build_progress` caps at 0.6 (0.1 per harness module, all 6
  already present) while `best_progress` is already 0.95 from the pre-review "harness complete"
  signal, so no build iteration can ever beat it. Consequences: the model escalates to Opus at
  `STALL_ESCALATE=3`, tournaments are suppressed at M0 (`loop.py:421`), and the loop halts with
  a status report at `STALL_MAX=12`. Four generators at one to two iterations each fits inside
  that budget, but do not burn iterations on side quests. `loop.py` is frozen, so this cannot
  be fixed from inside the loop; the stall counter resets when M1 begins.

## Do not retry
- `BRepAlgoAPI_Cut.HasErrors()` does not exist in OCP 7.9.3. Use `IsDone()` only.
- Do not make `selftest.py` skip (rather than fail) an unimplemented milestone generator. It
  reads like a convenience, but it is the exact hole that lets an incomplete harness certify
  itself and get frozen. Same for a missing `_BORE_FILLERS` entry.
- Do not "fix" `step_roundtrip` back to comparing `out_step` against a volume read from
  `out_step`. That is a tautology that can never fail; it must be a write→read cycle.
- A stand-in pipeline in a test/selftest must **re-export** the truth shape, never
  `shutil.copyfile` it — the `not_truth_copy` check hashes the output against
  `harness/truth/Mk.step` and will (correctly) reject a byte-identical copy.

## Log
(newest first — one block per iteration, format in MISSION.md §8)

### iter 8 — M0 — sonnet/medium — 2026-08-29T09:10
- Score before: `selftest.py` exit 1, 2 failures (M4/M5 generators unimplemented; M1/M2/M3 all
  green after this iteration's own run).
- Change: built `harness/generators.py::_make_m3` — outer cylinder R_o=1000 cut by a 6-point
  star bore (alternating tip R_tip=450 / valley R_valley=250 vertices at 12 points around the
  axis, straight edges, filleted per-vertex via `BRepFilletAPI_MakeFillet2d`: tip fillet=30,
  valley fillet=40), extruded the full length with the same margin-past-both-ends pattern as
  `_straight_bore`. New helper `_star_bore_cutter()` builds the profile with
  `BRepBuilderAPI_MakeVertex` once per point and reuses the same `TopoDS_Vertex` objects in both
  the polygon edges and the `AddFillet(vertex, radius)` calls — Fillet2d needs shared vertex
  identity to find the two adjacent edges at each corner. Registered `_make_bore_filled_m3` in
  `selftest.py::_BORE_FILLERS` (solid R_o cylinder, no star cut, mirrors `_make_bore_filled_m1`).
- Score after (local): `selftest.py` → all 4 M3 checks PASS (closed-form N/A — M3 has no
  `closed_form_volume`, so that check is skipped by design; truth STEP passes all 14 gates —
  n_faces=27 well under `face_count_max=100`; 1.01x-scaled copy fails volume_err_pct at 3.03%;
  bore-filled copy fails at 11.89%; gmsh meshes truth, min_quality=0.137 > gate 0.1). M4/M5
  still correctly FAIL on "generator implemented". `pytest tests/` → 16 passed (unchanged count
  — M3 has no dedicated generator test file yet, existing tests exercise it indirectly via
  `selftest.py`'s own test). Manual pre-check with a standalone script confirmed n_solids=1,
  brep_valid=True, volume=2.8077e10 mm³ (matches outer-cylinder-minus-star-prism-volume
  arithmetic exactly) before wiring into the real module.
- Learned: `BRepFilletAPI_MakeFillet2d(face)` + `AddFillet(TopoDS_Vertex, radius)` is the OCP
  2D fillet API — it operates on a planar face's own vertices/edges (not a 3D edge fillet), and
  `.Shape()` returns the filleted face as a `TopoDS_Shape` that needs `TopoDS.Face_s(...)` to
  cast back to `TopoDS_Face` before `BRepPrimAPI_MakePrism`. Confirmed live: `IsDone()` on both
  the wire builder and the fillet builder are the only checks needed — no `HasErrors()` follow-up
  (same OCP-7.9.3 pattern as `BRepAlgoAPI_Cut` noted in iter 1).
- Next: `_make_m4()` — finocyl per `milestones.py::_m4` params (circular bore R=300 fore of
  z=6000, 8 fin slots aft of z=6000 with w=80, radial 300→700, tip_r=40, flat fore wall). This
  is more involved than M2/M3: the bore cross-section changes shape at z=6000 (circle → circle+8
  slots), so the cutter is likely two boolean unions (straight cylinder for the fore segment,
  fore segment + 8 filleted slot prisms for the aft segment) fused or built as one prism per
  segment then unioned, cut from the outer cylinder. Register `_make_bore_filled_m4` in
  `selftest.py::_BORE_FILLERS` (solid cylinder, same pattern as M1/M3). Then M5 (M2 domes + M4
  fins combined) the same way — the review pass will re-run automatically once selftest goes
  green for all five milestones.

### iter 7 — M0 — sonnet/medium — 2026-08-29T08:51
- Score before: selftest exit 1, 4 failures (M2/M3/M4/M5 unimplemented aspects).
- Change: built `harness/generators.py::_make_m2` — revolves a meridian wire (fore ellipse arc
  + cylindrical wall + aft ellipse arc + on-axis edge) about Z via `BRepPrimAPI_MakeRevol` into
  a "capsule" solid (2:1 ellipsoidal domes, radial semi-axis R_o, axial semi-axis R_o/2,
  apexes on-axis at z=0/z=L), then cuts a straight `R_i` bore spanning the full length —
  matches docs/research/04 §"M2 + domed ends" exactly. Verified the ellipse winding/apex
  placement and the closed-form-free volume against a 400k-point numeric integration
  (27547756121.10 kernel vs .11 numeric — matches to 9 sig figs) before wiring it in.
  Registered `_make_bore_filled_m2` in `selftest.py::_BORE_FILLERS` as `Fuse(capsule, bore)`
  rather than the bare capsule — the bare capsule's meridian wire has an edge lying exactly on
  the rotation axis, which `BRepCheck_Analyzer` accepts in memory but a STEP write/read
  round-trip corrupts into an invalid shape, so the bare-capsule filler was failing on
  `brep_valid` instead of exercising `volume_err_pct`. Fusing routes it through OCC's boolean
  solver (same code path the real cut truth uses), which survives the round-trip.
- Score after (local): `selftest.py` — M2's 4 checks (truth passes all gates, scaled-copy
  fails on volume_err_pct, bore-filled fails on volume_err_pct, gmsh meshes truth) all PASS.
  Remaining 3 failures are only M3/M4/M5 generators, unchanged/expected.
  `score.py --milestone M1` still exits 1 at `pipeline_exit` (stub unchanged) — contract-valid.
- Learned: `gp_Ax2(origin, N, Vx)` ellipse winding for a quarter-arc apex-on-axis dome: with
  `Vx=(1,0,0)` (radial), `N=(0,1,0)` puts the arc's angle-90 end (the apex) at `z = center_z -
  minor_radius`; `N=(0,-1,0)` puts it at `z = center_z + minor_radius` — needed opposite N for
  fore (apex below center) vs aft (apex above center) domes. `BRepPrimAPI_MakeRevol` on a
  profile face that touches the rotation axis is valid in-memory but degenerate at the axis
  edge; don't trust `BRepCheck_Analyzer` on such a shape without also checking it survives a
  STEP round-trip, since that's what the scorer actually does.
- Next: `_make_m3` — 6-point star bore (R_valley=250, R_tip=450, tip fillet=30, valley
  fillet=40) cut through a straight R_o=1000 cylinder. Star profile: build a closed planar wire
  of alternating tip/valley points at radius R_tip/R_valley around 6-fold symmetry, fillet each
  vertex (`BRepFilletAPI_MakeFillet2d` on the wire's face, per-vertex radius), then
  `BRepPrimAPI_MakePrism` the filleted profile through L (or revolve-equivalent prism since no
  taper), cut from `BRepPrimAPI_MakeCylinder(R_o, L)`. Register `_make_bore_filled_m3` (solid
  cylinder, no star cut) in `_BORE_FILLERS`.

### iter 6 — M0 — opus/high — review-harness — 2026-08-29T08:35
- Score before: selftest exit 0, M1 scorer contract-valid, progress 0.5 — i.e. the driver was
  one green iteration away from tagging `harness-frozen`.
- Change: audited `harness/` as the last chance before it becomes immutable. Nine findings, all
  fixed. Ranked by how badly each would have misled the loop:
  1. **Freeze would have been fatal.** M2–M5 generators raise `NotImplementedError`, and
     `selftest.py` *skipped* them, so the harness was about to certify itself as trustworthy
     while able to score only M1. Post-freeze, `harness/` is restored from the tag every
     iteration, so M2 would have been unscoreable and unfixable — a permanent stall. Selftest
     now FAILS on an unimplemented generator (and on a missing `_BORE_FILLERS` entry), which
     bounces `m0_phase` back to `build` and buys the iterations needed to finish the harness.
  2. **`step_roundtrip` could never fail.** It compared `out_step` against `result_step_volume`,
     which was itself read from `out_step` — |ΔV|/V was identically 0. Now re-exports the
     read shape to a fresh STEP and reads *that* back, which genuinely exercises STEP fidelity.
  3. **`progress` denominator was the number of checks that had run**, not the number planned,
     so failing check 2 of 2 scored 0.50 and failing check 12 of 12 scored 0.92 — wildly
     nonlinear and not comparable across stages. Added `check_plan(spec)`; the denominator is
     now the full plan (M1: 14). The stub pipeline's score drops 0.5 → 0.0714, which is honest.
  4. **Partial credit was inverted for higher-is-better checks.** `threshold/value` saturates
     at the 1.0 clamp for gmsh SICN, so *failing* the last check scored the same as passing it
     — invisible to stall detection. `_partial()` is now direction-aware and capped at 0.999 so
     a failing check can never tie a passing one.
  5. **Skipped-check markers were missing** (MISSION §7 requires `pass:null,
     skipped:"prior failure"` for unreached checks). Now emitted for the whole plan.
  6. **The `bbox within 0.1 % of truth` universal gate (§6) was never implemented** — the
     designated tripwire for a 1000× unit error (§10.9). Added `bbox_err_pct` to all five
     milestones, comparing all 6 box faces relative to the truth extent (catches scaling *and*
     bulk translation).
  7. **Gaming — copying the answer.** cwd isolation cannot stop `rebuild.py` reading
     `harness/truth/` (it lives in the same repo). Added `not_truth_copy`, a sha256 compare
     against the truth STEP, plus a regression test that a copying pipeline is rejected.
  8. **Gaming — a tessellated shell as a "solid".** M2/M4/M5 had no `face_count_max`, so a
     sewn faceted mesh would pass `n_solids == 1`. Added deliberately generous caps
     (40/300/400 vs ~6/50/60 for exact geometry) — an anti-tessellation guard, not a style gate.
  9. Smaller: gmsh now has its own `mesh_timeout_s` (300 s) instead of borrowing
     `runtime_cap_s`, which for M5 is a 120 s *product* requirement that must not throttle the
     check; `score.json` is sanitized so a non-finite metric can never emit invalid JSON
     (`NaN` would break the driver's parse); `metrics.load_mesh` no longer silently returns a
     `Scene` whose `.is_volume` doesn't mean what the gates assume; `artifacts.pipeline_log`
     is now populated (was hardcoded null, violating the §7 example).
- Score after (local): `selftest.py` → **exit 1, by design** — all 6 M1 checks PASS (closed-form
  rel_err=0; truth STEP passes all 14 gates; 1.01× copy fails volume_err_pct at 3.03 %;
  bore-filled fails at 9.89 %; gmsh meshes truth, min_quality 0.234; determinism holds), and
  M2–M5 FAIL on "generator implemented". `score.py --milestone M1` → exit 1, contract-valid,
  progress 0.0714, 14 checks with 12 skipped markers. `pytest tests/` → 16 passed (was 13).
- Learned: determinism was only ever tested against the *stub* pipeline, which fails at
  `pipeline_exit` after two checks — it never exercised the seeded surface sampling or gmsh,
  the only two plausible sources of nondeterminism. Rerouted through `_score_with_step` so the
  full stack is compared; it does hold (identical dicts modulo `runtime_s`).
- Next: `_make_m2()` in `harness/generators.py` — 2:1 ellipsoidal domes on the outer surface at
  both ends, straight R_i=300 bore through, per `milestones.py::_m2` params (dome_semi_axial =
  R_o/2 = 500). Register `_make_bore_filled_m2` in `selftest.py::_BORE_FILLERS`. Then M3, M4,
  M5 the same way; the review pass will re-run automatically once selftest goes green.

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
