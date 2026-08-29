# PROGRESS — lab notebook of the loop (agent-maintained)

## Current state
- Milestone: M0 — harness under construction. `harness/milestones.py` (all 5 specs) and
  `harness/generators.py` (M1 only) are done and verified. M1 volume error = 0 % (exact).
- Truth files written: `harness/truth/M1.step`, `harness/truth/M1.stl`.
- Still needed: `harness/metrics.py`, `harness/meshcheck.py`, `harness/score.py`,
  `harness/selftest.py`, and M2–M5 generators.

## Do not retry
- `BRepAlgoAPI_Cut.HasErrors()` does not exist in OCP 7.9.3. Use `IsDone()` only.

## Log
(newest first — one block per iteration, format in MISSION.md §8)

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
