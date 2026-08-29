# PROGRESS — lab notebook of the loop (agent-maintained)

## Current state
- Milestone: M0 — harness under construction. `harness/milestones.py` (all 5 specs),
  `harness/generators.py` (M1 only), and `harness/metrics.py` (volume/CoM/inertia, symmetric
  surface deviation with by_z_bin, STEP round-trip) are done and verified against M1 truth.
- Truth files written: `harness/truth/M1.step`, `harness/truth/M1.stl`.
- Unit tests: `tests/test_metrics.py` (6 tests, all pass) — self-comparison ~0, 1%-scaled mesh
  correctly flagged (~100mm deviation, ~3% volume err), non-volume mesh raises ValueError,
  STEP round-trip matches closed-form to <1e-9 relative error.
- Still needed: `harness/meshcheck.py`, `harness/score.py`, `harness/selftest.py`, and M2–M5
  generators.

## Do not retry
- `BRepAlgoAPI_Cut.HasErrors()` does not exist in OCP 7.9.3. Use `IsDone()` only.

## Log
(newest first — one block per iteration, format in MISSION.md §8)

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
