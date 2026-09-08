# Input options

Every setting the tool has lives on one page: **Outline > Input**, in the Details dock. This page
documents all of them.

## Source

**Input STL** — the burnback surface mesh to convert. Use Browse, or type/paste a path directly:
the preview loads on Enter or when the field loses focus. Selecting a file shows the mesh in the
viewport straight away, before Analyze or Run has been touched, so you always have visual
confirmation that the file you meant is the file that loaded.

**Output STEP** — where the solid is written, default `out/rebuilt.step`. The report JSON and the
preview STL are written alongside it under the same base name. The folder is created if needed.

## Geometry

**Axis** — `auto`, `x`, `y`, or `z`. The motor's axis of revolution.

- `auto` is an inertia-tensor fit and is the single most fragile stage on a dirty mesh. It
  re-detects fresh on every rebuild, not once per file.
- **Prefer an explicit axis whenever you know the answer.** A typical Fluent-style motor exported
  from CAD is x-aligned.
- Check the detected axis and its confidence on the Detected page before trusting anything
  downstream of it.

**Units** — `mm`, `in`, or `m`. **STL files carry no units; you have to know.** The output STEP is
always written in millimetres regardless of what you choose here, and every number the app
displays after Analyze is in millimetres.

The fastest units check there is: compare the **Extent** on the Detected page against the part
length you expect. If it is off by a factor of 25.4 either way, this setting is wrong.

## Fidelity

**Sections** — 4 to 500, default 40. How many cross-section stations are taken along the axis.
More stations resolve more axial detail and cost more time. For a multi-body input (three BATES
segments, say) the count applies **per body**, not across the whole file.

Raising the section count is also the first thing to try when a run fails with a section-topology
error: it moves every station, and usually moves off whatever pathological z caused the failure.
The validated milestones all sit at 120 sections or below; higher values are untested for runtime.

**Chord tol (mm)** and **"auto from mesh"** — the chord tolerance describes **how faithful the
input mesh is**, not how much error you are willing to accept in the output.

- With **"auto from mesh"** checked (the default) the spinbox is disabled and the value comes
  from Analyze: twice the p95 of the mesh's own per-edge chordal **sag**.
- That sag estimate is deliberately **not** the median edge length. A clean CAD tessellation can
  carry 25 mm edges while sagging barely half a millimetre off the true surface; sizing the
  tolerance from edge length there puts the boolean tolerances at real-feature scale and either
  fails outright or produces an invalid solid.
- Uncheck it to type a value yourself. Sanity band from the validated milestones: a clean CAD
  tessellation wanted 0.5, a 10 mm-voxel marching-cubes scan wanted 5, an 8 mm-voxel one wanted 8.
- Too small reproduces mesh noise as extra faces. Too large simplifies real features away. If you
  are bracketing by hand, go by factors of two.

**Adaptive stations** — off by default. Clusters stations at detected features (domes, fillets,
slot ends) instead of spacing them uniformly.

- It is genuinely useful on domes and near-burnout geometry, and several validated milestones
  require it.
- It is **not** universally better. Uniform spacing is often **more robust** across sharp
  slot/fillet transitions, where adaptive placement can put a station exactly on the edge that
  breaks it. Directly measured on the M8 milestone: uniform placement passed at every section
  count tried, adaptive passed at one of four.
- **If a run crashes with adaptive on, turn it off before adding sections.**

## Actions

**Analyze**, **Run**, **Cancel** — the same three actions as the Run menu and the toolbar. Analyze
and Run are both disabled for the whole in-flight duration of any operation; Cancel is enabled
only while something is running.
