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

**Deliverable:** a Python tool `rebuild.py` that converts an STL of a grain into valid,
watertight BRep solids — one per propellant body (usually one; several for segmented or
near-burnout grains) — exported as STEP (mm, AP214) that a CAD/CFD tool can import directly as
solids — proven against synthetic ground truth by a quantitative scorer.

**Round 1 (M0–M5, done 2026-08-29)** proved initial-burn grains on a z-axis motor centred on
the origin. **Round 2 (M6–M12, this round)** makes it survive what real burnback files look
like: the x axis (Fluent's axisymmetric solver requires it), an axis that does not pass through
the origin, inches, mid-burn bores whose cross-section changes along the axis (loft), end-of-burn
topology (blind and stepped bores, fins burned out, thin webs), several disconnected propellant
bodies, feature-aware station placement, and marching-cubes meshes full of skewed, unwelded
triangles. Round 3 will wrap the engine in a desktop GUI; keep the engine importable (no logic
that only lives in the CLI).

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
7. **Units are millimetres end-to-end.** Input units are declared (`--units mm|in|m`, default
   mm) and converted to mm immediately after loading; the STEP header always says mm. A single
   global `chord_tol` (mm) derives every other tolerance as a documented multiple of
   `chord_tol`, the axial length `L`, or a local radius — **never an absolute millimetre
   constant** (Round 1 left `50.0` mm, `200·chord_tol`, `80·seam_eps` in `cli.py`; they broke
   on any other motor size and must live in `tol.py` as scale-relative values).
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
trimesh build123d gmsh pytest scikit-image` (+ `scikit-fmm` if its wheel installed — otherwise
use `scipy.ndimage.distance_transform_edt` for signed distances; both are harness-only,
GPL-free). `build123d` brings `cadquery-ocp-novtk` (OCCT 7.9.3); low-level
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
.venv/bin/python rebuild.py <input.stl> --axis z|x|y|auto|"vx,vy,vz" [--origin auto|"x,y,z"] \
    --units mm|in|m --sections 40 [--refine-bands "0:0.15:3x,0.85:1.0:3x"] [--adaptive] \
    [--chord-tol 0.5] -o out/<name>.step [--report out/<name>.report.json] \
    [--stl out/<name>.result.stl] [--progress-json]
```
Exit 0 on success, non-zero with a one-line reason on stderr on failure — **and the report JSON
is written on every exit path** (§7.2). `--refine-bands` is `start:end:factor` in normalized
axial fraction; overlapping bands take the finest spacing. `--units` is explicit (never guessed:
a 10 in motor and a 10 m motor have identical numbers); the tool prints a plausibility warning
when the converted extent is < 20 mm or > 1e5 mm. `--progress-json` emits one JSON line per
stage/station on stdout (the GUI's progress channel). `-o` holds one STEP with N solids when the
input has N propellant bodies.

### 5.5 Round 2 requirements (what M6–M13 force you to build; hints, not code)
1. **Frame stage** (`pipeline/frame.py`): auto axis = principal direction of the surface-area-
   weighted covariance of the mesh, choosing the *distinct* eigenvalue (not the smallest — short
   fat grains flip); origin = centroid of outer-loop circle fits at ~5 coarse stations (a fin-
   biased mesh centroid is wrong). Rigid transform to +Z through the origin, undone before
   export; STEP always mm. Report `frame`.
2. **Axial-end detection** (`pipeline/ends.py`): coarse sweep (~200 sections); extent = first and
   last plane whose section area > `A_min`; classify each end flat / dome / pinch from dA/dz; the
   first fitted station is where the outer circle-fit residual settles under `circle_max_resid`,
   capped at 0.02·L — this replaces the hard-coded `200·chord_tol` inset. Never place a station
   outside the material. Report `axial_extent_mm`, `end_kinds`.
3. **Cavity decomposition** (`pipeline/loops.py`): per station, envelope = axisymmetric fit of the
   max-radius profile; cavity polygons = envelope disc − section (shapely). Holes, slots, and
   slots that have burned through to the outside are all cavity polygons, so a station with
   several outer polygons needs no special case. Classify, orient CCW, match adjacent stations
   with `linear_sum_assignment` (centroid, log-area, wrap-aware angle — research 04 §2); unmatched
   ⇒ birth/death ⇒ bisection (reuse the pattern of `_bisect_topology_event`); output chains. The
   outer chain goes through the same solids ladder as the cutters.
4. **Solids ladder per chain**: revolve (circles on axis) > prism (loops identical within 1·ct
   after seam alignment) > **per-window loft** — windows split at shape-class boundaries; inside
   a window resample loops to a common M from an FFT-anchored seam, one periodic single-edge wire
   per station, `ThruSections(isSolid=True)`, `CheckCompatibility(False)`, `SetParType
   (ChordLength)`, `SetContinuity(C2)`, `SetMaxDegree(8)`; **slice-back check** at 3 interior z's;
   on failure ruled per-pair + `UnifySameDomain`, then custom skin (§5.2 step 6). Never one global
   spline across a pinch and a cylinder (recorded negative result in `pipeline/solids.py`). Seam
   overlaps between windows follow the Round 1 discipline but live in `tol.py`.
5. **Multi-body**: `mesh.split()`; drop bodies with volume < 1e-6·V_max as noise islands (log
   them); run the stage pipeline per body; export one compound of N solids; `bodies[]` in report.
6. **Healing / minimum feature size**: tolerant `merge_vertices`, `fix_normals`, sliver-loop
   filter (`tol.a_min`, thickness < 3·ct — dead code in Round 1, make it live), ring-point dedupe
   at 0.1·ct; after build `ShapeFix_Shape(precision=ct)` → `ShapeFix_Wireframe.FixSmallEdges` →
   `UnifySameDomain`; enforce shortest edge ≥ max(0.1 mm, 2·ct); report `min_edge_mm`,
   `min_face_area_mm2` (Parasolid wants non-coincident vertices > 100× its 1e-8 m precision).
7. **Feature-aware stations** (`pipeline/stations.py`): `--sections` is a *budget*; coarse pass
   N0 = 32, then bisection where loop count/nesting changes, |ΔA|/A > 1 %, centroid shift >
   0.02·√(A/π), matched-loop Hausdorff > 2·ct, or predicted-midpoint error > 2·ct (research 04
   §1); cosine clustering only inside detected dome/pinch windows; extra density around events
   (±3·topo_tol) and fillet bands (dA/dz sign change); honour `--refine-bands`; dedupe at dz_min/2.
   The Round 1 doubly-composed cosine warp with `--adaptive` ignored cannot pass M8's gates.
8. **Report on every exit** (`pipeline/report.py`, written in a `finally`): §7.2 keys. Also the
   engine must be importable (`pipeline.engine.rebuild(opts, on_progress, cancel)`) — the CLI is
   a thin wrapper; Round 3's GUI calls the function, never the shell.
9. **Scale-relative constants** (all in `tol.py`, inputs ct, L, R_o): pinch snap → max(5·ct,
   0.02·R_o); inset → residual-driven (item 2); `min_dz = 5·ct`; seam overlaps named
   `tol.seam_overlap_circ/other`; quadratic-fit window caps in stations, not points.
10. **Performance**: pre-bin faces by z (interval tree) or `trimesh.intersections.mesh_multiplane`
    so a 4e6-face mesh slices in ~10 ms per station, not ~1 s.

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

### 6.2 Round 2 ladder (M6–M13, MR) — one new capability per rung, each a strict superset

Shared family L=10000, R_o=1000 unless stated; "ct" is the spec's `chord_tol`; `topo_tol =
max(4·ct, L/5000)`. Universal gates as above, except **"exactly 1 solid" becomes "exactly N
solids" (N from the spec)**. Truth SOLIDS are always analytic (booleans of primitives, ruled
lofts, `BRepFilletAPI_MakeFillet`); level-set / marching cubes is used **only to synthesise the
input mesh**, with gates scaled to the voxel size h (an exact-SDF marching-cubes surface deviates
≈ h²κ/8 on smooth faces but up to ≈ h/2 at sharp edges).

| M | Capability forced | Truth (analytic) and input pathology | Milestone gates (beyond universal) | `rebuild.py` args |
|---|---|---|---|---|
| **M6** | per-window **loft** | Cylinder minus a *ruled* loft between the M3 star (R_v 250 / R_t 450, fillets 30/40) at z=−10 and the same star scaled ×1.5 (375/675, fillets 45/60) at z=L+10 — scale linear in z; exact cones/planes; closed-form frustum volume L/3·(A0+√(A0A1)+A1). Clean tessellation. | volume < 0.2 %; dev max < 2·ct, p99 < 0.8·ct; face_count ≤ 200 (forces UnifySameDomain or a smooth loft); runtime < 300 s | `--axis z --sections 60 --chord-tol 0.5` |
| **M7** | loop classification + **cross-station matching**, N cutters, generalised topology events | Cylinder, flat ends; central bore R=300 through; 6 satellite perforations R=100 at r=600 every 60°, from z=0 to z=7000 with a flat end wall (6 chain deaths at 7000). Closed form V = π(1000²−300²)L − 6π·100²·7000. | volume < 0.1 %; dev max < 1.2·ct, p99 < 0.8·ct; `topo_events_z_mm = [7000]` within topo_tol with `topo_events_max = 2`; face_count ≤ 60 | `--axis z --sections 60 --chord-tol 0.5` |
| **M8** | **feature-aware adaptive stations** on a mid-burn grain | M5 capsule (2:1 domes) minus the M5 cavity *dilated* by web w=150: bore R=450 through; 8 obround slots half-width 190, outer radius 850, z∈[5850, 9650], end-edge loops filleted r=150. Selftest checks the offset property (samples on the M8 cavity are 150±1e-3 from M5's). | volume < 0.2 %; dev max < 2·ct, p99 < 0.8·ct; dome_stations_min 8; `station_bands` {fore_wall ≥ 10, aft_wall ≥ 10}; `n_stations_max` 80; `topo_events_z_mm` [5850, 9650], max 3; face_count ≤ 300 | `--axis z --sections 80 --adaptive --chord-tol 0.5` |
| **M9** | **noisy, skewed marching-cubes input** (scale-relative constants) | M8 truth reused. Input: exact narrow-band SDF on an anisotropic grid (10, 10, 40) mm → `skimage.measure.marching_cubes` ≈ 5.5e5 triangles, aspect ≈ 4 (equiangle skew > 0.9), Gaussian normal noise σ=0.5 mm (seed 7); `watertight_expected = True`. | as M8 but volume < 0.5 %; dev max < 0.75·h_z = 30 mm, p99 < 0.5·h_xy = 5 mm; topo tol = h_z; runtime < 600 s | `--axis z --sections 80 --adaptive --chord-tol 5` |
| **M10** | **frame normalisation**: `--axis auto`, off-origin, inches, small scale | M8 truth scaled ×1/40 (L=250, R_o=25, ct=0.0125), rotated so the motor axis is +x, translated (254, −76.2, 101.6) mm; truth STEP in that frame (mm); STL written in **inches**. | as M8 scaled; `frame_axis_err_deg` ≤ 0.1; `axial_extent_err_mm` ≤ 4·ct; bbox_err_pct 0.1 catches wrong units / undo-transform; runtime < 300 s | `--axis auto --units in --sections 80 --adaptive --chord-tol 0.0125` |
| **M11** | **multi-solid output** | Segmented BATES, 3 annular segments with flat ends: A z∈[0,3000] R_i 300; B z∈[3500,6500] R_i 450 (dual-grain); C z∈[7000,10000] R_i 300; one STL, 3 shells. Closed form. | `n_solids = 3`; `per_solid_volume_err_pct` < 0.05 (centroid-matched); volume < 0.05 %; dev max < 1.2·ct, p99 < 0.8·ct; face_count ≤ 24 | `--axis z --sections 40 --chord-tol 0.5` |
| **M12** | near-end-of-burn **cavity decomposition**: slots open to the dome, thin webs, multi-outer-loop stations | M5 capsule minus the cavity dilated by w=250: bore 550; obround slots half-width 290, outer r 950 (50 mm web), z∈[5750, 9750], end fillets r=250; aft slots break through the dome for z > 9656 (stations there have 8 disjoint outer polygons); bore exits the dome near z≈83 / 9917. | volume < 0.3 %; dev max < 2·ct, p99 < 0.8·ct; dome_stations_min 8; `station_bands` {fore_wall ≥ 10, breakthrough [9600, 9800] ≥ 10}; `n_stations_max` 120; `topo_events_z_mm` [5750, 9656, 9750] (max 5); `min_edge_mm` ≥ 0.1; face_count ≤ 400; runtime < 600 s | `--axis z --sections 120 --adaptive --chord-tol 0.5` |
| **M13** | **capstone: a real-STL-shaped input** | M12 truth rotated to +x, translated (2500, −700, 1300) mm, STL in inches. Input: isotropic h=8 mm exact-SDF marching cubes ≈ 3.9e6 triangles; normal noise σ=0.8 mm; **unwelded** (per-facet vertices ± 1e-5 in jitter); 2 % flipped facets; 3 noise islands (5 mm tetrahedra inside the bore); `watertight_expected = False`. | `n_solids = 1` (islands dropped); volume < 0.5 %; dev max < 1.5·h = 12 mm, p99 < 0.5·h = 4 mm; frame_axis_err_deg 0.1; axial_extent_err_mm 8; station_bands as M12; n_stations_max 120; topo events [5750, 9750] tol 8, max 5; min_edge_mm 0.1; face_count ≤ 400; runtime < 900 s, mesh_timeout 600 | `--axis auto --units in --sections 120 --adaptive --chord-tol 8` |
| **MR** | **real-STL slot** (`optional = True`) | No truth. The first `real_inputs/*.stl` (gitignored) + optional `real_inputs/<name>.json` {units, axis, known_volume_mm3}. When absent the scorer emits `pass: true, skipped: "no real input"` and selftest prints `[SKIP]`. | self-referential: pipeline_exit; n_solids ≥ 1; brep_valid; volume vs the repaired input mesh (or `known_volume_mm3`) < 0.5 %; deviation vs the input mesh p99 < 1.0·ct_est, max < 4·ct_est (ct_est = median edge length); step_roundtrip; gmsh; min_edge_mm 0.1; runtime < 1800 s | `--axis auto --units <json or mm> --adaptive --sections 120 --chord-tol <ct_est>` |
| **HANDOFF** | — | `HANDOFF.md` v2 (§9) covering every M above (MR may be "skipped") | — |

Why the ladder holds: M6 adds loft; M7 adds chains and N cutters (its loops are circles, so no
new loft stress); M8 adds station steering on an analytic mesh; M9 changes only the input; M10
only the frame; M11 only the body count; M12 only the cavity-decomposition topology; M13
integrates everything. Why cosine end-clustering cannot pass M8: at n=80 the Round 1 warp spaces
mid-barrel stations ≈ 310 mm apart, so a 300 mm feature band gets 0–1 stations, and reaching ≥ 10
with uniform spacing needs n ≈ 500 ≫ `n_stations_max`.

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

### 7.2 Round 2 harness extension (built under M0 again, then re-frozen after the review pass)

The Round 1 harness (M1–M5) is frozen at `harness-frozen` and **its truths, gates and numbers
must not change** — after every harness edit, `score.py --milestone M1..M5` on the current
pipeline must still report `pass: true`. Extend it as follows.

- **`MilestoneSpec` gains** `chord_tol` (default `CHORD_TOL`), `frame` (`axis` unit vector in
  input coordinates, `origin_mm`, `units ∈ {mm, in, m}`), `input` (`kind ∈ {analytic, voxel}`,
  `spacing_mm`, `noise_sigma_mm`, `unweld_jitter`, `flip_frac`, `islands`,
  `watertight_expected`), `station_bands: {label: min_count}`, `n_stations_max`,
  `topo_events_z_mm: list`, `topo_events_max`, `n_solids`, `optional`, `input_glob`. Every spec
  keeps `params["R_o"]` (gmsh `hmax = R_o/10`).
- **Truth cache**: `make(Mk)` writes/reads `harness/truth/Mk.json` (param hash, V, A, bbox, axial
  extent, frame, per-solid volumes and centroids) and reuses `Mk.step`/`Mk.stl`/`Mk.input.stl`
  when the hash matches — voxel inputs take minutes and must be generated once.
  `python -m harness.generators --warm` pre-generates everything. `Truth` gains
  `input_stl_path` (what the pipeline gets), `truth_mesh_path` (clean tessellation at ct/2,
  harness-only), `axial_lo/axial_hi`, `frame`, `n_solids`, `solid_volumes`, `solid_centroids`.
- **`harness/voxelize.py`** (harness-only; `scikit-image`, optionally `scikit-fmm`, else
  `scipy.ndimage.distance_transform_edt`): tessellate the truth at ct/2 → occupancy per z-plane
  via `mesh.section` + `shapely.contains_xy` on the xy grid → narrow-band (|d| ≤ 2h) exact
  distance with `metrics.point_mesh_distance`, sign from occupancy, far field ±2h →
  `marching_cubes(phi, 0, spacing)` → pathologies in a fixed order with a seeded RNG (normal
  noise, flips, islands, unweld + jitter, unit scale) → binary STL. Budget: M13 ≈ 1250×250×250
  float32, 3–8 min once; never run inside the scorer's timed section.
- **Scorer**: per-spec `chord_tol` (`DEVIATION_DEFLECTION = ct/2`); `_run_pipeline` copies
  `truth.input_stl_path`; `input_watertight` fails only when `watertight_expected`; before
  binning/deviation, transform truth and result meshes by `inverse(frame)` so `pts[:, 2]` is the
  canonical axial coordinate and regions/`dome_stations_min` use `axial_lo/axial_hi`; the
  report's `stations_z_mm` are axial mm from the input's fore end along `report.frame.axis`
  (flip if `dot(report.frame.axis, truth.frame.axis) < 0`). **New checks** (cheap→expensive,
  key-gated so M1–M5 plans are unchanged): `per_solid_volume_err_pct` (Hungarian match on
  centroids), `frame_axis_err_deg`, `axial_extent_err_mm`, `station_bands` (every band ≥ min;
  `len(stations_z_mm) == n_stations`; every station inside the extent), `n_stations_max`,
  `topo_events` (every expected z matched within tol **and** reported count ≤ max; keep
  `topo_event_z` for M4/M5), `min_edge_mm` (shortest edge over `TopExp` edges). `MR` skips with
  `pass: true` when no input exists. `progress` stays deterministic and monotone.
- **Report contract v2** (required keys; the Round 1 four stay): `status`, `exit_code`, `error`
  (null on success), `stage_reached`, `frame` {axis, origin_mm, units, scale_to_mm},
  `axial_extent_mm`, `end_kinds`, `bodies[]`, `stations[]` (z, n_outer, n_cavity, class, fit
  residuals, area, chain ids), `chains`, `topology_events_z_mm`, `paths_used`, `timings`,
  `warnings`, `min_edge_mm`. Written on **every** exit path.
- **Selftest additions**: `_BORE_FILLERS` for M6–M13 (M6/M7 solid cylinder; M8/M9/M10 capsule ∪
  bore, transformed for M10; M11 three solid cylinders; M12/M13 capsule ∪ bore); `_ideal_report`
  covers bands, events list, frame, extent; a "2b" mutation proving **each new gate bites**
  (60 uniform stations → `station_bands` fails; `n_stations` 10 000 → `n_stations_max`; 40
  events → `topo_events`; axis rotated 5° → `frame_axis_err_deg`; extent short by 50 mm →
  `axial_extent_err_mm`; one M11 solid scaled 1.01 → `per_solid_volume_err_pct`; a 0.01 mm sliver
  edge → `min_edge_mm`); truth-correctness checks (M6 frustum volume, M7/M11 closed forms,
  M8/M12 offset property, M9/M13 input triangle count and skew statistics, M10 bbox = scaled
  rotated M8); a **report-on-failure** check (run `rebuild.py` on a deliberately non-watertight
  copy; `report.json` must exist with `status`, `error`, partial `stations`); scaled and
  bore-filled copies still fail on `volume_err_pct` for every milestone; determinism unchanged.
  Keep the full selftest under 8 minutes with warm caches.
- **Tests**: `tests/test_score.py` (M1 plan by name) stays valid; add `tests/test_score_round2.py`
  (plan by name for M8, M11, M13) and `tests/test_voxelize.py` (a sphere's MC surface within
  0.1·h of R).
- Anti-gaming properties kept: `_truth_hidden` still hides `harness/truth/`; `not_truth_copy`
  unchanged; report-derived gates are cross-checked against the STEP; station and event counts
  are capped; MR cannot be made absent by the pipeline (the scorer looks in `real_inputs/`, never
  in the temp cwd).

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

**HANDOFF v2 (Round 2)**: the table covers M1–M13 and MR (mark MR "skipped" if no real input was
present); add a SpaceClaim checklist entry per body count (M11 = three solids) and for the
inch/x-axis cases (M10, M13: confirm the STEP is in mm and oriented as the input); a real-STL
runbook v2 (`--units`, `--axis auto`, how to read `frame`/`axial_extent_mm`/`bodies` in the
report, what a `status: failed` report tells you); and a section "what Round 3 (GUI) needs from
the engine" listing the `pipeline.engine` API and `--progress-json`.

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
15. Marching-cubes staircase / sliver triangles (Round 2 inputs) → fit tolerances relative to the
    voxel size (`chord_tol` = h), ring-point dedupe, sliver-loop filter; never trust a single
    section's noise — fit across stations.
16. Unwelded facets, flipped facets, noise islands → tolerant `merge_vertices`, `fix_normals`,
    `mesh.split()` + volume-fraction island drop; report what was dropped.
17. Wrong units or an axis away from the origin → explicit `--units`, `--axis auto`, origin from
    circle-fit centres; the scorer's bbox check catches a wrong undo-transform instantly.
18. Blind bores / stepped bores / slots breaking through a dome → cavity decomposition (§5.5 3):
    zero-hole and multi-outer-polygon stations are ordinary cases, not errors.

## 12. Round 3 (GUI) — what the engine must already provide

Round 3 wraps the engine in a PySide6 desktop app (built-in 3D viewport, macOS + Windows,
PyInstaller `--onedir`). It is specified when Round 2's HANDOFF is done, but the engine must be
ready for it now: `pipeline.engine.analyze(opts)` (frame, units plausibility, extent, median edge
length, body count — without building anything) and `pipeline.engine.rebuild(opts,
on_progress, cancel)` returning the report dict; no logic that lives only in the CLI; no `gmsh`
or GPL imports inside `pipeline/`; LGPL/BSD/MIT dependencies only in the shipped package.

## 11. Loop modes you may be run in (the driver tells you in the prompt header)

- **normal** — one focused change (§2).
- **escalated** — same, but you are the stronger model because the previous iterations stalled;
  start by re-reading `## Do not retry` and the last 5 log blocks, then form a *different*
  hypothesis before touching code.
- **review-harness** (before each harness freeze — once in Round 1, again after the Round 2
  extension) — audit `harness/` for correctness and for ways a pipeline could pass without
  being right (gaming); fix what you find; run selftest.
- **tournament** — the milestone has stalled hard. Spawn 2–3 subagents in isolated git
  worktrees, each with a *named, different* strategy for the failing stage; each runs the scorer
  in its worktree; merge only the best-scoring branch into main; record why the others lost.
- **handoff** — write `HANDOFF.md` (§9). No code changes.
