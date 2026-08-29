# PROGRESS — lab notebook of the loop (agent-maintained)

## Notes from Brady (human, driver side) — 2026-08-29 11:45
- The iteration-10/11 selftest deaths were **memory, not time**. Every M2 run since the review
  pass (`33e3257`) was SIGKILLed by macOS at ~11.5 min after the fine-tessellation step (RSS
  and 64 GB of swap exhausted). The driver's iter-11 run died at 686 s, well under its 1500 s cap.
  Iter 11's `CHORD_TOL/2` change reduces the face count but does not *bound* memory: bound it
  (cap the number of sample points, batch the proximity query, or use a KD-tree on vertices).
- The driver now runs selftest/scorer with a 24 GB memory cap and a 40 min wall-clock cap and
  puts the outcome (pass/fail counts, peak RSS, kill reason, output tail) in the next prompt
  header under `## Last driver evaluation` — read it before re-running anything.
- Please add `--milestone Mk` (and `--skip-gmsh`) options to `harness/selftest.py` so a targeted
  change can be verified in ~2 min instead of 11; the driver still runs the full selftest.
- The stall counter you saw (stall=5) was a driver artifact (best_progress pinned at 0.95 by the
  pre-review pass); it has been reset. Time budget per iteration is now in the header.

## Current state
- Milestone: M0. **The harness is ready to freeze.** Iter 12's full `selftest.py` run: exit 0,
  `SELFTEST PASSED`, **31/31 checks in 85.7 s** (vs the driver's 1500 s cap and its 24 GB memory
  cap; iter 11's driver run peaked at 0.77 GB). `score.py --milestone M1` → exit 1 with
  contract-valid JSON, 15 checks, 13 skipped, `first_failure.check == pipeline_exit`,
  `progress 0.0667`. `pytest tests/ --ignore=tests/test_selftest.py` → 15 passed. Both M0 gate
  conditions in MISSION §6 now hold on a *completed* run, which is what the freeze was waiting on.
- The runtime/memory crisis that consumed iterations 10 and 11 is over. Iter 11's rewrite of the
  deviation query (`metrics.point_mesh_distance`) was the structural fix; iter 12 **verified it is
  exact** (below), so the deflection no longer has to be traded against cost.
- ⚠️ **Dome regions carry a real tessellation-noise floor and M2/M5 have less deviation headroom
  than the global numbers suggest.** Measured on a geometrically *perfect* result (truth STEP
  scored against itself through the full stack): the cylinder bands sit at p99 ≈ 5e-8 mm, but
  `fore_dome`/`aft_dome` sit at **p99 ≈ 0.148 mm, max ≈ 0.267 mm** — 37 % of the 0.4 mm per-region
  p99 gate and 44 % of the 0.6 mm max gate, consumed before the pipeline does anything. A curved
  surface tessellated at `DEVIATION_DEFLECTION` has genuine chordal error and the two sides sample
  it differently. Budget for it when working M2/M5: the dome allowance is ~0.25 mm, not ~0.4 mm.
- **What iterations 10-11 cost, kept short so it is not repeated:** the driver's M0 gate is
  `loop.py::run_selftest` under `SCORE_TIMEOUT_S`, and a selftest that overruns it is killed
  mid-run, which the driver records as a stall with nothing in `out/score.json` to explain it.
  Five iterations were lost to that. The cause was `metrics.surface_deviation`: trimesh's
  `ProximityQuery.on_surface` sizes its search box by the nearest *vertex*, which on OCC's strip
  tessellations (M2 truth has triangles up to 9953 mm long) selects ~2240 candidate faces per
  point and materialises them all at once — tens of GB. Iter 11 replaced it with
  `metrics.point_mesh_distance`. When M0 stalls, read `logs/iter-NNNN.selftest.log` for the
  `total ...s` line before trusting the M1 verdict.
- **`metrics.point_mesh_distance` is verified exact** (iter 12, and now a permanent selftest
  check). It bounds the search radius with a cloud of points that lie *on* the target surface
  (vertices + 100 k samples) instead of vertices alone, then consumes candidates in chunks. Any
  on-surface point is an upper bound on the distance to the surface, so the box stays conservative
  and the answer stays exact while shrinking from ~2000 mm to ~13 mm. Checked against trimesh's
  reference on M1 (872 faces) and M2 (58 k faces) across four regimes — on-surface, near-surface,
  far-field (up to 2119 mm), and along the motor axis: **max |diff| 4.6e-13 mm, and it never
  under-reports**. Under-reporting was the dangerous failure mode: it would have made a wrong
  solid score as a good one, silently, with nothing else in the harness to notice.
- `DEVIATION_DEFLECTION = CHORD_TOL/2` (0.25 mm). Both sides of the comparison are re-tessellated
  at it; the pipeline's *input* STL still ships at CHORD_TOL as MISSION §6 requires.
- **`rebuild.py` must write a `--report` JSON.** The scorer always passes
  `--report <cwd>/report.json` and three gates are graded from it. Keys: `n_stations` (int),
  `stations_z_mm` (list[float], input-STL coordinates), `paths_used` (dict[str,str]),
  `topology_events_z_mm` (list[float]). Missing file or key = that gate fails. M1 has none of
  these gates, so M1 can ignore it; M2 onward cannot.
- Truth geometry now generated for all five milestones (all deterministic, all gitignored):
  M1 V=2.858849e10 · M2 V=2.754776e10 · M3 V=2.807700e10 · M4 V=2.758419e10 (36 faces) ·
  M5 V=2.666899e10 (44 faces). Every truth STEP passes all of its own gates and meshes in gmsh.
- **After the freeze, `harness/` is restored from the tag every iteration — it can no longer be
  edited.** If a later milestone reveals a harness bug, it must be raised to Brady, not patched.
- Unit tests: `tests/test_metrics.py` (6) + `test_meshcheck.py` (3) + `test_score.py` (6) +
  `test_selftest.py` (1) — 16 total, all pass. `tests/` is agent-owned and NOT frozen, so a check
  added to `harness/` needs its hardcoded counterpart in `test_score.py` updated too (the check
  plan is asserted there by name). Two commands is still the habit —
  `pytest tests/ --ignore=tests/test_selftest.py` (~8 s warm) plus a direct `harness/selftest.py`
  run — but the selftest wrapper is now 86 s, not 11 min, so one shot is also fine.
- Next milestone is **M1**: `rebuild.py` is still the exit-3 stub, so the first M1 iteration
  starts `pipeline/` from nothing. M1 truth is the annular cylinder — MISSION §5.2.6's
  "all circles centred on the axis → revolve an exact meridian" fast path should fire and give
  ~1e-9 volume error, so aim straight at that rather than at the general loft.
- **The M0 stall counter was an artifact and should now reset.** `loop.py::m0_build_progress`
  caps at 0.6 while `best_progress` was already 0.95, so no build iteration could ever beat it
  and every one was logged as a stall (3 by iter 9, halt is at `STALL_MAX=12`). Advancing to M1
  resets it; if the driver still reports stalls on M1, that is a *real* signal.

## Do not retry
- Do not try to provoke `surface_deviation_p99_by_region` with a perturbed *shape*. To survive the
  global p99 the bad band must hold under 1 % of the pooled sample points, and no region band of
  M1–M5 is that small (the domes are ~20 % of ~200 k points). Any shape error big enough to move a
  region's p99 either trips `surface_deviation_max_mm` first or moves the global p99 too. The
  selftest proves the gate is wired by injecting a `by_z_bin` table instead — that is deliberate,
  not a shortcut.
- Do not assume `metrics.point_mesh_distance` needs re-verifying or replacing. Iteration 12
  measured it against trimesh's reference across four regimes on two meshes: max |diff| 4.6e-13 mm
  with zero under-reporting. The selftest now re-proves it in 1.5 s on every run. It is exact,
  it is fast, and it is frozen.
- `BRepAlgoAPI_Cut.HasErrors()` does not exist in OCP 7.9.3. Use `IsDone()` only.
- Do not make `selftest.py` skip (rather than fail) an unimplemented milestone generator. It
  reads like a convenience, but it is the exact hole that lets an incomplete harness certify
  itself and get frozen. Same for a missing `_BORE_FILLERS` entry.
- Do not "fix" `step_roundtrip` back to comparing `out_step` against a volume read from
  `out_step`. That is a tautology that can never fail; it must be a write→read cycle.
- A stand-in pipeline in a test/selftest must **re-export** the truth shape, never
  `shutil.copyfile` it — the `not_truth_copy` check hashes the output against
  `harness/truth/Mk.step` and will (correctly) reject a byte-identical copy.
- Do not run M5's fin slots to z=L the way M4's do. The aft dome's radius falls below
  `fin_r_outer`=700 at z≈9857, so full-length fins punch open slots straight through the dome
  wall — not a finocyl, and it contradicts `milestones._m5`'s `fin_zone` band, which ends at
  `1 - dome_h/L`. M5's fins stop at the aft dome shoulder z=L-dome_h=9500 with a flat aft wall.
- Do not measure bounding boxes with `BRepBndLib.Add_s`. It boxes B-splines by their control
  poles and overshoots a 20-station R=1000 fit by ~16 mm — eight times the `bbox_err_pct` budget
  — so it fails correct geometry. `AddOptimal_s(shape, box, False, False)` only.
- Do not replace `metrics.uniform_stations_needed`'s geometric bisection with one that re-runs
  the pipeline at successive station counts. It reads like the more honest baseline, but it adds
  minutes to every score and a timeout failure mode that, once `harness/` is frozen, has no legal
  fix from inside the loop.
- Do not lower `DEVIATION_DEFLECTION` below `CHORD_TOL/2` chasing a cleaner metric floor. This
  entry previously said `/5`; `/5` was measured on **M1 only** and is wrong for the milestones
  that matter — M2 has 154 k faces at 0.1 mm and one deviation call there can eat the whole
  1500 s `SCORE_TIMEOUT_S`, which is exactly what stalled M0 for five iterations. Deviation cost
  scales with mesh face count (≈ 1/deflection²), so always benchmark a *dome* milestone (M2/M5),
  never M1, before touching this constant.
- Do not benchmark harness cost on M1 and extrapolate. M1 is an annular cylinder: 872 faces at
  CHORD_TOL, versus 29 286 for M2. Any per-mesh cost measured on M1 understates M2–M5 by 30x+.
- Do not read an exit-137 / SIGKILL of `selftest.py` as a flaky environment or an OOM. It is the
  1500 s budget. Check the new `total …s` line at the end of the selftest output first.
- Do not measure the `uniform_stations_needed` baseline as radial error |Δr(z)|. A dome apex has
  a vertical tangent in r(z), so radial error diverges there while true surface deviation stays
  small; the bisection pins to `max_n` and the adaptive_efficiency gate silently becomes
  unfireable. Perpendicular distance in the (z, r) plane only.
- Known residual gaming hole, accepted: `_truth_hidden()` stops a pipeline from *reading* the
  truth STEP, but a pipeline could still `import harness.generators` and regenerate it. Closing
  that would mean sandboxing imports, which is out of proportion to the risk — the pipeline is
  written by this same loop, and PROGRESS/commit review is the backstop. If a milestone ever
  passes suspiciously early, check `pipeline/` for a `generators` import first.

## Log
(newest first — one block per iteration, format in MISSION.md §8)

### iter 12 — M0 — opus/high — review-harness — 2026-08-29T12:13
- Score before: driver's iter-11 evaluation PASSED — 27/27 selftest checks in 1 min, peak RSS
  0.77 GB. So this was the pre-freeze audit proper, not a repair: `harness/` becomes the immutable
  fitness function for every remaining iteration the moment this run is accepted.
- Iteration 10 already audited the harness once. The thing it could not have audited is the code
  written *after* it: iteration 11 replaced `trimesh.proximity.ProximityQuery.on_surface` — a
  reference implementation used by thousands of projects — with ~40 lines of hand-written
  candidate-culling and chunked point-triangle distance, and nothing anywhere proved the
  replacement returns the same numbers. **Every deviation gate in the scorer rests on that
  function, and its dangerous failure mode is silent:** a search radius that is even slightly too
  small under-reports the distance, so a wrong solid scores as a good one and the loop optimises
  toward nothing. That was the first thing I checked.
  - The argument that it is exact: the search radius is the distance to the nearest point of a
    cloud that lies *on* the target surface, so it is an upper bound on the true distance; any
    triangle within that distance must have a bounding box meeting the query cube; so the closest
    triangle is always in the candidate set. Sound — but worth measuring, because the chunked
    `np.minimum.reduceat` reduction is easy to get subtly wrong.
  - Measured against trimesh on M1 (872 faces) and M2 fine (58 472 faces), four regimes:
    on-surface, near-surface (σ=0.3 mm), far-field (uniform over the padded bbox, distances to
    2119 mm) and along the motor axis. **max |diff| = 4.6e-13 mm; minimum signed diff = -4.6e-13,
    i.e. it never under-reports.** Clean.
  - Made it permanent: `selftest.check_distance_kernel` runs that comparison on M1 every run
    (1.5 s). The freeze is only as good as the evidence behind it, and there was none for this.
- **Change (the one gate fix): MISSION §6's M2 row says "the deviation gate must hold per z-bin
  including dome bins" and no such check existed.** `by_z_bin` was computed and reported but only
  the *global* max/p99 were gated. The global p99 is dominated by the cylinder, which carries
  ~90 % of the surface area, so an error concentrated in a band can sit below the global 99th
  percentile and pass. Added `surface_deviation_p99_by_region`: the same p99 threshold applied
  inside every labelled region with >= `_MIN_BIN_POINTS` (200) pooled points, placed right after
  the global p99 in the ladder. It is enabled by the same gate key, so it covers M1/M2/M5 — the
  milestones §6 gives a p99 to — and leaves M3/M4 (max-only) alone. The global max already implies
  the per-region max, so only p99 needed its own check. Also attached the region label to the
  `surface_deviation_max_mm` failure location, which MISSION §7's example JSON shows and the code
  was omitting.
- Proving the new gate bites: it cannot be provoked with a perturbed shape (see `## Do not retry`
  — the bad band would have to be under 1 % of the pooled points and no region is that small), so
  the selftest injects a `by_z_bin` table with one region over the gate while the global max and
  p99 stay clean, and asserts `first_failure.check == "surface_deviation_p99_by_region"`. Fires on
  M1, M2 and M5.
- **Finding worth carrying into M2/M5, from actually reading the new per-region numbers:** on a
  geometrically *perfect* result the dome bands sit at p99 ≈ 0.148 mm / max ≈ 0.267 mm while the
  cylinder bands sit at ~5e-8 mm. That is pure tessellation noise on a curved surface, and it eats
  37 % of the per-region p99 budget and 44 % of the max budget before the pipeline does anything.
  The real dome allowance is ~0.25 mm, not ~0.4 mm. Recorded in `## Current state`.
- Also audited and found correct, so nothing was changed: cwd isolation + `_truth_hidden()` +
  the byte hash (a pipeline cannot read, copy or re-export the answer); `n_solids` + `face_count_max`
  together reject a sewn tessellated shell posing as a BRep; `bbox_err_pct` via `AddOptimal_s`
  catches unit and scale errors; `step_roundtrip` is a genuine write→read cycle; NaN propagates to
  a *failed* comparison everywhere and `_sanitize` keeps the JSON valid; `progress` is deterministic
  and its `_partial` term is direction-aware; the fail-fast plan denominator is the full plan.
- Score after (local): `selftest.py` exit 0, **`SELFTEST PASSED`, 31/31 checks, total 85.7 s**
  (was 27/27 in ~72 s; +4 checks, +14 s). `score.py --milestone M1` exit 1, contract-valid,
  15 checks / 13 skipped, `first_failure.check == pipeline_exit`, `progress 0.0667` (was 0.0714 —
  the denominator grew by one check, which is the intended arithmetic).
  `pytest tests/ --ignore=tests/test_selftest.py` → 15 passed after updating the hardcoded check
  plan in `test_score.py` (`tests/` is not frozen).
- Next: freeze `harness/` and start M1. `rebuild.py` is still the exit-3 stub, so the first M1
  iteration builds `pipeline/` from nothing; go straight at MISSION §5.2.6's "all circles centred
  on the axis → revolve an exact meridian" fast path rather than the general loft.

### iter 11 — M0 — opus/high — escalated — 2026-08-29T10:54
- Score before: driver verdict `M1 progress=0.0714, first failure pipeline_exit` — but that is
  the *M1* scorer, which is not the M0 gate and has been unchanged since iter 4. The real signal
  was `stall=5, best_progress=0.95` with `m0_phase` stuck at `build`.
- Escalated mode says form a *different* hypothesis before touching code, so I did not try to
  finish or re-verify the harness. I asked instead why five consecutive iterations were logged
  as stalls when iter 9 had already seen `selftest.py` exit 0 with 22/22 checks.
- **Diagnosis (the different hypothesis, and it is the right one): the M0 gate is failing on
  TIME, not on any assertion.** `loop.py::evaluate` runs `run_selftest()` under
  `SCORE_TIMEOUT_S = 1500 s`; on timeout it returns False, resets `m0_phase` `review → build`,
  and calls `update_stall(m0_build_progress()=0.6)`, which can never beat `best_progress=0.95`.
  So every iteration since iter 10 has been recorded as a stall for a reason nothing in
  `out/score.json` can show. Three pieces of evidence, all cheap:
  1. `logs/iter-0010.selftest.log` ends mid-M2 with no PASS/FAIL line and no summary — a
     truncated tail of a killed run, not a completed failing run.
  2. `git show-ref` shows `infra-frozen` but **no `harness-frozen`** — the freeze never fired.
  3. Iter 10's own note that two shell runs were "SIGKILLed by the environment (exit 137) at
     ~11.5 min" is the same overrun seen from inside; it was misattributed to the environment.
- Change: `DEVIATION_DEFLECTION` `CHORD_TOL/5` → `CHORD_TOL/2` in `harness/score.py`, plus
  per-check + total elapsed-time reporting in `harness/selftest.py`.
- Measured before changing anything (`_mesh_from_step` at three deflections, and a full score
  via `selftest._score_with_step`):
  - M1: 872 / 1 232 / 1 952 faces at 0.5 / 0.25 / 0.1 mm. Full M1 score at 0.5 mm = **76.0 s**.
  - M2: 29 286 / 58 472 / 153 636 faces at the same deflections. A *single* M2 deviation call at
    0.1 mm did not complete in 8 min of direct probing.
  Iter 10 justified `/5` with "30 s at 0.5 mm, 77 s at 0.1 mm" — both M1 numbers. Deviation cost
  tracks face count because `ProximityQuery.on_surface` does an r-tree lookup per query point,
  and face count grows as 1/deflection², so M2–M5 are 30x+ worse than the figure that was used.
- Why this specific change is safe: in the selftest the stand-in pipeline *re-exports the truth
  shape*, so both sides of the comparison tessellate identically — measured `dev_max = 3.5e-07`
  at 0.5 mm. Deflection therefore cannot move a selftest verdict at all; it only sets the noise
  floor a *real* pipeline output is judged against (~0.125 mm at /2 = 31 % of M1's 0.4 mm p99
  budget, vs 62 % at CHORD_TOL and 12 % at /5). Cost/fidelity only, no gate weakened.
- Score after (my local run): see the `total …s` line the new instrumentation prints. The target
  is exit 0 **and** a total with real margin under 1500 s.
- Learned: the driver's M0 gate has a failure mode that is invisible in `out/score.json` — the
  file the prompt calls "the single most important line in the repo". When M0 stalls, read
  `logs/iter-NNNN.selftest.log` and check for a summary line before trusting the M1 verdict.
- Next: if the total is still near 1500 s, do **not** tweak the deflection again. Ranked options,
  cheapest first: (a) `check_determinism` currently runs 2 more *full* M1 scores on top of the
  M1 truth score — reuse the truth score as `r1` and run only one more (~76 s); (b) drop
  `metrics.uniform_stations_needed`'s `n_bins` 8192 → 4096 (invariant only needs it above
  `max_n`=2048), which cuts the M2/M5 baseline bisection; (c) the real structural fix — replace
  `ProximityQuery.on_surface` with a vectorized cKDTree candidate shortlist + point-triangle
  distance, validated to ~1e-9 against trimesh on M1 and M2. (c) is worth doing properly because
  it removes the timeout risk permanently rather than trading fidelity for it.

### iter 10 — M0 — opus/high — review-harness — 2026-08-29T10:2x
- Score before: `selftest.py` exit 0 (22 checks). Nothing was broken; this was the pre-freeze
  audit of `harness/` as the immutable fitness function, per MISSION §2.
- Findings and fixes, in the order of the review checklist:
  1. **Truth correctness — `_bbox()` was measuring the wrong thing.** `BRepBndLib.Add_s` boxes a
     B-spline by its *control poles*; for a periodic 20-station fit through points on R=1000 the
     poles sit ~16 mm outside the surface, so a geometrically perfect result would have failed
     the 0.1 % `bbox_err_pct` gate (2 mm). Switched to `AddOptimal_s(..., useTriangulation=False)`,
     which evaluates real geometry and does not depend on an attached triangulation.
  2. **Gate fidelity — three §6 gates were declared but never enforced, and M4 was gameable.**
     - `dome_stations_min`, `topo_event_z`, `adaptive_efficiency` had no check blocks at all.
       They grade *how* the pipeline worked, which is only knowable from the pipeline's own
       account, so the optional `--report` of MISSION §5.3 is now **always** passed and its four
       keys (`n_stations`, `stations_z_mm`, `paths_used`, `topology_events_z_mm`) are frozen
       contract. A missing report or key is a check *failure* with a hint, never a crash.
     - `adaptive_efficiency` needs a "uniform stations needed" denominator. It is computed from
       the *truth geometry* (`metrics.uniform_stations_needed`, bisection on the silhouette
       profile r(z), milliseconds) rather than by re-running the pipeline at many station counts.
     - M5's gate row says "all M2 and M4 gates" but `dome_stations_min` was missing from it.
     - M4 listed no deviation gate, so a fin-less annular cylinder with a fudged radius passed
       M4 outright. Added `surface_deviation_max_mm = 2*CHORD_TOL` as an anti-gaming floor —
       deliberately looser than M5's 1.2*chord_tol on the same fins; a missing slot is ~400 mm off.
  3. **Gaming resistance — cwd isolation was not enough.** The scorer already ran `rebuild.py`
     in a temp cwd on a neutrally-named STL and byte-hashed the output against the truth, but
     nothing stopped a pipeline from reading `harness/truth/Mk.step` by absolute path and
     *re-exporting* it, which defeats a byte hash. `_truth_hidden()` now renames `harness/truth/`
     out of the way for the duration of the pipeline subprocess and restores it after, discarding
     anything the pipeline left at that path.
  4. **Metric fidelity — 62 % of M1's deviation budget was tessellation noise.** The deviation
     compared two 0.5 mm tessellations; measured, that alone is max 0.2495 / p99 0.2440 mm
     against an M1 p99 gate of 0.4 mm. Both sides are now re-tessellated at
     `DEVIATION_DEFLECTION = CHORD_TOL/5` (0.1 mm, ~0.05 mm noise) for the comparison only; the
     pipeline's *input* STL still ships at CHORD_TOL as §6 requires. /5 and not /10 because
     deviation is the scorer's dominant cost (M1: 30 s at 0.5 mm, 77 s at 0.1 mm, superlinear
     below) and `selftest.py` runs under the driver's 1500 s `SCORE_TIMEOUT_S`.
  5. **Contract — check ordering.** The three report checks are a JSON parse plus arithmetic, so
     they were placed *before* the 100k-sample deviation in the cheap→expensive ladder. This also
     keeps the new selftest perturbations fast, since they fail before deviation ever runs.
- **Follow-up found by actually running the new gate (this is why (5) mattered):** the first
  `uniform_stations_needed` measured *radial* error |Δr(z)|. At a dome apex the meridian has a
  vertical tangent, so |Δr| diverges where the true surface deviation is small — the bisection
  ran into its own `max_n` cap and handed `adaptive_efficiency` a budget of 2048 stations, i.e. a
  gate that could never fire. Now measures **perpendicular** distance from the truth meridian to
  the station polyline in the (z, r) plane (`_polyline_max_dist`), with the reference profile
  (8192 bins) kept finer than the densest candidate station grid (`max_n` 2048) so err(n) is not
  measured against a curve coarser than the grid under test. M1 → 3 stations, M2/M5 → 2048
  (still the cap: resolving a 90° meridian turn to 0.4 mm with *uniform* spacing is genuinely
  expensive, which is exactly the asymmetry the gate is meant to reward). Budget 1024 vs. the
  ~10² an adaptive pass should need, so it still discriminates by an order of magnitude. 1.1 s,
  cached per milestone.
- Verification: `selftest.py` grew a perturbation block that mutates the report of an otherwise
  perfect submission three ways (truncated dome stations / no topology event / 100 000 stations)
  and asserts each fails on *its own* check — a gate that is never exercised is a gate that does
  not exist. `pytest tests/ --ignore=tests/test_selftest.py` → 15 passed.
- Not done, deliberately: nothing in `pipeline/` was touched.

### iter 9 — M0 — opus/high — escalated — 2026-08-29T09:30
- Score before: `selftest.py` exit 1, 2 failures (M4 and M5 generators unimplemented). The
  driver logged a 3rd consecutive stall and escalated to Opus, but per the note above that
  stall is the `m0_build_progress` artifact, not a regression — so I did not form a new
  hypothesis about a failure; I just finished the harness.
- Change: built the last two generators, `_make_m4` and `_make_m5`, plus their `_BORE_FILLERS`
  entries. Deliberately **two** generators in one iteration rather than one: they share all of
  their machinery, and closing out M0 in a single iteration removes the whole stall-budget risk
  the previous block flagged. New shared helpers in `generators.py`:
  - `_fin_slot_prism()` — one fin slot as a prism of a planar profile: two straight flanks at
    ±`fin_w`/2, a straight inner end, and an outer cap that is an exact **semicircle** (the
    milestone gives `fin_tip_r`=40 = `fin_w`/2=40, so a corner fillet and a semicircular cap are
    the same shape; the function asserts that equality rather than assuming it). Built with
    `GC_MakeArcOfCircle(p_c, p_m, p_d)` — a 3-point arc, so no `gp_Circ`/parameter-range algebra.
    The inner end is pulled to `fin_r_inner - 50` = 250, i.e. *inside* the R=300 bore, so fusing
    the fin onto the bore is transversal instead of tangent (MISSION §10.6). That extra material
    lies where the bore already removes everything, so the resulting solid is unchanged.
  - `_finocyl_cutter()` — full-length `_straight_bore` fused with `n_fins` rotated copies, and a
    guard that the pulled-in inner ends cannot overlap each other (`hw < r_start·sin(π/n)`).
  - `_finish()` — the volume/area/bbox + STEP + STL + `Truth` tail that `_make_m1/2/3` all
    repeat inline; used by the two new makers only (the existing three left untouched).
  M4 = `Cut(cylinder R_o, cutter)` with fins run to z=L+10 so the aft face is a clean planar cut
  rather than a tangency. M5 = `Cut(_capsule_outer_shape(...), cutter)` with fins stopping at the
  aft dome shoulder z=9500 — see the new `## Do not retry` entry for why not z=L.
- Score after (local): **`selftest.py` → exit 0, `SELFTEST PASSED`, 22/22 checks** (M1–M5: truth
  STEP passes every gate; 1.01× copy fails `volume_err_pct` at 3.0301 %; bore-filled copy fails
  at 9.9/10.3/11.9/13.9/13.9 %; gmsh meshes every truth, min SICN 0.137–0.287 vs gate 0.1; M1
  closed-form rel_err=0; determinism holds). `score.py --milestone M1` → exit 1, contract-valid,
  `progress 0.0714`, `first_failure.check == pipeline_exit`. `pytest tests/
  --ignore=tests/test_selftest.py` → 15 passed. **Both M0 gate conditions now hold.**
- Verified before wiring in, via a throwaway probe script: M4 is 1 solid, 36 faces (cap 300),
  `BRepCheck_Analyzer` valid, V=2.758419e10 mm³ — cross-checked against an independent 2D
  numeric integration of the fin cross-section outside the bore circle (A_fin=31391.5 mm²,
  V = πR_o²L − πR_bore²L − 8·A_fin·4000 = 2.758396e10, rel err 8e-6, which is the integration
  grid's own discretization error). M5 V=2.666899e10 = M2's 2.754776e10 − 8·A_fin·3500 exactly.
  M5's bbox is z∈[23.03, 9976.97], not [0, 10000]: the R=300 bore punches through both dome
  apexes, so the true extremes are where the dome radius equals 300 — same as M2, and
  self-consistent because `bbox_err_pct` compares against this same truth.
- Learned: the fin cross-section area outside the bore is *not* the naive
  `(r_outer−r_inner)·w + πr_tip²/2` = 31313 mm². The bore is a circle, not the line r=300, so
  the flanks meet it at x=√(300²−40²)=297.32 and ~78 mm² of extra material survives. Any future
  closed-form volume for M4/M5 has to integrate that lens, which is exactly why
  `milestones._m4/_m5` leave `closed_form_volume=None` — the generator's `BRepGProp` value is
  the truth, and the numeric integration above is the independent check on it.
- Next: **M1.** The harness freezes after this iteration, so build `pipeline/`. `rebuild.py` is
  still the exit-3 stub; first target is `pipeline_exit`, i.e. get *any* valid single-solid STEP
  out of the CLI contract in MISSION §5.3. For M1 specifically, drive at §5.2.6's revolve fast
  path (all section loops are axis-centred circles → RDP the (R,z) polyline and
  `BRepPrimAPI_MakeRevol` an exact meridian); the `face_count_max=8` gate rules out a loft-based
  answer anyway. Do not attempt the general loft ladder until M2.

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
