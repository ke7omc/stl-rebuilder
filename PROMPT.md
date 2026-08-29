You are one iteration of an unattended coding loop. Nobody is watching; do not ask questions.
Work autonomously to completion of ONE focused step, then stop.

Start by reading, in this order:
1. `MISSION.md` — the spec and rules of engagement (rules in §2 are non-negotiable).
2. `PROGRESS.md` — `## Current state`, `## Do not retry`, and the most recent log blocks.
3. `out/score.json` — the driver's latest verdict (if it exists). The `first_failure.hint` is
   the single most important line in the repo right now.
4. `git status` and `git log --oneline -5` — if there are uncommitted changes from an
   interrupted iteration, evaluate them (keep, finish, or revert) before doing anything else.

Then:
- Decide the ONE change most likely to raise the scorer's `progress` for the current milestone.
  Say what it is in one line at the top of your work.
- Implement it. Use `.venv/bin/python` for everything. Consult `docs/research/*.md` for exact
  APIs before writing kernel or trimesh code — they contain verified signatures and traps.
- Verify locally with the same command the driver uses:
  `.venv/bin/python harness/score.py --milestone <current> --out out/score.local.json`
  (at M0, run `.venv/bin/python harness/selftest.py` instead). Read the JSON. If your change
  made things worse, revert it and record that in PROGRESS.md instead of leaving it in.
- Append a log block to `PROGRESS.md` (format in MISSION.md §8) and refresh `## Current state`
  and `## Do not retry`.
- Commit: `git add -A && git commit -m "<milestone>: <what changed>"`. Do not push.

Time, checkpoints, and saving work (the driver enforces the hard kill given in the header):
- You cannot see a clock; budget from the header's deadline and reserve the last 15 minutes for
  committing and updating PROGRESS.md. At the kill the driver auto-commits whatever is on disk,
  but your reasoning is lost unless it is already written in PROGRESS.md.
- Commit after every verified sub-step (`git commit -m "WIP <milestone>: <step>"` is fine);
  never carry more than ~20 minutes of uncommitted work.
- Before any run longer than 5 minutes (a full selftest, a scorer run), first write what you are
  testing and what result you expect into PROGRESS.md and commit. While iterating, verify the
  narrowest thing that answers the question (one milestone, one check); do one full run at the end.
- Run long commands unbuffered (`.venv/bin/python -u ...`) into a log file and poll it; never
  block on a silent call. The driver's own evaluation caps memory (MEM_LIMIT_GB in the header)
  and wall clock, and reports the reason in the next header — read `## Last driver evaluation`
  before re-running anything.

Constraints: never edit frozen paths (`loop.py`, `loop.sh`, `driver/`, `PROMPT.md`,
`MISSION.md`, `CLAUDE.md`, `.claude/`, and `harness/` once frozen); no network; no installs
outside `.venv`; no `git push`; do not delete `harness/truth/` artifacts; keep runtime of any
single command under 15 minutes.

When you finish, print a 3-line summary: what you changed, the local score before → after, and
what the next iteration should try.
