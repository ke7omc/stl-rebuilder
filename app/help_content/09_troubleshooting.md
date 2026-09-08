# Troubleshooting

## The three failures you are most likely to hit on a real part

### 1. Wrong units, or the wrong axis

**Symptom:** the Extent on the Detected page is off by a factor of 25.4, or the detected axis is
not the one the part is actually built around. Downstream this shows up as section-topology
failures everywhere, or as a plausible-looking STEP at 25.4 times the right scale.

**Fix:** set **Units** explicitly, and set **Axis** explicitly rather than leaving it on `auto`
whenever you know the answer — `auto` is an inertia-tensor fit and is the most fragile stage on a
dirty mesh. Check the Detected page's Frame group before looking at anything else.

### 2. A station landed somewhere pathological

**Symptom:** the run fails with a section-topology error — no section recovered at some z, more
than one outer loop, an outer loop that is not an axis-centred circle, or inconsistent hole
topology between stations.

**Fix, in this order:** turn **adaptive stations off** if it is on — uniform placement is often
more robust across sharp slot and fillet transitions, and adaptive is what put the station on the
edge. Then raise **Sections** (80, then 120, then 200): it moves every station, and usually moves
off the bad z.

Some of these are genuine hard rejections. A **non-axisymmetric outer wall is not supported at
all**, and no setting will change that.

### 3. Mesh noise reconstructed, or real features simplified away

**Symptom:** the run succeeds but the volume or deviation checks are bad, or the face count is far
higher than the 15 to 85 the validated milestones produce, or the STEP looks visibly lumpy.

**Fix:** adjust **Chord tol**. Too low reproduces noise; too high eats features. Bracket by
factors of two around the auto value.

Two traps worth knowing: do not judge the mesh's dimensional fidelity from its bounding box (noise
makes the box read oversized while the mean radius reads undersized), and an exact marching-cubes
input can be radially scaled relative to the real part, which is a volume-error floor no
reconstruction can remove.

## Failure kinds

On a failure the Output page leads with a red banner naming the kind, and the full text goes to
the log. Three families:

| Kind | What it means |
|---|---|
| input | the mesh could not be split into components, or a component is not watertight |
| topology | a section's loop topology could not be resolved — see failure 2 above |
| geometry | the finished solid failed the geometry kernel's own validity check |

A **geometry** failure most often means the chord tolerance is too fine for the mesh's scale — the
boolean tolerances end up at real-feature scale. Try the auto value first.

## A failed verification check is not a failed run

If the run completed and the Output page shows an amber cross rather than a red banner, the STEP
was written and is on disk. Verification is informational. Read the **WHAT TO DO** hint on the
failing row, and read the Verification page — in particular the case where a Bounds failure with a
passing Deviation is the input mesh's own noise and not something any setting will move.

## Application problems

| Symptom | Fix |
|---|---|
| the 3D view is blank | update the graphics driver if you can; otherwise set the environment variable `QT_OPENGL=software` before launching |
| the app will not start | run it from the repository folder — `app` and `pipeline` are packages in the repo root, not installed distributions |
| `import OCP` is very slow the first time | normal, it is a large binary; later imports are fast |
| a demo mesh is missing | the repository ships generators, not meshes: `python -c "from harness import generators; generators.make('M2')"` |
| the STEP will not import into SpaceClaim | import with facewise connections enabled, then see the SpaceClaim checklist in HANDOFF.md |

## Getting more detail

- The **log console** on the dashboard has the exact settings every run was launched with, the
  per-stage progress lines, and the full failure text. Copy it when reporting a problem.
- The **report JSON** beside the output STEP has the station positions, topology events, resolved
  frame, paths used, and the verification block. On a failure it still carries an `error` field
  and the partial stations.
