# stl-rebuilder — project context

## Current status & next steps
- **2026-09-07 — Fable design review + 3-phase GUI polish, complete.** Brady asked for an
  honest design evaluation ("make it look fantastic... enterprise like grade... NASA rocket
  theme") plus fresh ideas for the 3D viewport. Fable ran the app offscreen, took ~15 real
  screenshots (M2 + a trickier M8 finocyl/star-bore run), found the bones (dark shell,
  verification checklist, error presentation, the dial-cluster concept) already solid but
  flagged two real bugs — dial captions clipped by the widget's own bottom edge, and 40-60+
  full-opacity station rings completely wallpapering the solid on real geometry — plus a phased
  polish/capability plan. Implemented all 3 phases directly (not deferred):
  - **Phase 1** (finishing): both bugs fixed; monospace numerals everywhere data lives; toolbar
    text+shortcuts (F5/Ctrl+R/Esc/Ctrl+E/F/O/S/W) + a Run menu; window title tracks the loaded
    file; version string (status bar + About); path elision; viewport image export; Fit/Ortho
    camera actions; a real empty state; outline badges ("Stations (60)", "Output ✓").
  - **Phase 2** (mission-control identity): new `TELEMETRY` cyan color token so "the machine is
    working" no longer looks identical to "you clicked a button" (dials/spinner/progress bar);
    a `T+ 00:00:00` mission clock + NOMINAL/RUNNING/FAULT/ABORTED status strip; dial bezel
    depth; rebuilt-solid render color switched to machined-steel grey (Brady's call, replacing a
    saturated blue that competed with the UI's own accent colors).
  - **Phase 3** (viewport as a verification instrument — Brady's "more stuff for geometric
    viewing" ask): Stations table ↔ 3D ring ↔ new pyqtgraph radius-profile chart are now one
    linked selection; a draggable section-view slider clips the solid+input mesh along the
    motor axis (finally see the bore interior instead of squinting through transparency); a
    deviation heatmap colors the solid by actual per-point distance to the input mesh (scipy
    cKDTree) against the run's own tolerance; render-mode cycle (shaded/edges/wireframe); a
    camera-orientation widget alongside the existing precise axis-snap buttons.
  - Verified against real rendered screenshots at every stage, not code reading alone — M2
    (axis=Z) confirmed unaffected, M8 confirmed the ring/steel/highlight/section/heatmap
    features all work together. 20 new regression tests; full suite 137 passed. Committed at
    `59aa943` (plus `e0af0d4` from the same session fixing the giant/misplaced station rings
    when the detected motor axis isn't Z — see below). **Brady: `git push`** — 3 commits ahead
    of `origin/main` as of this entry.
  - **Known limitation, not yet fixed**: the deviation-heatmap color scale can read as
    uniformly mid-tone rather than sharply highlighting hotspots — it uses nearest-VERTEX
    distance between the two differently-tessellated meshes (input STL vs. OCCT preview STL),
    which is systematically coarser than the engine's own point-to-triangle verification metric
    (`_compute_verification`'s approx deviation). Real, working, just not the tightest possible
    signal; a future pass could swap in a point-to-triangle distance (e.g. `vtkImplicitPolyDataDistance`) if it turns out to matter in practice.
- **2026-09-06 — Interactive GUI hardening from Brady's live testing, plan complete.** Ran the
  GUI by hand against real-scale synthetic STLs (M8, M13) and fixed everything that surfaced,
  closing out the plan at `~/.claude/plans/serialized-percolating-bachman.md` end to end:
  - **Dashboard**: added a 5th ANALYZE dial (LOAD/SCAN/SECTIONING/BUILD already existed) wired to
    a real progress signal threaded through `engine.analyze()`/`pipeline/io.py::load_and_orient`
    (previously silent — Analyze on a big mesh looked hung). Needle "creeps" forward
    asymptotically between sparse real checkpoints (an absolute 0.93 ceiling, 80s time constant,
    never-move-backward invariant), calibrated against ACTUAL M13 timing data (`_weld_by_radius`
    alone burns ~160 of ~182s with only one checkpoint) — a first design based on theoretical
    checkpoint spacing was proven wrong by replaying real timestamps and would have saturated
    early then sat flat, or jumped backward.
  - **Two real bugs found via live use, both fixed**: (1) clicking Analyze then Run before
    Analyze finished silently started a second, redundant `AnalyzeWorker` (Analyze/Run now
    disabled for the whole in-flight duration); (2) station rings drawn with a stale CACHED
    Analyze axis instead of the current rebuild's own recomputed axis (`--axis auto` re-detects
    fresh every rebuild, origin-refine is chord_tol-dependent) produced a huge spiral of rings on
    large-extent parts — now prefers `report["frame"]["axis"]` from the rebuild itself.
  - **Actionable, context-aware error hints** (Brady's explicit ask — "nail down all of these
    types of errors...they should have some actionable advice"): crash/volume hints no longer
    say "try --adaptive" when it's already on (say to turn it OFF instead — confirmed by direct
    reproduction that `--adaptive` is measurably LESS robust than uniform placement on M8's
    slot/fillet transitions, 4/4 uniform pass vs 1/4 adaptive pass across sections
    40/52/60/100); the non-axisymmetric-loop crash hint now computes a concrete suggested
    `--chord-tol` value instead of a vague nudge, and distinguishes real off-axis centers from
    ordinary chordal roundness noise.
    Also fixed the M13-without-`--adaptive` crash's ROOT CAUSE in `pipeline/solids.py`'s fillet
    clamp (a fillet exactly filling half the wedge's span made two meridian points coincide,
    crashing `BRepBuilderAPI_MakeEdge` on a zero-length edge — now clamped a hair short via a
    `1e-6` margin), restored the fallback ladder's exception guard, and left a backstop
    `GeometryError` hint for the case nothing converges.
  - **Watertight confirmation surfaced end-to-end**: `_compute_verification` now reports it; the
    Analyze log line is a descriptive pass/fail checklist (watertight, single body, dropped
    islands) instead of a raw data dump; the Output page's Verification group leads with a
    Watertight row.
  - **Viewport** (done earlier in this same session, verified still working): clickable legend
    rows (`LegendRow` in `app/viewport.py`, synced to the View-menu checkboxes via
    `layer_toggled` signal) replacing the old inert `QLabel` legend, plus independent "Rebuilt
    solid: transparent" / "Input mesh: solid color" View-menu toggles for A/B'ing fin geometry
    against the input mesh.
  - 95 tests pass (`tests/api`, `tests/gui`, `tests/test_stations.py`, `tests/test_solids.py`).
    Committed at `ffd23ef`. **Brady: `git push`.**
  - **Not yet done / next**: none of this has been run against a REAL burnback STL yet — still
    the big open item from Round 2/3 (see below). The M13-without-adaptive fillet-clamp fix and
    the new hints are unit/regression-tested but worth re-confirming by hand on the actual M13
    milestone file (`harness/truth/M13.stl`) at `--sections 50` with no `--adaptive` if that
    specific repro matters again.
- **2026-09-04 10:52 — ROUND 3 COMPLETE. ALL THREE ROUNDS DONE.** G1 (engine API) and G2
  (PySide6 app) each passed on their first iteration; G3 took three Fable visual-review rounds
  (feedback via PROGRESS Notes → `state/G3_APPROVED`); HANDOFF v3 written and verified against
  the code. Round 3: 7 iterations, ≈$29, 3 hours. Project totals: 84 iterations, ≈$469,
  Aug 29 → Sep 4. The deliverables: `rebuild.py` CLI (13/13 milestones), `pipeline/engine.py`
  API (`analyze`/`rebuild` w/ progress+cancel), `python -m app` GUI (dark theme, 3D viewport,
  station table; screenshots in `out/gui/`), `WORK_SETUP.md` (work-machine setup, written for
  an AI assistant), `HANDOFF.md` v3 (484 lines). **Remaining items are all human:** (1) `git
  push` done through Sep 4 by Claude-via-script when Brady asks; (2) SpaceClaim checklist
  (HANDOFF §4); (3) THE big one — a real burnback STL through the §5 runbook / MR slot:
  everything so far is proven only on synthetic meshes; (4) try `python -m app` on the Mac and
  take the repo to work per WORK_SETUP.md.
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
