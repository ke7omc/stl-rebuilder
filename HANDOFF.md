# HANDOFF — STL → STEP rebuilder (all milestones M1–M5 PASS)

Written at iteration 25 on commit `d66d4aa`. Every number below comes from one clean local
`harness/score.py --keep` run per milestone (artifacts in `out/handoff/`, all
`pass: true, progress: 1.0`); M5 was additionally re-scored on this exact commit at handoff time
and reproduced identically. Nothing here is estimated.

## 1. Summary

`rebuild.py` takes a solid-rocket-motor burnback-surface STL and reconstructs a true BRep solid
(STEP): it slices the mesh at axial stations, fits each cross-section, classifies the bore as a
revolve (circular) or prism (constant non-circular) run, bisects the exact z of any
circular↔non-circular topology transition, builds and boolean-cuts the solid, and exports STEP
plus a JSON report. What is **proven** is that on five synthetic ground-truth motors of
increasing difficulty — annular cylinder, 2:1 ellipsoidal domes, 6-point star bore, 8-fin
finocyl with one topology event, and domes+fins with two events — the rebuilt solid is a single
valid BRep body within 0.02 % volume, ≤0.44 mm surface deviation, round-trips through STEP to
1e-13 mm, and tetrahedralizes in gmsh at min SICN ≥ 0.187 (gate 0.1), all in under 2.2 s.
What is **not proven** is anything on a real scanned/exported burnback STL: the pipeline has
only ever seen clean, watertight, axis-centred synthetic meshes with exactly one bore loop per
station and a bore whose non-circular cross-section is axially constant — the multi-loop,
lofted-cross-section, and mesh-repair paths described in MISSION §5.2 were never needed and are
therefore not built (see §6).

## 2. Results

| Milestone | Geometry | Path fired (outer / bore) | Stations | Volume err % | Dev. max (mm) | Dev. p99 (mm) | gmsh min SICN (n_tet) | Faces | Runtime (s) |
|---|---|---|---|---|---|---|---|---|---|
| M1 | annular cylinder, L=10000, R_o=1000, R_i=300 | revolve / revolve | 40 | 0.00032 | 0.0050 | 0.0050 | 0.297 (132,188) | 4 | 1.30 |
| M2 | M1 + 2:1 ellipsoidal domes both ends, straight bore | revolve / revolve | 40 (10 per dome) | 0.02022 | 0.3488 | 0.2251 | 0.237 (129,407) | 4 | 1.53 |
| M3 | 6-point star bore, constant cross-section, full length | revolve / prism | 60 | 0.00239 | 0.3655 | not gated | 0.187 (163,875) | 27 | 1.43 |
| M4 | finocyl: circular bore fore, 8 fin slots aft of z=6000 (one topology event) | revolve / mixed | 80 | 0.01390 | 0.4396 | not gated | 0.219 (144,045) | 44 | 1.92 |
| M5 | M2 domes + fins that stop before the aft dome (two topology events) | revolve / mixed | 40 | 0.00594 | 0.3584 | 0.2914 | 0.234 (139,886) | 53 | 2.12 |

Notes on the table:

- **rms deviation is not reported** — the frozen scorer emits `max_mm` and `p99_mm` only
  (`harness/metrics.py` computes no rms), so there is no measured rms to quote. Per-region
  breakdowns (fore_dome / cylinder / fin_zone / aft_dome) are in each
  `out/handoff/score.M*.json` → `metrics.by_z_bin`.
- **"not gated"** means MISSION §6 defines no `surface_deviation_p99_mm` gate for M3/M4, so the
  scorer does not emit the check. The worst per-region p99 measured anyway: M3 0.338 mm
  (cylinder), M4 0.418 mm (fin_zone).
- **Topology-event z accuracy:** M4 located its event at z=5999.99999 (error 1.3e-5 mm vs the
  true 6000.0, gate ±2.0 mm); M5 located both at 5999.99998 and 9499.99998.
- **STEP round-trip** (write→read→compare volume) was ≤7e-14 mm³ relative on all five, against a
  1e-6 gate.
- **Margins.** The tightest gate anywhere is M4's `surface_deviation_max_mm` at 0.440 vs 0.6 mm
  (1.36×). The next tightest is M3's `gmsh_tet` at 0.187 vs 0.1 (1.87×). Everything else clears
  by >2×. Runtimes are 1–2 s against per-milestone caps of 120–600 s, so there is ample headroom
  for a larger real STL.

## 3. Artifacts

All five milestones follow the same layout. `out/` is gitignored — these files are on this
machine's disk now but are **not** committed; regenerate any of them with the command in the
last column.

| Milestone | Rebuilt STEP (the deliverable) | Truth STEP | Truth STL (pipeline input) | Pipeline report JSON | Full scorer verdict |
|---|---|---|---|---|---|
| M1 | `out/handoff/M1.step` | `harness/truth/M1.step` | `harness/truth/M1.stl` | `out/handoff/M1.report.json` | `out/handoff/score.M1.json` |
| M2 | `out/handoff/M2.step` | `harness/truth/M2.step` | `harness/truth/M2.stl` | `out/handoff/M2.report.json` | `out/handoff/score.M2.json` |
| M3 | `out/handoff/M3.step` | `harness/truth/M3.step` | `harness/truth/M3.stl` | `out/handoff/M3.report.json` | `out/handoff/score.M3.json` |
| M4 | `out/handoff/M4.step` | `harness/truth/M4.step` | `harness/truth/M4.stl` | `out/handoff/M4.report.json` | `out/handoff/score.M4.json` |
| M5 | `out/handoff/M5.step` | `harness/truth/M5.step` | `harness/truth/M5.stl` | `out/handoff/M5.report.json` | `out/handoff/score.M5.json` |

Each milestone also has a `out/handoff/M*.pipeline.log` (the pipeline's own stdout).

The exact `rebuild.py` command that produced each STEP — these are `MilestoneSpec.rebuild_args`
from `harness/milestones.py`, which is the source of truth:

```
.venv/bin/python rebuild.py harness/truth/M1.stl -o out/handoff/M1.step --report out/handoff/M1.report.json --axis z --sections 40 --chord-tol 0.5
.venv/bin/python rebuild.py harness/truth/M2.stl -o out/handoff/M2.step --report out/handoff/M2.report.json --axis z --sections 40 --chord-tol 0.5
.venv/bin/python rebuild.py harness/truth/M3.stl -o out/handoff/M3.step --report out/handoff/M3.report.json --axis z --sections 60 --chord-tol 0.5
.venv/bin/python rebuild.py harness/truth/M4.stl -o out/handoff/M4.step --report out/handoff/M4.report.json --axis z --sections 80 --chord-tol 0.5
.venv/bin/python rebuild.py harness/truth/M5.stl -o out/handoff/M5.step --report out/handoff/M5.report.json --axis z --sections 40 --adaptive --chord-tol 0.5
```

To regenerate a milestone's whole artifact set (STEP + report + log + scorer verdict) in one go:

```
.venv/bin/python harness/score.py --milestone M5 --out out/handoff/score.M5.json --keep
```

(The scorer copies `harness/truth/` aside and feeds the pipeline a neutrally-named copy of the
STL, so the pipeline cannot read the answer. `--keep` is what populates `out/handoff/`.)

## 4. SpaceClaim checklist

Do this per STEP file, in order:

1. **Import** the STEP (File → Open, or drag-drop). The files are mm throughout — use a
   units-neutral import and confirm the overall length reads ~10000 mm, not 10 or 10,000,000.
2. **Confirm exactly one solid body** in the structure tree. The scorer verified `n_solids == 1`
   and OCCT `BRepCheck_Analyzer` validity on every file, so more than one body, or any sheet/
   surface body, means something changed during import — stop and compare against
   `out/handoff/score.M*.json`. If SpaceClaim's healing/repair tool flags anything, that is also
   unexpected: the pipeline already runs `ShapeFix_Shape` + `ShapeUpgrade_UnifySameDomain`
   (falling back to the pre-unify shape if unify would invalidate it) before export.
3. **Confirm the features are actually there** — this is the check that catches a plausible-
   looking but wrong solid, which volume alone will not:
   - M1: plain cylindrical bore, straight through, both ends flat.
   - M2: domed (2:1 ellipsoidal) outer ends, bore still straight through.
   - M3: 6-point star bore, same star cross-section the full length.
   - M4: circular bore forward, 8 fin slots starting exactly at z=6000 with a flat forward wall.
   - M5: domes **and** fins, with the star section starting at z=6000 and **ending at z=9500** —
     the bore returns to circular before the aft dome. If the star runs to the aft end, the
     second topology event was missed.
4. **Mesh it** with SpaceClaim/Fluent meshing. Each STEP already tetrahedralizes in gmsh at min
   SICN ≥ 0.187 against a 0.1 quality floor, so a comparable mesh should come out without any
   manual healing. Sliver elements clustered at one axial station are the signature of a seam
   fuse — see the seam note in §6.
5. **Compare the volume** SpaceClaim reports (mass properties) against the truth. Truth volumes
   are recoverable by importing the matching `harness/truth/M*.step`; the rebuilt solid should
   agree with it to the "Volume err %" column in §2 (worst case 0.02 %, i.e. 5 significant
   figures). A disagreement larger than that means the file is not the one the scorer graded.

## 5. Running it on a real burnback STL

**Recommended first command** (uniform stations — see the `--adaptive` warning below):

```
.venv/bin/python rebuild.py /path/to/burnback.stl \
    -o out/real.step --report out/real.report.json \
    --axis z --sections 80 --chord-tol <see below>
```

**Estimating `--chord-tol`.** It is the allowed chord-vs-arc error in mm when the pipeline
converts a sliced polygon into analytic geometry, and it should be set to roughly the STL's own
facet resolution — tighter than the mesh is meaningless, looser than the mesh throws away real
detail. The synthetic motors used 0.5 mm on a 1000 mm outer radius (5e-4 of the radius). For a
real file, measure the median triangle edge length and use about that:

```
.venv/bin/python -c "
import trimesh, numpy as np
m = trimesh.load('/path/to/burnback.stl')
e = np.linalg.norm(m.vertices[m.edges[:,0]] - m.vertices[m.edges[:,1]], axis=1)
print('median edge', np.median(e), 'p90', np.percentile(e,90), 'bbox', m.extents)
"
```

Start at the median edge length. If deviation looks too coarse in SpaceClaim, halve it; if the
run fails circle-fit checks on geometry you believe is round, loosen it — the circle-fit
residual threshold scales off `--chord-tol` (`pipeline/tol.py::circle_max_resid`).

**Set `--sections` from the geometry, not habit.** Stations are cosine-clustered toward both
axial ends (dense at the domes, sparse mid-barrel). Any feature that starts or stops partway
along the barrel needs enough stations either side of it for the topology-event bisection to
bracket it; 80 was sufficient for M4's single event, and M5 resolved two events with 40 only
because its features sit near the ends where the clustering is already dense.

**What to read in the report JSON** (`out/real.report.json`), in priority order:

- `topology_events_z_mm` — the z of every circular↔non-circular bore transition the pipeline
  found. Compare against what you know about the grain. Missing or spurious events are the
  single most consequential failure and will not show up as an obviously broken solid.
- `paths_used` — `{"outer": ..., "bore": ...}`. `revolve` = fitted as axisymmetric, `prism` =
  constant non-circular extrusion, `mixed` = both, split at the events above. If a bore you know
  is star-shaped reports `revolve`, the circle fit is accepting it and `--chord-tol` is too
  loose.
- `n_stations` and `stations_z_mm` — confirms the requested count survived and shows where the
  clustering actually put them relative to your features.

**The three most likely failure modes, and the knob for each:**

| Symptom | Cause | What to do |
|---|---|---|
| **Exit 3**: `input mesh is not a single watertight body (watertight=False, body_count=N)` | The STL has holes larger than the pipeline repairs, or is multiple shells. `pipeline/io.py` only fixes normals, merges vertices, and fills 1-triangle/1-quad holes. | No CLI knob covers this — it is a pre-processing job. Repair the mesh externally (MeshLab/Netfabb/`trimesh.repair`) until it is a single watertight body, then re-run. The message reports the actual body count and gap so you know how far off it is. |
| **Exit 4**: `expected 1 outer loop + 1 hole at z=..., got N outer / M holes (unsupported topology)` | A station has more than one independent bore loop (e.g. several separate perforations) — genuinely not implemented, see §6. | If it is a real multi-perforation grain, the pipeline cannot do it. If it is spurious (a thin web sliced into two loops by mesh noise), loosen `--chord-tol` and/or move stations off that z by changing `--sections`; the message names the exact z so you can check the slice. |
| **Exit 4**: `outer loop at z=... is not an axis-centered circle (max_resid=..., center=...)` | Either the outer envelope really is non-axisymmetric (not supported), or the part is not centred on / aligned with the axis you passed. | First check `--axis` (z is the default; the motor must be aligned to it and centred on it). If the axis is right and `max_resid` is only slightly over, raise `--chord-tol` — the residual threshold is derived from it. A genuinely non-axisymmetric outer surface is out of scope. |

Any other traceback exits **2** with the exception type and last 6 frames on stderr — the
pipeline never lets a raw crash escape.

Because there is no truth STEP for a real part, validation is manual: the §4 SpaceClaim
checklist, plus comparing the rebuilt volume against a CAD/CMM reference if one exists, plus a
gmsh or SpaceClaim mesh to confirm no slivers before it goes to a solver.

## 6. Known limitations

- **`--adaptive` and `--refine-bands` are accepted by the CLI but do nothing.** This is the most
  important caveat here, because the flag name implies otherwise. `pipeline/cli.py` calls
  `stations.uniform_stations()` unconditionally; both flags are parsed and discarded (see the
  module docstring of `pipeline/stations.py`). `uniform_stations` is not literally uniform — it
  applies a doubly-composed cosine warp that concentrates stations toward both axial ends, which
  is what clears M2/M5's `dome_stations_min ≥ 8` gate (10 per dome out of 40). M5's
  `adaptive_efficiency` result — 40 stations where a truly uniform sweep would need 2048 to meet
  the same deviation gate — is a product of that end-clustering, **not** of feature-aware
  adaptive placement. Consequence for a real part: stations cannot currently be steered toward a
  feature in the middle of the barrel; the only lever is raising `--sections` globally.
- **One outer loop + one bore loop per station is the hard limit.** Multiple independent
  perforations per cross-section are unsupported and fail fast (exit 4) rather than producing a
  wrong solid. Supporting them needs the cross-station loop-matching stage
  (`linear_sum_assignment`, MISSION §5.2 step 4) that no milestone required, so it was never
  built.
- **Non-circular bores must be axially constant.** The only non-circular path is a constant-
  cross-section prism (`pipeline/solids.py::build_prism_solid`). A bore whose non-circular
  *shape* changes with z — which is exactly what a mid-burn burnback surface looks like — needs
  the B-spline loft branch of MISSION §5.2, which is not implemented. M3/M4/M5 all have
  prismatic star/fin sections. **This is the largest gap between the proven envelope and a real
  burnback STL.**
- **The outer envelope must be an axis-centred circle at every station.** Non-axisymmetric
  outer surfaces (exit 4) are not handled.
- **Seam-fuse tolerance is not one global knob.** Where a prism run is fused to a revolve run at
  a topology event, the overlap is asymmetric and was tuned per seam: M5's fore and aft events
  tolerated different amounts of widening before either reintroducing surface deviation or
  NaN-ing the deviation metric entirely (iter 21/22 in `PROGRESS.md` has the full account). If a
  real model shows a gmsh sliver or a NaN deviation near a transition, bisect which seam is
  responsible and tune that one — do not sweep `seam_eps` uniformly.
- **Input mesh repair is minimal** — normals, vertex merge, and 1-triangle/1-quad hole fills
  only. Anything worse is rejected at exit 3 with the measured gap size.
- **Runtime tracks topology complexity, not station count.** M5 at 40 stations (2.12 s) costs
  more than M1 at 40 (1.30 s); M4 at 80 (1.92 s) is mid-pack. All five sit 60–300× under their
  caps, so a bigger real STL has room before runtime matters.

## 7. What to try next

In rough order of value for getting this onto real parts:

1. **Implement the loft path** (varying non-circular cross-section, MISSION §5.2 B-spline
   branch). This is the limitation most likely to be hit immediately by a real burnback surface.
   Note the prior negative result recorded in `pipeline/solids.py`'s module docstring: replacing
   the revolve meridian with a single global spline was tried and reverted — exact interpolation
   overshot to 20 % volume error, least-squares self-intersected at the dome pinch. Any loft work
   should be local and per-run, not a global re-parametrisation.
2. **Make `--adaptive` real** — feature-aware station placement (refine around detected topology
   events and curvature peaks) so mid-barrel features do not require inflating `--sections`.
   The flag and its plumbing already exist; only the placement logic is missing.
3. **Multi-loop bore support** via the `linear_sum_assignment` loop-matching stage, if real
   grains with several perforations are in scope.
4. **Add an rms deviation metric** to the scorer if rms is wanted for reporting — it currently
   computes max and p99 only. Note `harness/` is frozen at tag `harness-frozen` and the driver
   restores it before every scoring run, so changing it means re-tagging.
