## MODE: HANDOFF

Every milestone gate has passed. Write `HANDOFF.md` for a human engineer who will open the
results in SpaceClaim/Fluent. No code changes in this iteration.

Include, in this order:
1. **Summary** — three sentences: what the tool does, what was proven, what is not yet proven.
2. **Results table** — one row per milestone (M1–M5 in Round 1; M1–M13 and MR in Round 2, MR
   marked "skipped" if no real input was present): volume error %, deviation max / p99 (mm),
   gmsh min SICN, stations used, bodies, path fired per chain (revolve / prism / loft / custom),
   runtime (s). Pull the numbers from `logs/` and `out/` — do not estimate them.
3. **Artifacts** — for each milestone: path to the output STEP, the truth STEP, the truth STL
   input, the pipeline report JSON, and the exact `rebuild.py` command that produced it.
4. **SpaceClaim checklist** — import STEP → confirm exactly one solid body → confirm the bore /
   star / fins are present → run a mesh → compare volume shown in SpaceClaim to the table.
5. **How to run on a real burnback STL** — the recommended first command (uniform stations,
   `--chord-tol` matched to the STL's facet size — say how to estimate it), what to look at in
   the report JSON, and the three most likely failure modes with the CLI knob that addresses
   each.
6. **Known limitations** and **what to try next** (from PROGRESS.md `## Current state`).
7. *(Round 2)* The extra items in MISSION §9 "HANDOFF v2": per-body and inch/x-axis checklist
   entries, the real-STL runbook v2, and "what Round 3 (GUI) needs from the engine".
8. *(Round 3)* The extra items in MISSION §9 "HANDOFF v3": GUI launch instructions for
   Windows + macOS (verify `WORK_SETUP.md` §6 is accurate — fix it if not), the `--smoke`
   self-check, the screenshot inventory, the `pipeline.engine` API with a minimal scripted
   example, and the G1–G3 gate results.

Commit `HANDOFF.md`. Print its path as your final line.
