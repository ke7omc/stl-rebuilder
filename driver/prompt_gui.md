## ROUND 3 (GUI milestones G1–G3) — extra rules for this milestone

You are building the desktop GUI ladder specified in MISSION §12 and §6.3. The geometry
scorer does not grade these milestones; the DRIVER runs the gate commands below after your
iteration and its verdict (exit codes + output tail) arrives in your next header exactly like
a scorer verdict. Verify locally with the SAME commands before committing.

Gate commands the driver runs (mirror them exactly):
- **G1**: `.venv/bin/python -m pytest tests/api -q` must exit 0, AND every milestone M1–M13
  must still pass the frozen scorer (the driver re-scores them all — the engine extraction
  must not change CLI behaviour at all).
- **G2**: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/gui -q` exit 0, AND
  `QT_QPA_PLATFORM=offscreen .venv/bin/python -m app --smoke out/gui` exit 0 producing
  `out/gui/smoke.json` plus at least 3 PNG screenshots ≥ 20 KB each (§12 contract).
- **G3**: same smoke run fresh, AND human/Fable **visual approval**: the driver stops the loop
  with "awaiting visual review"; a reviewer looks at `out/gui/*.png` and either writes
  feedback into PROGRESS.md `## Notes from Brady` (fix it, improve polish, iterate) or
  creates `state/G3_APPROVED`. Your job while unapproved: act on every feedback note, then
  regenerate the screenshots. Never create `state/G3_APPROVED` yourself — that is gaming,
  it is checked, and the reviewer diffs the screenshots.

Design bar (G2/G3): this must look like professional engineering software (Ansys
Discovery/Mechanical reference): dark theme by default via QSS, Outline/Details/3D-view/Log
layout with QDockWidget, qtawesome icons, consistent 4/8px spacing grid, real typography —
no default-grey Qt look, no cramped dialogs. The 3D viewport (pyvistaqt QtInteractor) shows
the input STL translucent over the rebuilt solid, station planes, topology-event markers, and
an axis triad. Engine calls run in a QThread worker with progress + cancel — the UI never
blocks.

Constraints: engine logic lives in `pipeline/engine.py` (`analyze()`, `rebuild()` per §12);
`app/` imports the engine, never the CLI internals; no gmsh or GPL imports anywhere under
`pipeline/` or `app/`; `rebuild.py` CLI behaviour must remain byte-identical (M1–M13 are
re-scored on every G pass); keep WORK_SETUP.md §6 accurate if you change the launch contract;
tests must pass offscreen with no display.
