# stl-rebuilder — project context

## Current status & next steps
- **2026-08-30 — ROUND 2 STARTED.** Plan approved (`~/.claude/plans/1-is-this-saved-golden-taco.md`
  on the laptop): engine robustness first (M6–M13 + MR per MISSION §6.2/§7.2 — loft, loop
  matching, feature-aware stations, marching-cubes inputs with skew/noise, x-axis/off-origin/
  inches/small scale, multi-body, near-burnout cavity decomposition, real-STL slot), then Round 3
  = PySide6 desktop GUI (MISSION §12). The loop re-entered M0 to extend the harness
  (`driver/prompt_m0_round2.md`; harness is NOT restored from its tag while at M0), then the Opus
  review pass re-freezes it, then the driver fast-forwards through M1–M5 to M6. Drop a real
  burnback STL into `real_inputs/` (gitignored) whenever work authorises one — MR picks it up.
  Driver additions: `fast_forward`, `LOOP_STOP_AT=<milestone>`, `SCORE_TIMEOUT_S` 60 min.
  Expected cost ≈ $200–340, 3–5 days of loop time. **Brady: `git push` (the driver never pushes).**
- **2026-08-29 20:41 — Round 1 done.** 25 iterations, ≈$103 API-equivalent, one day. All
  five milestones pass on commit `1b4ff91` (verified together by the driver's regression sweep):
  M1 annular cylinder, M2 ellipsoidal domes, M3 6-point star bore, M4 finocyl with a topology
  event at z=6000, M5 domes + fins with two events. Volume errors ≤ 0.02 %, deviation max
  ≤ 0.44 mm (gate 0.6), gmsh min SICN ≥ 0.187 (gate 0.1), 1–2 s per rebuild. `HANDOFF.md`
  has the results table (§2), the SpaceClaim checklist (§4), how to run a real burnback STL
  incl. choosing `--chord-tol` (§5), and honest limitations (§6). Winning STEPs:
  `logs/M{1..5}-final-step.step`.
- **Next (Brady):** (1) `git push` — the driver never pushes and the Bash guard blocks Claude
  from pushing too. (2) SpaceClaim checklist on the five STEPs (HANDOFF §4). (3) Run a real
  burnback STL per HANDOFF §5 and note what SpaceClaim says; that feedback is the spec for the
  next round. (4) Know the gaps before trusting it on real grains: non-circular bores must be
  axially constant (no loft path — the biggest gap for mid-burn surfaces), one outer + one bore
  loop per station, non-axisymmetric outers rejected, and `--adaptive`/`--refine-bands` are
  no-ops (M5's adaptive-efficiency gate was met by cosine end-clustering — a harness weakness
  worth fixing if a next round needs real feature-aware station placement).
- **If continuing the loop:** add M6+ to MISSION.md (tapered star / lofted non-circular bore,
  multiple perforations, a real-STL-derived case), re-run the harness review (the harness is
  frozen at `harness-frozen`; re-point it after changes), then `./loop.sh`.
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
