# Workflow: Analyze and Run

The tool has exactly two operations. Everything else in the window is either an input to one of
them or a view of what they produced.

## Analyze (F5)

Analyze reads the input mesh and works out how to interpret it. It builds **no geometry** and
writes **no files**.

What it does:

- Loads the STL and repairs it — fills small holes, drops noise islands, and reports whether the
  result is watertight.
- Detects the **motor axis** by an inertia-tensor fit, and reports a confidence percentage.
- Applies the **units** conversion you chose, so every number after this point is in millimetres.
- Measures the mesh: triangle count, body count, median edge length, axial extent, bounds.
- Estimates a **suggested chord tolerance** from the mesh's own chordal sag.

The results land on the **Detected** page, and the Outline selection moves there automatically.
The status-bar telemetry line picks up `axis - units - triangles` and keeps showing it.

## Run (Ctrl+R)

Run does the actual reconstruction: it slices the mesh into cross-section stations along the
axis, classifies each station's loops, builds the solid from them, and writes the STEP.

Every run writes three files, all named after the Output STEP path:

| File | Contents |
|---|---|
| `<output>.step` | the BRep solid — the deliverable |
| `<output>.report.json` | stations, topology events, frame, paths used, verification |
| `<output>.preview.stl` | a tessellation of the solid, for eyeballing in a mesh viewer |

When the run finishes, the Outline selection moves to **Output** and the Stations page fills in.

## Why Run sometimes runs Analyze first

If **"auto from mesh"** is checked and there is no analysis for the currently loaded input, Run
silently runs Analyze first and then chains straight into the rebuild. The log says so:
`auto chord-tol needs Analyze first - running it now`.

This is deliberate. Without an analysis to read the auto value from, the chord tolerance would
silently fall back to the raw spinbox default, which is often far too fine for a real motor's
scale — fine enough to fail solid construction outright, with nothing pointing at chord tolerance
as the cause.

An Analyze that chains into a Run counts as **one mission** on the dashboard: the T+ clock runs
across both.

## While something is in flight

- Both **Analyze** and **Run** are disabled for the whole duration of any operation, however it
  started. Clicking Analyze and then Run used to start a second, redundant analysis.
- **Cancel (Esc)** is enabled instead. A cancelled run shows `ABORTED` on the status strip, not a
  fault — you stopped it, nothing broke.
- The status bar shows a determinate progress bar and a busy spinner; the dashboard dials show
  which phase is active.

## Re-running with different settings

Change a setting on the Input page and click Run again. Nothing is cached across runs except the
analysis, and even that is re-checked against the current input path. In particular, `Axis: auto`
**re-detects the axis fresh on every rebuild** — it is not reused from Analyze — because the axis
origin refinement depends on the chord tolerance the rebuild is actually using.
