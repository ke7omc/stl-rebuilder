# HANDOFF — STL → STEP rebuilder (all milestones M1–M5 PASS)

Generated at iteration 24, commit `e0b10ca`, from a clean local re-run of
`harness/score.py --keep` for every milestone (values below are that run's numbers, all
`pass: true, progress: 1.0`). Regenerate any of this yourself with:

```
.venv/bin/python harness/score.py --milestone M3 --out out/score.M3.json --keep
```

`--keep` copies the pipeline's STEP/STL/log/report into the same directory as `--out` — that's
what populated `out/handoff/` referenced below.

## What this is

`rebuild.py` (delegates to `pipeline/`) takes a solid-rocket-motor burnback-surface STL and
reconstructs a true BRep solid (STEP): fits circular/non-circular cross-sections at a set of
axial stations, classifies revolve/prism/loft per z-range, builds boolean-cut solids, and
exports STEP + a validation report. `harness/` (frozen, tag `harness-frozen`) is the independent
scorer — it never trusts the pipeline, only checks its output against synthetic ground truth.

## Per-milestone results

| Milestone | Shape added | Path fired (outer / bore) | Stations used | Faces | Runtime (s) | Volume err % | Surf. dev. max / p99 (mm) | gmsh min SICN (n_tet) | STEP roundtrip |
|---|---|---|---|---|---|---|---|---|---|
| M1 | annular cylinder, axis-centered bore | revolve / revolve | 40 | 4 | 1.30 | 0.00032 | 0.0050 / 0.0050 | 0.297 (132,188) | 2.2e-14 |
| M2 | 2:1 ellipsoidal domes both ends, straight bore | revolve / revolve | 40 (10+10 dome-clustered) | 4 | 1.53 | 0.02022 | 0.349 / 0.263 | 0.237 (129,407) | 2.8e-14 |
| M3 | star-shaped bore (constant cross-section) | revolve / prism | 60 | 27 | 1.43 | 0.00239 | 0.365 / 0.338 | 0.187 (163,875) | 4.7e-14 |
| M4 | fin-slot bore, single circular→star topology event at z=6000mm | revolve / mixed | 80 | 44 | 1.92 | 0.01390 | 0.440 / 0.418 (fin_zone) | 0.219 (144,045) | 4.0e-14 |
| M5 | fins that stop before the aft end — TWO topology events (circular→star→circular) sandwiching one prism run, adaptive station placement | revolve / mixed | 40 (adaptive; a uniform pass would need 2048 to hit the same deviation gate — 51x efficiency) | 53 | 2.12 | 0.00594 | 0.358 / 0.301 (aft_dome) | 0.234 (139,886) | 7.0e-14 |

All values pass their scorer gates with comfortable margin (e.g. M5's binding gate, `gmsh_tet`,
is 0.234 vs a 0.1 threshold — 2.3x margin; the tightest gate anywhere is M4's surface deviation
at 0.44 vs 0.6mm, 1.36x margin). `surface_deviation` rms is not a separately gated metric — the
scorer's authoritative figures are `max_mm` and `p99_mm`, both in the table above (per-region
breakdown is in `out/handoff/score.M*.json` → `metrics.by_z_bin`).

## File paths

- **Truth STEP/STL** (independent ground truth, never touched by the pipeline):
  `harness/truth/M{1..5}.step`, `harness/truth/M{1..5}.stl`
- **Rebuilt output STEP for each milestone** (from this handoff's `--keep` run):
  `out/handoff/M{1..5}.step`
- **Pipeline's own validation report / log per milestone**: `out/handoff/M{1..5}.report.json`,
  `out/handoff/M{1..5}.pipeline.log`
- **Full scorer verdict per milestone** (all 14–17 checks, pass/fail/value/threshold):
  `out/handoff/score.M{1..5}.json`
- Note: `out/` and `*.step` are gitignored (see `.gitignore`) — these files exist on this
  machine's disk right now but are not committed. Regenerate with the command above if they are
  ever cleaned up.

## Exact `rebuild.py` command per milestone

```
# M1 — annular cylinder
.venv/bin/python rebuild.py harness/truth/M1.stl -o out/handoff/M1.step --axis z --sections 40 --chord-tol 0.5

# M2 — 2:1 ellipsoidal domes
.venv/bin/python rebuild.py harness/truth/M2.stl -o out/handoff/M2.step --axis z --sections 40 --chord-tol 0.5

# M3 — star bore
.venv/bin/python rebuild.py harness/truth/M3.stl -o out/handoff/M3.step --axis z --sections 60 --chord-tol 0.5

# M4 — single fin-slot topology event
.venv/bin/python rebuild.py harness/truth/M4.stl -o out/handoff/M4.step --axis z --sections 80 --chord-tol 0.5

# M5 — fin-slot sandwich (two topology events), adaptive stations
.venv/bin/python rebuild.py harness/truth/M5.stl -o out/handoff/M5.step --axis z --sections 40 --adaptive --chord-tol 0.5
```

(`--chord-tol 0.5` is `CHORD_TOL` from `harness/milestones.py`; the scorer invokes these same
args — see `MilestoneSpec.rebuild_args` in that file for the source of truth.)

## SpaceClaim checklist (per STEP file)

1. **Import** the STEP file (File → Open, or drag-drop). Use a units-neutral import — the files
   are in mm throughout (`pipeline/tol.py`, MISSION §5.4).
2. **Exactly one solid body** should appear in the structure tree — if SpaceClaim reports more
   than one body or any open/sheet bodies, that's a regression from what the scorer verified
   (`n_solids == 1`, `brep_valid` via `BRepCheck_Analyzer`); stop and compare against
   `out/handoff/score.M*.json`.
3. **Visually confirm the expected features are present** for that milestone: M1 plain bore, M2
   domed ends, M3 star bore, M4 one circular→star transition, M5 two transitions sandwiching a
   fin run that stops short of the aft dome (star bore does NOT reach the aft end on M5).
4. **Mesh it** (SpaceClaim's own meshing or hand off to your solver) — the scorer already proved
   each STEP tetrahedralizes cleanly in gmsh at min SICN ≥ 0.187 (well above the 0.1 quality
   floor), so a similar-quality mesh should be achievable in SpaceClaim without manual healing.
5. If SpaceClaim's own healing/repair tool reports anything, it is unexpected — the pipeline
   already runs `ShapeFix_Shape` + `ShapeUpgrade_UnifySameDomain` (with a fallback to the
   pre-unify shape if unify would invalidate it) before export; a clean OCCT-valid shape should
   need no further repair.

## Known limitations (read before pointing this at a real burnback STL)

- **Loop classification is exactly "one outer loop + one hole loop per station"** for the
  circular cases, generalized to "one outer + interleaved circular/non-circular hole runs" for
  M3–M5. A station with more than one independent bore loop (e.g. two separate holes side by
  side, not one star) is NOT handled — `pipeline/cli.py::_run` fails fast with a diagnostic
  stderr line (exit 4) rather than silently producing a wrong shape. A real motor with
  multiple independent perforations per cross-section needs the loop-matching stage
  (`linear_sum_assignment` across stations) that MISSION §5.2 step 4 describes but was never
  built — none of M1–M5 required it.
- **Non-circular fitting is a single family**: constant-cross-section prism (extruded star/fin
  polygon) via `pipeline/solids.py::build_prism_solid`. A true burnback surface where the
  non-circular cross-section's *shape* itself varies with z (not just scales) would need a loft
  path (MISSION §5.2's B-spline branch) that isn't built.
- **Adaptive station placement** (`--adaptive`, M5 only) clusters stations near topology events
  and dome apexes; M1–M4 use uniform placement. For a real STL with many/irregular features,
  start with `--adaptive` (it was 51x more station-efficient than uniform on M5 for the same
  deviation gate) but verify `dome_stations_min`/`adaptive_efficiency`-style checks manually —
  the scorer's gates for those are milestone-specific synthetic thresholds, not universal ones.
- **Seam-fuse tolerance (`seam_eps`) is NOT one global knob** — M5's two topology events (fore
  vs aft) tolerated different amounts of seam widening before either reintroducing spurious
  surface deviation or NaN-ing the deviation metric outright (see `PROGRESS.md` iter 21/22 log
  entries for the full investigation). If a real model shows a `gmsh_tet` sliver or a NaN
  deviation near a topology event, don't sweep `seam_eps` uniformly — bisect which seam and
  tune that one only.
- **No holes/gaps repair beyond 1–3 triangle fills.** `pipeline/io.py` fixes normals, merges
  vertices, and fills only tiny (1 tri / 1 quad) holes in the input STL, then fails with a
  reported gap size otherwise (MISSION §10 failure mode 1). A real scanned/exported burnback
  mesh with larger holes needs pre-repair (e.g. in a mesh tool) before this pipeline will accept it.
- **Runtime scales with topology complexity, not station count directly** — M5 at 40 adaptive
  stations (2.1s) is comparable to M1's 40 uniform stations (1.3s) despite far more geometry;
  M4's 80 uniform stations (1.9s) is the slowest of the five. All are far under each milestone's
  `runtime_cap_s` (120–600s), so there's headroom for a real, larger STL before runtime becomes
  a concern.

## What to try first on a real burnback STL

1. Run with `--adaptive --chord-tol 0.5` first (M5's config) — it's the most general path
   exercised (mixed circular/non-circular, two topology events) and the most station-efficient.
2. If `pipeline/cli.py` exits 4 (unsupported station shape), read the stderr diagnostic line —
   it names the station z and what it found. Most likely causes, in order of likelihood: (a)
   more than one independent bore loop at some station (not supported, see limitations above),
   (b) a non-circular loop that isn't a simple single polygon (self-intersecting fit — see
   MISSION §10 failure mode 7).
3. Check `out/<name>.report.json`'s `n_stations` and `topology_events_z_mm` against visual
   intuition for the real geometry — if events are missing or spurious, increase `--sections`
   or inspect `pipeline/stations.py`'s vertex-snap logic near that z.
4. There is no independent truth STEP for a real part, so validate manually: SpaceClaim checklist
   above, plus compare rebuilt volume (STEP mass properties) against a CAD/CMM reference if one
   exists, and mesh it in gmsh or SpaceClaim to confirm no slivers before handing it to a solver.
