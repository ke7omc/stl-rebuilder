# Quick Start

STL Rebuilder converts a solid-rocket-motor **burnback surface mesh** (an STL) into a true
**BRep solid** (a STEP file) that SpaceClaim, Fluent, and other CAD tools can import as real
geometry rather than as a triangle soup.

This page walks the whole tool end to end on **M2**, the domed-capsule test motor, in ten steps.
It takes about a minute of machine time. Everything else in this manual is a detail page for one
of these steps.

There is an interactive version of this walkthrough too — **Help > Guided Demos**, thirteen of
them, covering every burnback shape the engine was validated against, each one walked through
inside the window with the pointer landing on the control you need next. The **Guided demos**
page lists what each teaches, and says whether they are active in this build.

## Before step 1: get a test mesh

The repository ships **no meshes** — it ships the generators that produce them. Create M2 once,
from the repository folder:

```
.venv/bin/python -c "from harness import generators; generators.make('M2')"
```

That writes `harness/truth/M2.stl` (the input) and `harness/truth/M2.step` (the analytic answer
to compare against). On Windows the command is
`.venv\Scripts\python -c "from harness import generators; generators.make('M2')"`.

## The ten steps

1. **Open the mesh.** File > Open STL (Ctrl+O), pick `harness/truth/M2.stl`. The mesh appears in
   the 3D viewport immediately, before anything has been analyzed or built.

2. **Check the output path.** Outline > Input, in the Details dock on the right. Output STEP
   defaults to `out/rebuilt.step`. Every run also writes `out/rebuilt.report.json` and
   `out/rebuilt.preview.stl` beside it.

3. **Set the axis and units.** For M2, Axis `z` and Units `mm`. Axis `auto` also works here, but
   units are never guessable — an STL carries no units, and you have to state them.

4. **Leave the fidelity settings alone.** Sections 40, "auto from mesh" checked, "adaptive
   stations" off. These are M2's validated settings.

5. **Click Analyze (F5).** This loads and repairs the mesh, detects the motor axis and scale, and
   measures mesh quality. It builds no geometry. The ANALYZE dial on the dashboard runs while it
   works.

6. **Read the Detected page.** The Outline selection jumps there on its own. Confirm the axis is
   what you expected, the extent is the length you expected, and the mesh is watertight. The
   suggested chord tolerance at the bottom is the number "auto from mesh" will use.

7. **Click Run (Ctrl+R).** The rebuild takes the sections, builds the solid, and writes the STEP.
   The LOAD, SCAN, SECTIONING, and BUILD dials advance in turn; the status bar shows a progress
   bar for the duration.

8. **Read the Verification group.** The Outline selection jumps to Output when the run finishes.
   Every check should be a green tick. **A failing check never blocks the run and never changes
   the output** — it tells you where the solid disagrees with the input, so you know to look.
   Deviation is the check to trust most; see the Verification page for why.

9. **Look at the result.** In the viewport, press **B** to swap between the input mesh and the
   rebuilt solid, press **S** for a section view through the bore, and press **F** to re-frame the
   camera. The Stations page shows the radius profile and the per-station cross-section table.

10. **Take the STEP to CAD.** Use the "Reveal file" button at the bottom of the Output page to
    open the folder. Import `out/rebuilt.step` and confirm it arrives as one solid with the volume
    the Verification group reported.

## What to read next

- **Workflow** — what Analyze and Run actually do, and why Run sometimes runs Analyze first.
- **Input options** — every setting on the Input page and when to change it.
- **Verification** — how to read a failed check, including the failure that is not yours to fix.
- **Troubleshooting** — symptoms and the one knob that fixes each.
