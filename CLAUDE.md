# stl-rebuilder — project context

## Current status & next steps
- 2026-08-29: Loop infrastructure built and **plumbing proven with 2 supervised iterations**
  (`./loop.sh --once` ×2): agent invoked headless, committed its own work, PROGRESS.md kept,
  driver scored/recorded cost, M0 progress proxy moved 0.0 → 0.3. Cost so far ≈ $2.9 (plus
  ~$0.5 of preflight smoke tests). Harness state: `milestones.py`, `generators.py` (M1 only),
  `metrics.py` (+6 tests) exist; `meshcheck.py`, `score.py`, `selftest.py`, M2–M5 generators
  still to come — all inside M0.
- Driver facts learned: CLI 2.1.152's `sonnet` alias resolves to Sonnet **4.6**, so the driver
  uses full IDs (`claude-sonnet-5`, `claude-opus-5`) — both verified served correctly.
  `--max-turns` does not exist on this CLI; per-iteration bounds are `--max-budget-usd` + timeout.
- **Next (Brady):** start the unattended run — `cd ~/Projects/stl-rebuilder && ./loop.sh` —
  and leave it. Monitor with `tail -f logs/loop.log` / `python3 loop.py --status`. Expect several
  more M0 iterations, then an Opus review pass, then the `harness-frozen` tag and M1.
- After the first `USAGE GATE` or `RATE/USAGE LIMIT` line appears, set `LOOP_WINDOW_BUDGET_USD`
  to roughly what had been spent in that window (default 40 is a placeholder).
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
`infra-frozen`; to change `loop.py`/`PROMPT.md`/`MISSION.md`, edit, commit, then
`git tag -f infra-frozen`. The loop's state is `state/loop_state.json` (gitignored).

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
