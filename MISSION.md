# MISSION — STL burnback → true BRep solid (STEP), built by an autonomous loop

You are the coding agent inside an unattended, self-correcting loop. A driver script
(`loop.py`) invokes you fresh each iteration, then **independently** scores the repo with a
frozen harness. Nobody is watching. Your only feedback channel is the scorer's JSON; your only
memory is the files in this repo and git history. Read this file every iteration.

## 1. The problem (why this exists)

Solid-rocket-motor propellant "burnback" geometries arrive as STL surface meshes. CFD models are
solids, so an STL is useless until it is rebuilt as a true solid. Previous attempts to slice the
STL and loft the sections failed: section polylines were hundreds of tiny segments, high-level
CAD lofts twisted or refused, and lofting two *surfaces* gave a lofted *surface*, not a solid.

**Deliverable:** a Python tool `rebuild.py` that converts an STL of a grain into a valid,
watertight, single-body BRep solid exported as STEP (mm, AP214) that a CAD/CFD tool can import
directly as a solid — proven against synthetic ground truth by a quantitative scorer.

## 2. Non-negotiable rules of engagement

1. **One focused change per iteration.** Pick the single highest-leverage thing the latest
   `out/score.json` points at. Do not refactor the world.
2. **Never edit frozen files.** `loop.py`, `loop.sh`, `driver/`, `PROMPT.md`, `MISSION.md`,
   `CLAUDE.md`, `.claude/`, and — after the M0 freeze — `harness/`. The driver restores them from
   git tags before every scoring run, so edits there are wasted and logged as violations.
3. **Never touch the scorer's verdict.** You may (and should) run the scorer yourself to check
   your work, but only the driver's run counts.
4. **Commit every iteration** with a message that states the milestone and the change
   (`M2: cosine-cluster stations toward dome apex`). Uncommitted work is auto-committed by the
   driver with a generic message — that is worse for the next iteration than your own message.
5. **Update `PROGRESS.md` every iteration** (see §8 format). It is the lab notebook that the
   next fresh-context iteration relies on. Record what you tried, the metric before/after, what
   you learned, and what to try next. Failed experiments are valuable — write them down so
   nobody repeats them.
6. **Stay inside this repo and its venv.** Use `.venv/bin/python`. No network, no system
   installs, no other directories, no `git push`.
7. **Units are millimetres end-to-end.** A single global `chord_tol` (mm) derives every other
   tolerance as a documented multiple. Never introduce an independent magic tolerance.
8. **Prefer deterministic code over cleverness.** Fixed RNG seeds, no time-dependent behavior,
   log every stage decision so a failure is diagnosable from the log alone.

## 3. Repository layout

```
MISSION.md PROMPT.md PROGRESS.md README.md CLAUDE.md     (frozen except PROGRESS.md)
loop.sh loop.py driver/                                  (frozen driver + prompts + hook)
docs/research/                                           (verified API research — read these)
rebuild.py                                               (thin CLI entry → pipeline.cli.main)
pipeline/                                                (YOUR PRODUCT — agent-owned)
  __init__.py  cli.py  io.py  stations.py  slicing.py  loops.py  fitting.py
  solids.py  booleans.py  export.py  report.py  tol.py
harness/                                                 (built in M0, then FROZEN)
  milestones.py  generators.py  metrics.py  meshcheck.py  score.py  selftest.py  truth/
tests/                                                   (your unit tests, pytest — not frozen)
out/  logs/  state/                                      (gitignored runtime artifacts)
.venv/                                                   (pre-installed; see §4)
```

## 4. Environment

`.venv` (Python 3.14, macOS arm64) is pre-installed with: `numpy scipy shapely networkx rtree
trimesh build123d gmsh pytest`. `build123d` brings `cadquery-ocp-novtk` (OCCT 7.9.3); low-level
OCCT is importable as `from OCP.<Module> import <Class>`. `gmsh` is the pip wheel with its own
bundled OCCT. If an import fails, fix the venv with `.venv/bin/pip install <pkg>` and record the
exact package/version in PROGRESS.md. Do not install anything outside the venv.

API references you must read before writing the corresponding module (they were verified
against the installed versions and contain exact signatures and traps):

- `docs/research/01-trimesh-slicing-fitting-metrics.md` — slicing (`mesh.section` keyword-only
  arg trap; `to_2D` with a user-supplied transform; `polygons_full`), circle fit, periodic
  B-spline fit, mesh-vs-mesh deviation metrics.
- `docs/research/02-brep-loft-step-gmsh.md` — `BRepOffsetAPI_ThruSections` (isSolid,
  `CheckCompatibility(False)`), wires from curves, faces with holes, booleans + fuzzy value,
  `BRepCheck_Analyzer`, `ShapeFix`/`UnifySameDomain`, STEP export, gmsh mesh check snippet.
- `docs/research/04-pipeline-design-notes.md` — the algorithm design this mission is based on:
  station placement, loop matching, seam control, end caps, fast paths, failure modes.

## 5. Architecture you will build

### 5.1 Decomposition (the key decision)
Never loft a whole multi-loop section. Decompose the grain into independent single-loop solids:

- **Outer envelope**: one solid built from the outer boundary loops over the full length.
- **One cutter solid per hole chain** (bore, each fin slot, each star lobe if it ever detaches):
  built over that chain's axial extent and extended ±`eps_cut` past ends/walls so booleans are
  transversal, never tangent.
- Final solid = `Cut(outer, Fuse(cutters))` → `ShapeFix_Shape` → `ShapeUpgrade_UnifySameDomain`
  → `BRepCheck_Analyzer` → STEP.

Topology changes along the axis (a fin slot appearing, a star bore ending) become chain
birth/death events localized by bisection — not zoned lofts of the entire section.

### 5.2 Stages
1. **io**: load STL with trimesh (`process=True`), `merge_vertices`, `fix_normals`; report
   `is_watertight`, `is_volume`, `body_count`, bounds. Rotate the user-specified motor axis
   (`--axis x|y|z` or a 3-vector) to +Z with a rigid transform; undo it before export.
2. **stations**: place slice stations along z: uniform → user bands (`--refine-bands`) →
   adaptive (`--adaptive`, recursive bisection on loop-count change, area/centroid change,
   predicted-midpoint interpolation error > 2·chord_tol). Always inset the first/last station
   by `eps_end`; never place a station at a z that coincides with a mesh vertex (snap away).
3. **slicing**: for each station, `mesh.section(plane_origin=..., plane_normal=[0,0,1])` →
   `Path3D.to_2D(to_2D=<one fixed transform for all stations>)` → `polygons_full` gives shapely
   polygons with holes nested. Filter slivers (area < A_min, bbox thickness < 3·chord_tol).
   On an open/garbage section, jitter z by +0.1·chord_tol and retry (×3).
4. **loops**: classify (exterior vs holes), enforce winding (outer CCW; holes re-oriented CCW
   when they become standalone cutter outlines), match loops between adjacent stations with
   `scipy.optimize.linear_sum_assignment` on a cost of centroid distance, log-area ratio, and
   angular position (wrap-aware); unmatched loops are births/deaths; bisect to localize the event
   plane. Output: chains (ordered lists of (z, loop) with a shape class per station).
5. **fitting**: per loop, on the raw dense ordered ring points:
   - circle test (Kasa + least-squares refine): accept if max|r_i − R| < 1.5·chord_tol and
     rms < 0.5·chord_tol;
   - else periodic B-spline: resample to a common point count M by arc length starting at an
     aligned seam (FFT-phase anchor for n-fold shapes, +X ray for near-circles; clamp seam drift
     between stations), consistent CCW winding, fit with `GeomAPI_Interpolate(periodic=True)` on
     a decimated set or `GeomAPI_PointsToBSpline` then close; verify the fitted curve is simple
     (sample densely → `shapely.LinearRing.is_simple`), refit tighter on failure.
6. **solids** (decision order per chain — cheapest exact path first):
   - all circles with centers on the axis → **revolve** an exact meridian
     (`BRepPrimAPI_MakeRevol`) after RDP + arc/line segmentation of the (R, z) polyline;
   - identical loops over a run → **prism** (`BRepPrimAPI_MakePrism`) of an exact wire;
   - otherwise **loft** with raw `BRepOffsetAPI_ThruSections(isSolid=True, ruled=?)`,
     one single-edge wire per station, `CheckCompatibility(False)`, `SetParType(ChordLength)`,
     `SetContinuity(C2)`, then verify by slicing the loft back at 3 interior z's and comparing
     to the input slices (this catches twist).
   - Loft fallback ladder if gates fail: (a) tuned smooth ThruSections → (b) ruled loft per
     adjacent station pair + `UnifySameDomain` → (c) **custom loft**: build skin surfaces
     yourself (`Geom_BSplineSurface` through the fitted section curves, or ruled surfaces per
     pair), sew with `BRepBuilderAPI_Sewing(tol)`, cap, `BRepBuilderAPI_MakeSolid`, fix
     orientation. Writing a geometry kernel from scratch is out of scope.
   - End caps: planar caps at the inset first/last stations, translated to the true end if the
     profile is prismatic there; domes via dense cosine-clustered stations and an extrapolated
     apex vertex (`ThruSections.AddVertex`) with fallback to a tiny planar micro-cap at
     r_min = max(0.02·R, 5·chord_tol) — the gmsh gate decides which is acceptable.
7. **booleans**: `BRepAlgoAPI_Cut` with `SetFuzzyValue(chord_tol)`, `SetRunParallel(True)`;
   on `HasErrors()` retry with 3× fuzzy; cutters always extended by `eps_cut = 10·chord_tol`.
8. **export**: `ShapeFix_Shape` → `ShapeUpgrade_UnifySameDomain(shape, True, True, True)` →
   `BRepCheck_Analyzer.IsValid()` → undo axis transform → STEP (AP214, `write.step.unit=MM`).
   Also write a fine tessellation of the result as STL (for the scorer) and a JSON stage
   report (`report.py`): stations used, chains, path chosen per chain, timings, warnings.

### 5.3 CLI contract (the scorer calls this — keep it stable)
```
.venv/bin/python rebuild.py <input.stl> --axis z --sections 40 \
    [--refine-bands "0:0.15:3x,0.85:1.0:3x"] [--adaptive] [--chord-tol 0.5] \
    -o out/<name>.step [--report out/<name>.report.json] [--stl out/<name>.result.stl]
```
Exit 0 on success, non-zero with a one-line reason on stderr on failure. `--refine-bands` is
`start:end:factor` in normalized axial fraction; overlapping bands take the finest spacing.

### 5.4 Tolerance table (`pipeline/tol.py`, all derived from `chord_tol`)
| name | value | used for |
|---|---|---|
| `eps_end` | max(2·chord_tol, 1e-4·L) | inset of first/last station |
| `eps_cut` | 10·chord_tol | cutter extension past ends/walls |
| `fuzzy` | chord_tol (retry 3×) | boolean fuzzy value |
| `sew_tol` | 2·chord_tol | sewing / custom loft |
| `A_min` | π·(5·chord_tol)² | sliver loop rejection |
| `circle_max_resid` | 1.5·chord_tol | circle acceptance |
| `dz_min` | max(4·chord_tol, L/5000) | adaptive bisection floor |
| `topo_tol` | max(dz_min, 4·chord_tol) | topology-event localization |

## 6. Milestone ladder and gates

The driver keeps the current milestone in `state/loop_state.json`. The scorer for milestone
`Mk` generates its ground truth analytically in the kernel, tessellates it to STL at a controlled
chordal deflection (the STL is the pipeline's *only* input), runs `rebuild.py`, and gates the
result. Universal gates on every milestone: exactly 1 solid; `BRepCheck_Analyzer` valid; STEP
re-imports with |ΔV|/V < 1e-6 and bbox within 0.1 % of truth; gmsh imports the STEP and produces
≥1 tetrahedron with min SICN > 0.1 and no `Error` log lines; runtime under the milestone cap;
no NaN/inf in any metric.

| M | Ground truth (mm) | Milestone-specific gates |
|---|---|---|
| **M0** | none — build the harness itself | `harness/selftest.py` exit 0; `harness/score.py --milestone M1` runs to completion and emits contract-valid JSON (pass or fail, not error). Then one Opus review pass, then the driver freezes `harness/`. |
| **M1** | annular cylinder: L=10000, R_o=1000, R_i=300 | volume error < 0.05 % (report which path fired; revolve should give ~1e-9); p99 deviation < 0.8·chord_tol, max < 1.2·chord_tol; face count ≤ 8 |
| **M2** | M1 + 2:1 ellipsoidal domes on the outer surface both ends, straight bore through | deviation gate must hold per z-bin including dome bins; ≥ 8 stations in each dome; apex closure meshable |
| **M3** | outer cylinder; 6-point star bore, R_valley=250, R_tip=450, tip/valley fillets 30/40, extruded full length | volume error < 0.1 %; max deviation < 2·chord_tol (argmax expected at fillets); face count < 100 |
| **M4** | finocyl: circular bore R=300 fore; 8 rectangular fin slots (w=80, radial 300→700, tip radius 40) aft of z=6000 with a flat fore wall | a detected topology event at z=6000 ± topo_tol; still exactly 1 solid; volume error < 0.2 %; gmsh passes at slot corners |
| **M5** | M2 domes + M4 fins combined | all M2 and M4 gates with `--adaptive`, AND stations used ≤ 0.5 × the uniform count needed to hit the same deviation gate (scorer computes this by bisection, cached), AND wall clock < 120 s |
| **HANDOFF** | — | write `HANDOFF.md` (§9); driver verifies it exists and cites every milestone's artifacts |

Default `chord_tol` for the truth tessellation is 0.5 mm; the scorer passes the same value to
`rebuild.py --chord-tol`. Gates are evaluated at that tolerance.

## 7. Harness contract (M0 builds this; it is then frozen)

`harness/milestones.py` — one dict per milestone: parameters, expected topology, region
labels along z (`fore_dome`, `cylinder`, `fin_zone`, `aft_dome`…), recommended `rebuild.py`
args, gate thresholds, runtime cap.

`harness/generators.py` — `make(milestone) -> Truth` builds the analytic solid in build123d/OCP,
records `V_truth` (`brepgprop.VolumeProperties`), `A_truth`, bbox, writes `harness/truth/Mk.step`
and `harness/truth/Mk.stl` (`BRepMesh_IncrementalMesh(shape, chord_tol, False, 0.3, True)` +
`StlAPI_Writer`), and returns them. Truth files are regenerated if missing (deterministic).

`harness/metrics.py` — volume/CoM/inertia from trimesh (gate on `is_volume`); symmetric
deviation: sample 100 k surface points on each mesh with a fixed seed **plus both meshes'
vertices**, `ProximityQuery.on_surface` both directions, report max/p99/rms, the argmax point,
and a per-z-bin table labelled with region names; STEP round-trip via `STEPControl_Reader`.

`harness/meshcheck.py` — the gmsh check from `docs/research/02-…md` §6 (import STEP,
`Mesh.MeshSizeMax = R/10`, `generate(3)`, count tets, min SICN, capture `Error` log lines).
Run gmsh in a **subprocess** so a crash inside gmsh cannot kill the scorer.

`harness/score.py` — CLI:
```
.venv/bin/python harness/score.py --milestone M1 --out out/score.json [--keep]
```
exit **0** = pass, **1** = fail, **2** = harness/internal error. It runs `rebuild.py` as a
subprocess with the milestone's recommended args and a timeout, then evaluates the checks
cheap→expensive, **fail-fast**, writing `out/score.json`:

```json
{"milestone":"M2","pass":false,"progress":0.64,"stage_reached":"validate",
 "first_failure":{"check":"surface_deviation_max_mm","value":42.1,"threshold":0.6,
   "location":{"z_mm":9700,"xyz_mm":[812,-233,9700],"region":"aft_dome","loop":"outer"},
   "hint":"max deviation 42.1 mm at z=9700 (aft_dome); only 2 stations in z=[9500,10000]; densify toward the apex or fix apex extrapolation"},
 "checks":[{"name":"input_watertight","pass":true,"value":true},
           {"name":"pipeline_exit","pass":true,"value":0,"stderr_tail":""},
           {"name":"n_solids","pass":true,"value":1,"expect":1},
           {"name":"brep_valid","pass":true},
           {"name":"volume_err_pct","pass":true,"value":0.011,"threshold":0.05},
           {"name":"surface_deviation_max_mm","pass":false,"value":42.1,"threshold":0.6},
           {"name":"step_roundtrip","pass":null,"skipped":"prior failure"},
           {"name":"gmsh_tet","pass":null,"skipped":"prior failure"}],
 "metrics":{"n_stations":40,"n_faces":12,"runtime_s":18.2,"paths_used":{"outer":"loft","bore":"revolve"},
            "by_z_bin":[{"z0":0,"z1":1000,"region":"fore_dome","max_mm":0.4}]},
 "artifacts":{"step":"out/M2.step","report":"out/M2.report.json","pipeline_log":"out/M2.log"}}
```
`progress` ∈ [0, 1] is **deterministic**: (index of first failing check + partial) / n_checks,
where partial = clamp(threshold / value, 0, 1) for lower-is-better numeric checks, else 0;
pass ⇒ 1.0. The driver uses it to detect stalls, so it must be monotone in "closer to passing".
Every failing check carries a `hint` with a localized cause (z, region, loop) and one concrete
remediation. Exceptions anywhere are caught and reported as a check named `exception` with the
last 5 traceback frames — the harness must never crash on a pipeline bug (exit 2 is reserved
for bugs in the harness itself).

`harness/selftest.py` — proves the harness is trustworthy **without** the pipeline: for each
milestone, generate truth; assert `V_truth` matches the closed-form value where one exists
(M1: π(R_o²−R_i²)L); score the **truth STEP itself** through the metric stack (must pass every
gate); score a deliberately perturbed copy (scaled by 1.01, and a version with the bore filled)
and assert it fails on the expected check (`volume_err_pct`) with a sensible hint; run gmsh on
the truth STEP. Exit 0 only if all of that holds.

## 8. PROGRESS.md format (append one block per iteration, newest at the top under `## Log`)

```
### iter 17 — M2 — sonnet/medium — 2026-08-29T14:02
- Score before: progress 0.64, first failure surface_deviation_max_mm=42.1 @ z=9700 aft_dome
- Change: cosine-cluster stations toward dome apex when |dA/dz| large (stations.py)
- Score after (my local run): progress 0.81, first failure gmsh_tet (apex sliver)
- Learned: AddVertex apex produces a degenerate edge gmsh rejects at chord_tol 0.5
- Next: try r_min micro-cap fallback for the apex
```
Keep a short `## Current state` section at the top (milestone, what works, known issues,
active hypotheses) and a `## Do not retry` list of things proven not to work and why.

## 9. HANDOFF.md (final milestone)

A human will read this and open the files in SpaceClaim. Include: a per-milestone table
(volume error, deviation max/p99/rms, gmsh min SICN, stations used, path fired per chain,
runtime); paths to each milestone's output STEP and truth STEP; the exact `rebuild.py`
command per milestone; a SpaceClaim checklist (import STEP → exactly one solid body → bore /
star / fins present → mesh); known limitations; and what to try first on a real burnback STL.

## 10. Known failure modes → mitigations (read before debugging)

1. Non-watertight input → merge vertices, fix normals, `fill_holes` (only 1-tri/1-quad holes);
   report `is_watertight`, Euler number; tolerance-join open polylines (< 3·chord_tol); fail with
   z and gap size otherwise.
2. Open sections at vertex-coincident z → snap stations away from unique vertex z's; jitter and
   retry on failure.
3. Sliver loops near tangent planes (dome ends) → `A_min` and bbox-thickness filters; apex by
   extrapolation, never by slicing.
4. Loft twist/spiral → single-edge periodic wires, common M, arc-length parameterization,
   FFT-phase seam anchor with drift clamp, CCW normalization, `CheckCompatibility(False)`;
   slice-back verification reports the z of residual twist.
5. Seam mismatch between stations → check ‖seam_k − seam_{k+1}‖ against local point spacing
   before lofting; re-anchor from the previous station on failure.
6. Boolean failure from tangent/coincident faces → extend cutters by `eps_cut`; fuzzy value;
   retry 3×; inspect `HasErrors()`.
7. Self-intersecting fitted loops (over-smoothed star fillets) → sample the fitted curve
   densely, `LinearRing.is_simple`; halve tolerance and refit; floor at interpolation.
8. Mis-assigned holes across stations (8 similar slots) → angular term in the cost; reject
   moves > half the angular pitch; unmatched ⇒ birth/death, never a forced match.
9. Unit/tolerance mismatch → mm everywhere; one `chord_tol`; scorer's bbox check catches
   1000× errors instantly.
10. Apex/degenerate surfaces gmsh cannot mesh → fallback ladder revolve → AddVertex nose →
    r_min micro-cap; always `ShapeFix` + `UnifySameDomain` before export.
11. Duplicate stations from adaptive insertion → unique with min gap dz_min/2.
12. C0 kinks between loft segments → `SetContinuity(C2)`, `SetParType(ChordLength)`,
    `SetMaxDegree(8)`.
13. trimesh `section()` positional-argument order changed → always call with keywords.
14. `Path3D.to_planar` was removed in trimesh 5 → use `to_2D`.

## 11. Loop modes you may be run in (the driver tells you in the prompt header)

- **normal** — one focused change (§2).
- **escalated** — same, but you are the stronger model because the previous iterations stalled;
  start by re-reading `## Do not retry` and the last 5 log blocks, then form a *different*
  hypothesis before touching code.
- **review-harness** (once, before the M0 freeze) — audit `harness/` for correctness and for
  ways a pipeline could pass without being right (gaming); fix what you find; run selftest.
- **tournament** — the milestone has stalled hard. Spawn 2–3 subagents in isolated git
  worktrees, each with a *named, different* strategy for the failing stage; each runs the scorer
  in its worktree; merge only the best-scoring branch into main; record why the others lost.
- **handoff** — write `HANDOFF.md` (§9). No code changes.
