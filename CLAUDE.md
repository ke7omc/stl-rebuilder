# stl-rebuilder — project context

## Current status & next steps
- 2026-08-29 (first unattended run, 08:10–11:24, 11 iterations, ≈$27): the M0 harness is built —
  all five truth generators, 22 selftest checks, scorer contract valid — and went through the
  Opus review pass (`33e3257`), which tightened three §6 gates but rewrote the deviation metric
  into something that exhausts 64 GB RAM + 64 GB swap on M2's fine tessellation (SIGKILL at
  ~11.5 min, every run since). Iter 11 (`8cedc57`) coarsened the tessellation (`CHORD_TOL/2`);
  whether that is enough is unverified. State: M0 build phase, escalated (Opus), stall reset.
- The run exposed driver gaps, now fixed (commit "driver: hardening after the first unattended
  run"): Ctrl-C/timeout left the agent running as an orphan; no clock for the agent; the
  review pass counted as builder stalls; killed agents booked $0; no memory guard; no live
  progress. See README "Watch it / Stop it" and the `CONFIG` block for the new knobs
  (90/120/180-min timeouts, $6 normal budget, $120 window, 24 GB memory cap, 5-min heartbeats).
- **Next (Brady):** `cd ~/Projects/stl-rebuilder && ./loop.sh` and leave it. Expected: iter 12
  (Opus) verifies/fixes the memory blowup → selftest green → review pass → `harness-frozen` →
  M1 on Sonnet. Watch `tail -f logs/loop.log` / `python3 loop.py --status`; stop with
  `python3 loop.py --stop` (graceful) or `--kill` (now; Ctrl-C is the same).
- After the first real `RATE/USAGE LIMIT` line, set `LOOP_WINDOW_BUDGET_USD` to roughly what
  `--status` shows spent in that window (the $120 default is a placeholder above today's rate).
- The driver never pushes; push manually from an interactive session to sync GitHub.
- After HANDOFF.md exists: Brady opens the listed STEP files in SpaceClaim (Notion Phase 4).

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
