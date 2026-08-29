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
