# stl-rebuilder — project context

## Current status & next steps
- **2026-09-04 07:55 — ROUND 3 (GUI) STARTED.** Ladder G1→G2→G3→HANDOFFv3 (MISSION §6.3, §12
  rewritten): G1 `pipeline/engine.py` API extraction, G2 PySide6 app (`python -m app`, pyvistaqt
  3D viewport, offscreen smoke + screenshots), G3 polish with **Fable visual review** — the
  driver stops at "awaiting visual review"; reviewer inspects `out/gui/*.png`, writes feedback
  into PROGRESS.md `## Notes from Brady` or creates `state/G3_APPROVED`. **No packaging/.exe —
  run-from-source on mac + Windows is the deliverable** (Brady can't run installers at work);
  `WORK_SETUP.md` is the work-machine setup doc (written for the AI assistant there) and must
  stay accurate. GUI deps installed in `.venv` (PySide6 6.11.2, pyvista 0.48.4, vtk 9.6.2,
  pyqtgraph, qtawesome). Driver gates live in `loop.py::evaluate_gui`; G fragment
  `driver/prompt_gui.md`. Lesson re-learned at launch: **re-tag `infra-frozen` BEFORE starting
  the loop** — a stale tag makes preflight revert the new setup as "agent tampering" (cost two
  false starts, fixed at `2ae0227`).
- **2026-08-31 10:27 — ROUND 2 COMPLETE.** All 13 milestones + MR(skipped) + HANDOFF green;
  final regression sweep passed on the last commit; driver exited DONE at iteration 77.
  Round 2 ran 2026-08-30 04:29 → 08-31 10:27 (~30 h wall incl. Brady's token-limit pause),
  52 iterations, ≈$330 (project total $435). The engine now does: per-window ruled loft (M6),
  multi-loop matching with chain birth/death (M7), real feature-aware `--adaptive` stations
  (M8 — 10 iterations, the hardest fight), marching-cubes input repair with skew p95≈0.96
  (M9), `--axis auto`/inches/off-origin/1/40-scale frames (M10), multi-body STEP (M11),
  near-burnout dome breakthrough (M12), and the 5.3 M-triangle dirty capstone in 280 s (M13).
  `HANDOFF.md` (394 lines) has the full results table, SpaceClaim checklist, real-STL runbook,
  and three recorded engine gaps for Round 3 (e.g. `paths_used` never reports `loft`).
  Winning STEPs: `logs/M{1..13}-final-step.step`. The Opus harness review (iter 39) fixed a
  real gaming hole (station_bands endorsed cosine end-clustering) and left 9 documented
  non-blocking scorer weaknesses in PROGRESS.md `## Open review findings`.
  **Brady: `git push` (~85 commits pending; the driver never pushes).**
- **2026-08-29 20:41 — Round 1 done.** 25 iterations, ≈$103 API-equivalent, one day. All
  five milestones pass on commit `1b4ff91` (verified together by the driver's regression sweep):
  M1 annular cylinder, M2 ellipsoidal domes, M3 6-point star bore, M4 finocyl with a topology
  event at z=6000, M5 domes + fins with two events. Volume errors ≤ 0.02 %, deviation max
  ≤ 0.44 mm (gate 0.6), gmsh min SICN ≥ 0.187 (gate 0.1), 1–2 s per rebuild. `HANDOFF.md`
  has the results table (§2), the SpaceClaim checklist (§4), how to run a real burnback STL
  incl. choosing `--chord-tol` (§5), and honest limitations (§6). Winning STEPs:
  `logs/M{1..5}-final-step.step`.
- **Next (Brady):** (1) `git push`. (2) SpaceClaim checklist on the 13 STEPs (HANDOFF §4) —
  the Parasolid import is the one test the OCCT harness cannot see. (3) When work authorises
  it, drop a real burnback STL (+ optional `<name>.json` with units/axis/known volume) into
  `real_inputs/` and run MR per HANDOFF §5 — everything so far is proven only on synthesised
  meshes. (4) Decide Round 3 (PySide6 GUI, MISSION §12): extend MISSION with G1–G3, driver-level
  gates, re-tag `infra-frozen`, `./loop.sh`; also decide the Python 3.12 migration for wheels.
- **Round 1 gaps now closed in Round 2:** loft path, multi-loop stations, non-axisymmetric
  handling scope unchanged (outer must still be axisymmetric — a real-STL risk), `--adaptive`
  is real (M8/M10/M12/M13 gates force it), scale/units/axis handled.
- **Known CLI quirk:** the `claude` CLI froze mid-iteration 3× (silent, 0 sockets, after a
  completed step) — the driver's timeout catches it; killing just the `claude -p` pid banks the
  committed work early. Not a driver bug.
- Driver hardening done today (all on `infra-frozen`): save-then-stop (`--stop`/`--kill`/Ctrl-C),
  stream-json heartbeats + `--status` + `state/STATUS.md`, memory-guarded scoring (24 GB),
  per-mode timeouts 90/120/180 min, budgets $10/$15/$20, crash guard + auto-restart in
  `loop.sh`, review-pass stall reset, `PROGRESS_MIN_DELTA`, `--keep` artifacts, and the
  regression gate (a milestone pass requires all earlier milestones to still pass; otherwise
  demote — it caught M5's fix breaking M4). Window budget default $120; use
  `LOOP_WINDOW_BUDGET_USD` to raise it for a session. Costs: Sonnet iterations $2–9 (10–50
  min), Opus review/escalated $2–4.
- Incidents worth remembering: the M0 review pass's deviation metric OOM'd the machine (64 GB
  RAM + 64 GB swap) — fixed by the agent with a radius-bounded KD-tree query (75 s selftest);
  a `ROOT / None` crash on the first milestone pass silently stopped the loop for 4 h before the
  crash guard existed.

## What this is
An autonomous, self-correcting coding loop that builds `rebuild.py`: an STL (solid-rocket-motor
burnback surface mesh) → true BRep solid (STEP) converter, validated against synthetic ground
truth by a frozen scoring harness. Read `MISSION.md` for the full spec and `README.md` for how
the loop works and how to run it.

## If you are the looping agent
The driver puts everything you need in the prompt header, `PROMPT.md`, and `MISSION.md`.
Rules of engagement are MISSION.md §2 — frozen paths, one change per iteration, commit, and
update `PROGRESS.md`. Use `.venv/bin/python`. No network, no `git push`, nothing outside this repo.

## If you are Brady's assistant in an interactive session
Do not edit `harness/` after it is frozen (tag `harness-frozen`) without also re-tagging —
the driver restores it from the tag before every scoring run. Infra files are restored from
`infra-frozen`; to change `loop.py`/`PROMPT.md`/`MISSION.md`, edit, commit, then re-point
the `infra-frozen` tag (`-f`). The loop's state is `state/loop_state.json` (gitignored);
`state/current.json` says what the driver is doing right now; `state/STATUS.md` is the dashboard.
Stop a running loop with `python3 loop.py --stop` / `--kill`, never by killing processes by hand.
The project's Bash guard hook (`driver/guard_bash.py`) also applies to you here: any Bash command
whose text mentions a `~/.claude`/`~/.ssh`/`~/.config` path, or `git tag`/`git remote`, is
blocked — use the Read/Write tools for those files and a script file for the re-tag.

## Key paths
- `MISSION.md` — spec, milestones, gates, harness contract, failure modes
- `loop.py` — driver (config block at top; env overrides `LOOP_<NAME>`)
- `driver/prompt_*.md` — mode fragments; `driver/guard_bash.py` — PreToolUse hook
- `docs/research/*.md` — verified API research (trimesh, OCP/build123d, gmsh, claude CLI)
- `harness/` — scorer + generators (agent-built at M0, then frozen)
- `pipeline/` — the product (agent-owned)

## Access notes
No secrets involved. Runs on Brady's Claude subscription via the `claude` CLI (OAuth login);
the driver strips `ANTHROPIC_API_KEY` from the agent env unless `LOOP_USE_API_KEY=1`.
