## MILESTONE M0 — build the harness (the fitness function for every later iteration)

There is no pipeline yet: `rebuild.py` is a stub that exits 3 with "not implemented". Your job
across the M0 iterations is to build `harness/` exactly per MISSION.md §7 so that the driver's
M0 gate passes:

1. `.venv/bin/python harness/selftest.py` exits 0, and
2. `.venv/bin/python harness/score.py --milestone M1 --out out/score.json` exits 0 or 1 (never
   2) and writes contract-valid JSON — with the stub pipeline that means a *failing* score whose
   `first_failure.check` is `pipeline_exit` and whose hint says the pipeline is not implemented.

Suggested build order (one focused step per iteration; several iterations are expected):
1. `harness/milestones.py` (all five specs from MISSION §6, with region labels and gates) and
   `harness/generators.py` for **M1 first** — verify the closed-form volume π(R_o²−R_i²)L.
2. `harness/metrics.py` (volume/CoM/inertia; symmetric deviation with per-z-bin regions;
   STEP round-trip) and `harness/meshcheck.py` (gmsh in a subprocess).
3. `harness/score.py` with the full contract (fail-fast checks, `progress`, hints, exit codes,
   run `rebuild.py` in a temp cwd on a neutrally named copy of the STL).
4. `harness/selftest.py`: closed-form checks; truth STEP scores as a pass; a 1 %-scaled copy
   fails on `volume_err_pct`; a bore-filled copy fails; determinism (score twice, diff).
5. Generators for M2, M3, M4, M5 (M2: 2:1 ellipsoidal domes on the outer surface with a
   straight bore; M3: 6-point star bore with fillets; M4: finocyl with 8 slots aft of z=6000 and
   a flat fore wall; M5: M2+M4) — record each truth volume in `harness/truth/Mk.json`.

Truth files are gitignored; generation must be deterministic and regenerate on demand. Keep the
harness independent of `pipeline/` (it may only call `rebuild.py` as a subprocess). Write
`tests/` for anything non-trivial. Read `docs/research/02-brep-loft-step-gmsh.md` §6 for the
gmsh snippet and §1/§5 for STEP I/O before writing `meshcheck.py`/`metrics.py`.
