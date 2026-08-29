## MODE: ESCALATED

The previous iterations on this milestone stalled (no improvement in the scorer's `progress`).
You are the stronger model and you are here to change the approach, not to try harder at the
same thing.

Before touching code:
1. Read `## Do not retry` and the last 5 log blocks in `PROGRESS.md` carefully.
2. Read the pipeline's own report/log for the failing run (`artifacts` paths in `out/score.json`).
3. Write a short diagnosis in PROGRESS.md: what the evidence says is actually wrong (cite the
   numbers), which hypotheses are now ruled out, and the different hypothesis you will test.

Then make ONE change that tests that hypothesis. If the failure is in lofting, consult the
fallback ladder in MISSION.md §5.2 step 6 and move down one rung rather than re-tuning the
current one. Record the outcome either way.
