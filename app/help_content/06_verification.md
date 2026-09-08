# Verification

Every successful run compares the **repaired input mesh** against the **solid that was actually
written to the STEP**, and reports the result as a checklist at the top of the Output page. The
same numbers are embedded in the report JSON under `verification`.

**A failing check never blocks the run, never changes the output, and never changes the exit
code.** It flags where the solid disagrees with the input beyond tolerance, so you know to look
before you trust it. That is why a failure is drawn amber, not red.

| Glyph | Meaning |
|---|---|
| green tick | pass |
| amber cross | fail, informational |
| grey dash | not applicable or not measurable |

## The checks

**Watertight** — the input mesh's watertightness after repair. By the time a rebuild has run this
is always true; the row is there as confirmation, not as a live gate.

**Volume** — the input mesh's enclosed volume against the STEP solid's, tolerance **0.5 %**. The
most direct "is this the same part" check there is. Note one floor it cannot get under: a
marching-cubes input can be radially scaled relative to the real part, which shows up as a
volume error no reconstruction can remove.

**Bounds X / Y / Z** — the per-axis bounding range of the input against the solid, tolerance
`max(2 x chord tol, 0.1 % of the axial extent)`. The chord-tolerance term covers the
tessellation's own sag; the length term covers legitimate end-pinch solving. Each row shows both
ranges, the largest deviation, and the tolerance.

**Bodies** — how many separate bodies the engine processed against how many solids ended up in the
STEP. A three-segment BATES grain should read 3 expected against 3 in STEP.

**Deviation** — an approximate sampled surface deviation: subsampled input vertices against the
solid's tessellation, reported as p95 and max, with the pass gated on **p95** against a tolerance
of twice the chord tolerance.

**Deviation is the primary "is the reconstruction actually right" signal.** It is a robust
statistic over the whole surface. Volume can be right for the wrong reasons, and Bounds is a
single most-extreme point on each axis; p95 deviation is neither.

## Reading a failed check

A failed row appends the engine's own suggestion right into the visible text, under an unmissable
**WHAT TO DO:** label — not hidden in a tooltip. Read that before changing any setting.

Some hints tell you what to change. Some hints tell you that **nothing will change it**, and those
are the ones worth trusting most.

## The case where a failure is not yours to fix

The pattern to learn: **a Bounds failure on the motor axis, with Deviation passing comfortably.**

This is what the M9 test motor does at its own official, historically validated settings. Its
solid stops about 21 mm short of the input's aft-dome tip, so Bounds Z fails — and no combination
of chord tolerance and adaptive placement moves it. A finer tolerance crashed the run outright; a
coarser one and the auto-suggested one both left the shortfall identical.

The reason is the input, not the reconstruction. M9 is a genuinely noisy, coarse marching-cubes
mesh with a median edge length of 40 mm, and the true surface position at the single
most-extreme apex point carries real uncertainty at that scale. Meanwhile Deviation passes at
about 2.3 mm against a 10 mm gate — four times of margin, over the whole surface.

When Bounds and Deviation disagree like this, **trust Deviation**. Bounds is one point; Deviation
is the surface. The engine's hint on that row says the same thing, and says it because every
alternative was tried.

Rule of thumb: a failed check plus a passing Deviation plus an honest hint means read, not re-run.
Not every amber cross is yours to fix.

The M9 guided demo walks this exact case live, from a run that fails on purpose. Start it from
**Help > Guided Demos**, pick *M9 — Noisy Scan Input*, and read the failure on your own machine
rather than here. It generates a 44 MB mesh the first time and takes a few minutes to run.
