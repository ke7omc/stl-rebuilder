# Dashboard

The bottom dock is the instrument cluster: a mission clock, a status strip, five gauge dials, and
the log console. It exists because a large run sits silent for long stretches, and a frozen
progress bar and a frozen crash look identical.

The splitter between the dials and the log is draggable — give the log more room when you are
reading it, and give the dials more room when you are watching a long run.

## Mission clock

`T+ HH:MM:SS`, in the LED-style readout. It starts when any run starts and freezes when the run
finishes or fails. An Analyze that chains into a Run is **one mission** — the clock runs across
both rather than restarting.

## Status strip

| Word | Meaning |
|---|---|
| STANDBY | nothing running |
| RUNNING - STAGE | a run is in flight, in the named stage |
| NOMINAL | the last run finished |
| FAULT - KIND | the last run failed, with the failure kind |
| ABORTED | you cancelled the run |

`ABORTED` is deliberately distinguished from a fault. You stopped it; nothing broke.

## The five dials

| Dial | Phase |
|---|---|
| ANALYZE | `engine.analyze()` — load, repair, frame and scale detection |
| LOAD | the rebuild's own mesh load |
| SCAN | axis and feature scan |
| SECTIONING | station placement and per-station section extraction |
| BUILD | solid construction and STEP export |

Each dial has a needle, a progress arc, an LED percentage readout, and a caption naming what the
stage is currently doing. Idle dials are grey, the active one is instrument cyan, finished dials
are green, and a dial that failed is red.

**About the needle between checkpoints.** The engine reports progress at genuine checkpoints, and
some of those are far apart — on the largest test motor a single internal step accounts for most
of the run with one checkpoint in it. Rather than sit frozen there, the needle **creeps** forward
asymptotically toward a **93 % ceiling** and never moves backward.

That creep is an **is-alive indicator, not a measurement.** A creeping needle means the process is
still running; it is not claiming to know how far along it is. When the next real checkpoint
arrives, the needle snaps to it.

One behavior that looks like a bug and is not: if the engine runs a verify-and-refine retry pass,
the SECTIONING and BUILD dials **re-activate** rather than resetting to zero. The retry is a
continuation of the same mission, and the dials say so.

## Log console

Timestamped, monospaced, and colored by level — grey for information, amber for warnings, red for
failures. It carries the full detail the rest of the UI summarises: the exact settings each run
was launched with, the per-stage progress lines with their fractions, the Analyze pass/fail
checklist, and the full failure text.

When something surprising happens, the log is the first place to look, and it is the thing to copy
when reporting a problem.
