# stl-rebuilder — an autonomous loop that builds an STL→solid (STEP) converter

Solid-rocket-motor burnback geometries arrive as STL meshes; CFD needs true solids. This repo
does not (yet) contain the converter — it contains a **self-annealing coding loop** that builds
it: a driver repeatedly launches a fresh headless Claude agent, scores the result against
synthetic ground truth with a frozen harness, and stops only when every milestone gate is
green. The spec the agent works from is [`MISSION.md`](MISSION.md).

## Quick start

```bash
cd ~/Projects/stl-rebuilder
./loop.sh --dry-run        # show config + the exact prompt/args, call nothing
./loop.sh --once           # one supervised iteration (preflight + smoke test + 1 iteration)
./loop.sh                  # run unattended until HANDOFF.md exists or a guardrail trips
```

Watch it:
```bash
tail -f logs/loop.log                  # narrative + a heartbeat every 5 min while an agent or scorer runs:
                                       #   [hb] iter 12 escalated/opus-5 M0: 25 min of 2h00m (21%), deadline 13:24 — turns 14, tools 9, last: Bash(...) 2 min ago — window $21/$90
python3 loop.py --status               # where it is: iteration/phase, elapsed vs deadline, spend vs gate, last evaluation
cat state/STATUS.md                    # the same plus a table of the last 12 iterations (refreshed after every iteration)
cat out/score.json | python3 -m json.tool | head -40   # the latest scorer verdict
git log --oneline | head               # one commit per iteration (plus the agent's own WIP commits)
```

Stop it:
```bash
python3 loop.py --stop     # graceful: finish and evaluate the in-flight iteration, then exit
python3 loop.py --kill     # now: kill the agent's whole process tree, checkpoint-commit its work, save state, exit
                           # (Ctrl-C in the driver's terminal does the same as --kill)
```
All state is on disk (`state/loop_state.json`, git); rerun `./loop.sh` to resume exactly where
it left off — if it was stopped after an agent finished but before its evaluation ran, the
evaluation runs first. If it stopped on a guardrail, read `state/STATUS.md`, fix or adjust,
then `python3 loop.py --reset-stall && ./loop.sh`. Preflight also kills any agent left orphaned
by an earlier driver.

Tunable knobs are the `CONFIG` block at the top of `loop.py`; any of them can be overridden
per run with an env var, e.g. `LOOP_WINDOW_BUDGET_USD=60 LOOP_MODEL_DEFAULT=claude-opus-5 ./loop.sh`.

## Environment

`.venv` is a Python 3.14 venv with `numpy scipy shapely networkx rtree trimesh build123d gmsh
pytest` (build123d pulls `cadquery-ocp-novtk`, i.e. OCCT 7.9.3, importable as `OCP`). Recreate:
```bash
python3 -m venv .venv && .venv/bin/pip install numpy scipy shapely networkx rtree trimesh build123d gmsh pytest
```
The `claude` CLI must be logged in (run `claude` once interactively). The driver strips
`ANTHROPIC_API_KEY` from the agent's environment so iterations bill the subscription; set
`LOOP_USE_API_KEY=1` to use the API instead.

## How the loop works (the looping-agent primer)

**1. The loop is dumb bash/Python; the intelligence is inside each iteration.** `loop.py`
launches `claude -p` with the prompt on stdin. Each launch is a *fresh* agent with no memory of
previous iterations — deliberately (the "Ralph Wiggum" pattern). Long sessions accumulate
context rot; fresh sessions stay sharp. State lives in files instead: `MISSION.md` (spec, never
changes), `PROGRESS.md` (the agent's lab notebook), `out/score.json` (the latest verdict), and
git history (one commit per iteration).

**2. It never asks a human because something else answers its questions.** The harness in
`harness/` generates ground-truth solids analytically, tessellates them to STL as the
pipeline's input, runs `rebuild.py`, and scores the output: volume error, surface deviation
(with the *location* of the worst point), watertightness, single-solid, STEP round-trip, and
whether gmsh can tet-mesh it. The scorer's `first_failure.hint` — e.g. *"max deviation 42 mm at
z=9700 (aft_dome); only 2 stations there; densify toward the apex"* — is the feedback that
replaces a person looking at screenshots.

**3. The driver decides when it is done, and the agent can't grade itself.** After every
iteration the *driver* runs the scorer and reads the exit code; a milestone advances only on
the driver's verdict. The harness is git-tagged (`harness-frozen`) once it passes its own
self-test plus an Opus review pass, and the driver restores it from that tag before every
scoring run — so the agent cannot "fix" the test to pass. The same protection covers the
driver, prompts, and spec (`infra-frozen`).

**4. Guardrails make unattended safe.**
- *Permissions:* `.claude/settings.json` auto-approves edits and Bash but denies `git push`,
  network tools, system package managers, 1Password, and recursive deletes; a PreToolUse hook
  (`driver/guard_bash.py`) catches the same things inside `bash -c` / `&&` chains. Each
  iteration runs with all MCP servers and skills disabled. If permission denials keep stalling
  iterations, `LOOP_SKIP_PERMISSIONS=1` switches to `--dangerously-skip-permissions` (your call).
- *Cost and time:* a per-iteration `--max-budget-usd` ($10 normal, $15 escalated/review, $20
  tournament), a per-iteration wall-clock timeout by mode (90 min normal, 120 escalated/review,
  180 tournament) that kills the agent's whole process tree and auto-commits whatever is on
  disk, an iteration cap, and every iteration's cost/turns/model logged to
  `logs/iter-NNNN.claude.json`. The agent is told its deadline and a commit-by time in the
  prompt header. If the CLI dies before reporting cost, the driver books the budget cap as an
  estimate so the usage gate errs safe.
- *Machine protection:* the driver's own selftest/scorer runs are watched every 5 s and killed
  if their process tree exceeds `MEM_LIMIT_GB` (24 GB) or `SCORE_TIMEOUT_S` (40 min); the reason
  is written into the evaluation tail that the next agent sees in its header.
- *Usage pacing:* the driver sums each iteration's cost into a rolling 5-hour window and
  pauses **between** iterations (the in-flight task always finishes and commits) when it
  reaches 75 % of `WINDOW_BUDGET_USD` (default $120), resuming automatically when the window ages out. If a
  rate/usage limit hits mid-iteration anyway, the driver parses the reset time from the error,
  sleeps, and retries without counting it as an iteration; partial edits stay in the working
  tree for the next iteration to pick up. `WINDOW_BUDGET_USD` is a stand-in for your plan's
  real window — after the first time the backstop fires, set it to roughly what was spent.
- *Stalls:* no improvement in the scorer's `progress` for 3 iterations → escalate from Sonnet
  (medium effort) to Opus 5 (high); 4 more → a **tournament** (Opus spawns 2–3 subagents in
  isolated worktrees, each with a different strategy; only the best-scoring branch merges);
  12 → stop with `state/STATUS.md`. A milestone pass resets everything back to Sonnet. At M0,
  progress is 0.6 for the six harness modules existing plus 0.35 × the fraction of selftest
  checks passing; when the Opus review pass sends the harness back to build, the bar resets so
  the review's stricter checks do not count as builder stalls (no tournaments at M0).

**5. Model choice is per iteration, in code.** `--model sonnet` by default (cheap, fine for
spec-following work), `--model claude-opus-5` when escalated, for the harness review, and for
tournaments; effort `medium`/`high`/`xhigh` respectively — never `max` by default. Preflight
sends a one-turn hello per model and logs which model *actually* served it (`model_usage`), so
a stale alias can't silently downgrade the loop.

## Milestones (driver-scored; details in MISSION.md §6)

| M | Truth geometry | Passes when |
|---|---|---|
| M0 | — | harness self-test green + scorer contract valid, then Opus review, then frozen |
| M1 | annular cylinder 10 m × Ø2 m, bore Ø0.6 m | volume < 0.05 %, deviation p99 < 0.8·chord_tol, 1 solid, STEP + gmsh OK |
| M2 | + 2:1 ellipsoidal domes | dome bins pass the deviation gate, apex meshable |
| M3 | 6-point star bore with fillets | volume < 0.1 %, deviation < 2·chord_tol |
| M4 | finocyl, 8 fin slots aft of z=6 m | topology event found at z=6000 ± tol, still 1 solid |
| M5 | M2 + M4 with `--adaptive` | all gates with ≤ 50 % of the uniform station count, < 120 s |
| HANDOFF | — | `HANDOFF.md` written for human review in SpaceClaim |

## Layout

```
MISSION.md PROMPT.md PROGRESS.md CLAUDE.md   spec / per-iteration prompt / lab notebook / project context
loop.py loop.sh driver/                      driver, mode prompts, Bash guard hook (frozen: infra-frozen)
docs/research/                               verified API research the agent reads instead of the web
rebuild.py pipeline/                         the product (agent-owned)
harness/                                     scorer + truth generators (frozen after M0: harness-frozen)
tests/ out/ logs/ state/                     agent tests / artifacts / per-iteration logs / driver state
```

To change a frozen file yourself: edit, commit, `git tag -f infra-frozen` (or `harness-frozen`).
