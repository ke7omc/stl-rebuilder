# Outline and Details

The left **Outline** dock is a four-step map of a run. Selecting a node swaps the right-hand
**Details** dock to that node's page. The tool moves the selection for you as a run progresses,
so the Outline doubles as a progress indicator.

| Node | Page | When it fills in |
|---|---|---|
| Input | settings and actions | always available |
| Detected | what Analyze measured | after Analyze |
| Stations | radius profile and per-station table | after Run |
| Output | verification, files, result, report | after Run (or after a failure) |

Two nodes grow a suffix once a run completes: **Stations (40)** picks up the actual station count,
and **Output** picks up a tick when the run produced a verification block.

## Input

Covered in full on the **Input options** page: source paths, axis, units, sections, chord
tolerance, adaptive stations, and the Analyze / Run / Cancel row.

## Detected

Everything Analyze measured, in three groups. Read this page before trusting anything downstream.

**Frame**

- **Axis** — the detected axis with a confidence percentage. Low confidence on a part you expected
  to be strongly elongated means the fit latched onto the wrong principal direction.
- **Origin (X, Y)** — how far off the world origin the axis sits. Large unexpected values usually
  mean noise islands dragged the inertia tensor.
- **Units** — echoes what you chose. Confirm it.

**Mesh**

- **Extent** — the part length along the axis, in millimetres. The fastest units check available:
  off by 25.4 either way means the Units setting is wrong.
- **Bodies** — how many separate watertight components the mesh split into. More than one is fine
  and supported (a three-segment BATES grain, say); it changes what the Bodies verification check
  expects.
- **Triangles** — the input mesh's triangle count.
- **Median edge length** — a mesh statistic, useful for judging how coarse the input is. **Do not
  use it as a chord tolerance** — see Input options for why.
- **Watertight** — yes or no after repair. The whole pipeline assumes yes.
- **Dropped islands** — how many disconnected noise fragments the repair discarded.
- **Bounds** — the mesh's bounding box.

**Suggested run settings**

- **Chord tol (auto)** — the sag-derived value "auto from mesh" will use for the rebuild.

## Stations

The radius profile chart and the per-station table. Covered on its own page.

## Output

The result of the last run. On success: the Verification group first (its own page), then the
files written, the solid's body/face/volume/station counts, the report summary, and any warnings.
There is a **Reveal file** button at the bottom that opens the output folder.

On failure the page leads with a red error banner carrying the failure kind and message, and the
tree holds the same two fields instead of a manifest. See the Troubleshooting page for what each
kind means.
