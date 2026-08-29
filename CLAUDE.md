# stl-rebuilder — project context

## Current status & next steps
- 2026-08-29: Loop infrastructure built (MISSION.md spec, PROMPT.md, loop.py driver, driver/
  mode prompts + Bash guard hook, .claude/settings.json allowlist, docs/research/ reference
  dumps, .venv verified on Python 3.14). Milestone M0 (harness) not yet started by the loop.
- Next: run `./loop.sh --once` under supervision to prove the plumbing, then `./loop.sh` to run
  unattended until HANDOFF.md exists. Monitor with `tail -f logs/loop.log` and
  `python3 loop.py --status`.
- After the loop finishes: Brady opens the STEP files listed in HANDOFF.md in SpaceClaim.

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
