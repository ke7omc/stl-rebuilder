## MODE: TOURNAMENT

This milestone has stalled through normal and escalated iterations. Depth has stopped paying;
buy breadth. You are the orchestrator of a small tournament.

1. From `PROGRESS.md` and `out/score.json`, identify the failing stage and write down 2–3
   **genuinely different** strategies for it (not parameter tweaks of one idea). Examples for a
   loft failure: ruled per-pair lofts + UnifySameDomain; custom skin surfaces + sewing; revolve
   or prism decomposition of the chain; different seam/parameterization scheme.
2. For each strategy, spawn a subagent with the Agent tool using an isolated git worktree
   (`isolation: "worktree"`). Give each subagent: the strategy name, the exact hypothesis, the
   files it may change (only `pipeline/`), the command to score itself
   (`.venv/bin/python harness/score.py --milestone <M> --out out/score.<name>.json`, run from
   its worktree with the venv at the main repo path), and the instruction to commit on its
   branch and return the resulting `progress`, `first_failure`, and a 5-line summary.
3. Run the subagents in parallel. Wait for all.
4. Pick the branch with the highest `progress` (ties: fewer lines changed). Merge ONLY that
   branch into main (`git merge --no-ff`). Do not merge the others.
5. Re-run the scorer on main to confirm. Append a PROGRESS.md block recording every
   candidate's strategy, score, and the specific reason the losers lost (this is the most
   valuable output of a tournament — it prunes the search for every later iteration).
6. Commit. Clean up worktrees you created.
