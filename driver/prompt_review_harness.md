## MODE: REVIEW-HARNESS (runs once, before harness/ is frozen)

`harness/selftest.py` passes and `harness/score.py` produces contract-valid JSON. Your job is
to audit `harness/` as a skeptical reviewer BEFORE it becomes the immutable fitness function
for every later iteration. A wrong scorer silently misleads the whole loop; a gameable scorer
lets a wrong pipeline "pass".

Check, and fix what you find:
1. **Correctness of truth**: each milestone's analytic solid matches its spec in MISSION.md §6
   (dimensions, dome shape, star parameters, fin slot placement at z=6000 with a flat fore
   wall). Closed-form volume checks where available.
2. **Gate fidelity**: thresholds match §6 exactly; the deviation metric is symmetric and
   includes vertices; the per-z-bin table uses the region labels; STEP round-trip re-reads the
   file the pipeline wrote (not an in-memory shape); gmsh runs in a subprocess.
3. **Gaming resistance**: could a pipeline pass by emitting the truth STEP, by scaling, by
   returning a mesh instead of a BRep, by writing to the wrong path, by reading
   `harness/truth/*.step` directly? Make the scorer copy the input STL to a temp dir with a
   neutral name and run `rebuild.py` with cwd = that temp dir so the pipeline cannot see
   `harness/truth/`; verify the output is a STEP that gmsh recognizes as a solid.
4. **Contract compliance**: exit codes 0/1/2, `progress` deterministic and monotone, every
   failing check has a `hint` with location, `skipped` markers after the first failure,
   exceptions reported not raised.
5. **Determinism**: fixed seeds; run the scorer twice on the truth STEP and diff the JSON.

Run `.venv/bin/python harness/selftest.py` after your fixes. Log what you changed and why in
PROGRESS.md. Commit. Do not touch `pipeline/`.

### Round 2 review (when `harness-frozen` already exists and M6+ specs are present)
Audit the extension with the same scepticism, specifically:
6. **M1–M5 unchanged**: `score.py --milestone M1..M5` on the current pipeline still pass with
   the same numbers as `logs/M*-final-report.json`; the M1 check plan is byte-identical.
7. **Gate numbers match MISSION §6.2** for every new milestone; truths match their closed forms
   (M6 frustum, M7/M11 exact, M8/M12 offset property = w); voxel inputs (M9, M13) are genuinely
   pathological (triangle count, equiangle-skew statistics, unwelded/flipped/islands as
   specified) and the deviation gates are in voxel units.
8. **Feature-aware station gate**: prove that Round 1's cosine end-clustering FAILS
   `station_bands` under `n_stations_max` (the selftest counter-example), and that `n_stations`
   cannot be self-reported (it must equal `len(stations_z_mm)`, all inside the extent).
9. **Frame handling**: the scorer maps the result back into the truth frame; a wrong undo-
   transform or wrong units fails `bbox_err_pct`; `stations_z_mm` semantics are consistent for
   an x-axis input.
10. **Multi-body**: `per_solid_volume_err_pct` cannot be satisfied by one fused solid or by
    duplicating a body; islands must be dropped, not exported.
11. **MR**: the skip path is reachable only when `real_inputs/` has no STL; the pipeline's temp
    cwd cannot influence it.
12. **Report-on-failure** is exercised by selftest and the report keys of §7.2 are required.
13. **Selftest time** under 8 minutes with warm caches; voxel generation is cached and never
    runs inside a scorer's timed section.
