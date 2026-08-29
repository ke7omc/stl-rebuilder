# Research: independent pipeline + harness design: station placement, matching, seams, end caps, fast paths, milestones, scorer contract, failure modes

_Verified research dump, 2026-08-28. Treat as reference, not gospel: re-verify any API against the installed package version before relying on it._

Dense design notes follow. All OCC names are OCCT 7.x as exposed by pythonocc-core / OCP (cadquery-ocp). NOTE up front: pythonocc-core & OCP wheels do NOT exist for Python 3.14 — pin a conda env at python=3.11 or 3.12 (`conda create -n stl2brep python=3.11 pythonocc-core trimesh shapely scipy gmsh -c conda-forge`). This is a hard environment constraint the agent must respect on day 0.

ARCHITECTURE DECISION (drives everything below): do NOT loft whole multi-loop sections. Decompose into independent single-loop solids: (a) ONE outer envelope solid lofted from outer loops over full length; (b) one solid PER HOLE-CHAIN (bore, each fin slot) lofted over that chain's axial extent, extended ±ε_cut axially past its true extent (and past motor ends when through-going); final solid = BRepAlgoAPI_Cut(outer, fuse(hole_solids)). This makes topology changes (fin slot appears, star point fades) local to one cutter solid instead of forcing zoned lofts of the entire section, and it makes ThruSections always see exactly one wire per station. Zoning (task item 2c) then only applies WITHIN a chain whose shape class changes discontinuously — rare; usually a chain is born/dies instead.

## 1. Station placement

- Uniform: stations z_k = z0+ε .. z1−ε, N from config; ε = max(2·stl_chord_tol, 1e-4·L).
- Banded: config list `[{z_min, z_max, dz}]`; overlapping bands → min dz wins; auto-band default: detect dome regions by |dA/dz| of outer loop area on a coarse pre-pass (dz=L/50); where |dA/dz|·dz_coarse > 0.02·A use dz_fine = dz_coarse/8, plus cosine clustering toward the tangent plane at each end: z_j = z_dome_start + h·(1−cos(jπ/2m)) so spacing →0 at apex.
- Adaptive (recursive bisection):
```
S = sorted coarse stations (uniform, N0≈32)
queue = adjacent pairs; while queue:
  (a,b) = pop; if b−a < dz_min: continue        # dz_min = max(4·chord_tol, L/5000)
  Sa, Sb = slice(a), slice(b); match loops (sec.2)
  refine if any:
    - loop-count or nesting-signature differs        (topology → also triggers sec.2 bisection)
    - matched pair: |A2−A1|/max(A1,A2) > 0.01
    - centroid shift > 0.02·sqrt(A/π)
    - 2D directed Hausdorff (both ways) between matched loops (each resampled M=256 pts arc-length
      from aligned seam, KDTree nearest) > tol_h = 2·chord_tol
    - predicted-midpoint error: slice at m=(a+b)/2, compare against linear interp of the two
      resampled loops; max dev > tol_h   ← this is the real fidelity test (catches domes where
      A varies smoothly but nonlinearly)
  on refine: insert m into S, push (a,m),(m,b); depth cap 10
```
- Dedup: after all insertions, `np.unique` with min gap dz_min/2; slicing is cheap (trimesh), so adaptive cost is fine even at thousands of slices.

## 2. Loop classification & correspondence

Slicing: `sec = mesh.section(plane_origin=[0,0,z], plane_normal=[0,0,1])` → Path3D; `planar, T = sec.to_planar()` → Path2D. **`planar.polygons_full` returns shapely Polygons with `.exterior` and `.interiors` already nested** — trimesh does the containment/nesting for you (even-odd tree). Depth-0 exterior = outer boundary (there should be exactly 1 for a grain; >1 outers at one z = error or a truly split grain — flag). Each interior ring = hole. Signed area via shoelake; force outer CCW, and when building hole CUTTER solids re-orient each hole ring CCW as its own standalone outer (`shapely.geometry.polygon.orient(p, sign=1.0)`).
For bulk slicing use `trimesh.intersections.mesh_multiplane(mesh, plane_origin, plane_normal, heights)` — vectorized, returns (list of 2D line arrays, 3D transforms, face indices); still need per-z polygon assembly (`trimesh.path.polygons.paths_to_polygons` / `Path2D` from entities).

Cross-station matching (adjacent stations i, i+1), holes matched to holes only:
- cost(j,k) = w1·‖c_j−c_k‖/D_ref + w2·|ln(A_j/A_k)| + w3·|θ_j−θ_k| (θ = angular position about axis, wrap-aware; D_ref = outer diameter; w=(1,1,2)); infeasible if ‖c_j−c_k‖ > 0.5·min_pitch (pitch = min angular gap between holes ·r) → cost=∞.
- `scipy.optimize.linear_sum_assignment` on the cost matrix; assignments with cost=∞ rejected → unmatched loops = birth/death events.
- Chains: connected sequences of matched loops over z → each chain becomes one cutter (or the outer envelope).

Topology-change localization: when loop count / nesting signature differs between z_a and z_b, bisect: slice midpoint, keep the half containing the change, until b−a < dz_min. Result: z_lo = last station with old topology, z_hi = first with new. Event plane z_e = (z_lo+z_hi)/2.
Zone/extent rules:
- Chain born at z_e (fin slot appears): its cutter solid spans [z_e − ε_cut, chain_end + ε_cut] if it should cut flush from a wall — BUT for a slot that starts mid-grain with a wall, the slot's fore face is real geometry: start the cutter loft at the first real station z_hi, then prepend a station at z_e with the SAME loop (copied, prismatic ε extension) and planar-cap it → the cutter's fore face lands at z_e. If instead the feature blends smoothly to zero (star point fading), end the loft with ThruSections `AddVertex` at the measured vanishing point (see sec.4).
- Chain shape-class change with same count (circle→star along z, continuous): NOT a topology event; one loft handles it — just ensure common M and seam (sec.3). Split into two lofted zones only if curvature/Hausdorff refinement bottoms out at dz_min and still fails (discontinuous step): then build zone solids sharing the exact same wire at the shared boundary station and `BRepAlgoAPI_Fuse` with `SetFuzzyValue(2·chord_tol)`, then `ShapeUpgrade_UnifySameDomain` to merge the coincident interface.

## 3. Twist/seam control

- Resample every loop of a chain to the SAME point count M (M=128 default; M=64·n_lobes for stars) at uniform arc length, starting at an aligned seam point, same winding direction (CCW in the z-plane, viewed from +Z).
- Seam anchor: compute r(θ) about the loop centroid, FFT; if dominant harmonic n≥2 with amplitude > 3·chord_tol (star/fin), seam angle θ0 = −phase(c_n)/n (locks to a symmetry feature, e.g. a valley) — robust across stations even as the shape morphs. Else (near-circular) θ0 = 0 (+X ray). Seam point = polyline∩ray from centroid at θ0. Track θ0 continuity along the chain; unwrap; if it drifts > π/n between adjacent stations, reuse the previous station's θ0 (prevents seam flips from FFT noise).
- Build each section as ONE closed periodic B-spline edge (single vertex at the seam) → ThruSections gets 1-edge wires with corresponding vertices; call `ThruSections.CheckCompatibility(False)` because we guarantee compatibility ourselves. Multi-edge wires (arc+line star wires) across sections are a twist/mismatch trap — only use them for constant-section extrudes (sec.5).
- Verification: after loft, slice the resulting BRep at 3 interior stations (`BRepAlgoAPI_Section` with a plane), compare against the input slice at same z; max dev > tol → report "twist/loft deviation at z=…". Also detect spiral: for each consecutive section pair, dot the seam-to-centroid vectors; drift accumulating monotonically = spiral.

## 4. End-cap closure

- Flat ends: slice at z0+ε, z1−ε; after fitting the section curve, translate the wire to exactly z0/z1 IF the local profile is prismatic there (|dA/dz| ≈ 0 over the last 3 stations) — recovers the ε of lost length exactly. Caps: `BRepOffsetAPI_ThruSections(True /*isSolid*/, ...)` auto-caps planar end wires with planar faces (verify with BRepCheck); else `BRepBuilderAPI_MakeFace(wire, True)` + `BRepBuilderAPI_Sewing(2·chord_tol)` + `BRepBuilderAPI_MakeSolid`.
- Domed ends: cosine-clustered dense stations (sec.1). Apex closure options ranked: (1) axisymmetric fast path (sec.5) → exact revolve, no issue; (2) `thru.AddVertex(BRepBuilderAPI_MakeVertex(gp_Pnt(cx,cy,z_apex)).Vertex())` as the terminal "section" → smooth closed nose; z_apex extrapolated from the last two stations' (r,z) by fitting r²-linear (sphere/ellipsoid-consistent) not r-linear; (3) stop at r_min = max(0.02·R, 5·chord_tol) and tiny planar cap — most robust for gmsh (degenerate apex edges from AddVertex sometimes produce sliver surface parametrization gmsh dislikes; scorer's gmsh gate decides).
- Degenerate-slice problem: slicing at/beyond the tangent plane returns nothing, or slivers with A→0 and garbage winding. Rules: discard any loop with A < A_min = (5·chord_tol)²·π; discard stations whose loop bbox min-dimension < 3·chord_tol; never place a station within ε of mesh z-extents; the apex is handled by extrapolation (AddVertex) not by slicing.

## 5. Primitive-detection fast paths

Per fitted loop (on the raw resampled points, before spline fit):
- Circle test: Taubin algebraic fit (or Kasa + one Gauss-Newton step); accept if max|r_i − R_fit| < 1.5·chord_tol AND rms < 0.5·chord_tol. STL vertices lie ON the true surface (tessellation is inscribed) so vertex fit is unbiased — no sag correction needed for the radius; do NOT fit to segment midpoints (those sag inward by R(1−cos(π/n))).
- Chain of circles, centers colinear with axis within 1·chord_tol, → axisymmetric path: build meridian polyline (R_k, z_k), simplify with Douglas-Peucker (tol = chord_tol) then greedy line/arc merging (fit arc to runs of ≥4 points, accept if residual < chord_tol); revolve: `BRepPrimAPI_MakeRevol(face_or_wire_profile, gp_Ax1(origin, gp_Dir(0,0,1)))` → exact cylinder/cone/sphere faces; volume error ~1e-9. This alone nails M1+M2 outer & bore.
- Constant-section run detection: consecutive stations whose loops match within 1·chord_tol after 2D alignment → collapse the run and `BRepPrimAPI_MakePrism(face, gp_Vec(0,0,run_len))` with an exact-arc/line wire (RDP + arc/line segmentation of the 2D loop). Nails M3 star body; huge robustness win vs lofting 100 identical sections.
- Fallback: periodic B-spline. Prefer approximation over interpolation to kill facet noise: `GeomAPI_PointsToBSpline(pts_array, DegMin=3, DegMax=8, Continuity=GeomAbs_C2, Tol=chord_tol)` then make periodic, or `GeomAPI_Interpolate(harray, /*Periodic=*/True, tol)` on a decimated (RDP at 0.5·chord_tol) point set. Cap control points (warn if >200/loop). Post-fit self-intersection check (sec.8 #7).
- Decision order per chain: revolve > prism > loft; mixed chains split at run boundaries and fused.

## 6. Milestone ladder — ground truth + gates

Ground-truth generator (shared): build analytic solid IN the kernel (pythonocc), record V_truth = `brepgprop.VolumeProperties(shape, GProp_GProps()); props.Mass()`, A_truth likewise (SurfaceProperties); tessellate `BRepMesh_IncrementalMesh(shape, lin_defl, False, ang_defl=0.3, True)` at controlled chord_tol (default lin_defl=0.5 mm on a 1 m radius / 10 m motor — units: WORK IN MM EVERYWHERE, so R=1000, L=10000); `StlAPI_Writer` (binary). Keep truth solid + truth mesh for the scorer. Each milestone module exposes `make(params) -> {stl_path, truth_shape, truth_mesh, V, A, expected_topology}`.

- M1 annular cylinder: Cut(MakeCylinder(1000, 10000), MakeCylinder(300, 10000)). V = π(1000²−300²)·10000 mm³. Gates: watertight input; result exactly 1 solid / 1 shell; BRepCheck_Analyzer.IsValid; |ΔV|/V < 0.05% (< 1e-5 if revolve path fired — report which path); symmetric surface deviation (sample 20k points on result faces via fine tessellation + KDTree vs truth mesh, and reverse) max < 1.2·chord_tol, p99 < 0.8·chord_tol; STEP write (AP214, `Interface_Static.SetCVal("write.step.unit","MM")`) → re-read (`STEPControl_Reader`, TransferRoots, OneShape) → IsValid, |V_reimport−V_result|/V < 1e-6; gmsh: import STEP, `Mesh.MeshSizeMax = R/10`, `generate(3)`, ≥1 3D element, min SICN (`gmsh.model.mesh.getElementQualities(tags,"minSICN")`) > 0.1. Face-count sanity ≤ 8.
- M2 + domed ends: outer = cylinder + 2:1 ellipsoid domes both ends (revolve a meridian: line + ellipse arc, `Geom_Ellipse` trimmed), straight bore R=300 through everything (so domes are on outer only). V from kernel. Extra gates: dev gate must hold IN dome regions specifically (scorer bins deviation by z, reports per-bin max); station count in dome ≥ 8; apex closure meshable by gmsh.
- M3 star bore: outer cylinder; bore = 6-point star, R_valley=250, R_tip=450, tip/valley fillet radii 30/40, built as exact 2D wire (lines + arc fillets via `ChFi2d_FilletAPI` or constructed arcs) extruded full length. Gates: |ΔV|/V < 0.1%; dev max < 2·chord_tol (fillet regions are the hard part — scorer reports argmax location, expect it at fillets); result face count < 100; optional C6 symmetry check (rotate result mesh samples by 60°, re-measure dev, must match).
- M4 finocyl: fore 60% = circular bore R=300; aft 40% = same bore + 8 fin slots (rectangular slot w=80, radial extent 300→700, rounded outer tips r=40), slots start at z_fin=6000 with a flat fore wall. Truth: Cut(outer, Fuse(bore_cyl, slot_prisms)). Extra gates: pipeline reports a detected topology event with |z_detected − 6000| < max(dz_min, 4·chord_tol); still exactly 1 solid; |ΔV|/V < 0.2%; gmsh ok (slot corners are the boolean stress test).
- M5 adaptive fidelity: M2 domes + M4 fins combined, run once with adaptive placement and a station budget; gates: all M2/M4 geometric gates pass AND N_stations ≤ 0.5·N_uniform_equiv (N_uniform_equiv = uniform count needed to hit the same dev gate, computed by the scorer with a bisection over uniform N — cache it), AND wall-clock < 120 s.
All milestones also gate on: no NaN/inf in any metric; runtime cap; the pipeline emitting its own machine-readable stage log.

## 7. Scorer output contract (per-iteration JSON, stdout + `score.json`)

```json
{"milestone":"M4","iteration":17,"pass":false,"stage_reached":"boolean_cut",
 "first_failure":{"check":"surface_deviation_max","value_mm":42.1,"threshold_mm":1.2,
   "location":{"z_mm":9700,"xyz_mm":[812,-233,9700],"region":"aft_dome","loop":"outer"},
   "hint":"max deviation 42.1 mm at z=9700 near aft dome apex; only 2 stations in z=[9500,10000]; densify stations toward apex or fix apex extrapolation"},
 "checks":[
  {"name":"input_watertight","pass":true,"value":true},
  {"name":"sections_extracted","pass":true,"value":214,"detail":"stations=214, loops: outer=214, holes chains=9"},
  {"name":"topology_events","pass":true,"value":[{"z_mm":6001.9,"kind":"chain_birth","count":8}],"expected":[{"z_mm":6000,"tol_mm":4}]},
  {"name":"solid_built","pass":true},
  {"name":"brep_valid","pass":true},{"name":"n_solids","pass":true,"value":1,"expect":1},
  {"name":"volume_err_pct","pass":true,"value":0.083,"threshold":0.2,
    "extra":{"V_truth_mm3":2.6412e10,"V_result_mm3":2.6390e10}},
  {"name":"surface_deviation","pass":false,"value_max_mm":42.1,"value_p99_mm":0.9,"value_rms_mm":0.31,
    "threshold_max_mm":1.2,"argmax":{"z_mm":9700,"xyz_mm":[812,-233,9700]},
    "by_z_bin":[{"z0":0,"z1":1000,"max":0.4},"..."]},
  {"name":"step_roundtrip","pass":null,"skipped":"prior failure"},
  {"name":"gmsh_tet","pass":null,"skipped":"prior failure"}],
 "metrics":{"n_stations":214,"n_faces":57,"runtime_s":38.2,"paths_used":{"outer":"revolve","bore":"loft_bspline","slots":"prism"}},
 "artifacts":{"step":"out/M4_it17.step","deviation_ply":"out/M4_it17_dev.ply","log":"out/M4_it17.log"}}
```
Rules: checks ordered cheap→expensive, fail-fast with `skipped` markers so the agent sees exactly the first broken stage; every failing check carries a `hint` string with a LOCALIZED cause (z, region name derived from truth metadata: fore_dome/cylinder/aft_dome/fin_zone, loop id) and one suggested remediation; numeric values always with units in key names (`_mm`, `_pct`); `stage_reached` ∈ {input, sectioning, matching, fitting, loft, boolean, validate, export, mesh}; exceptions are caught and reported as `{"stage_reached":X,"first_failure":{"check":"exception","value":"<type>: <msg>","traceback_tail":"last 5 frames"}}` — never let the harness see a raw crash. Scorer is a separate process from the pipeline (pipeline bugs can't corrupt scoring); scorer imports truth from the generator module, never from the pipeline.

## 8. Top 10 failure modes → mitigations

1. Non-watertight/degenerate input STL (real-world burnback meshes) → preflight: `mesh.merge_vertices()`, `trimesh.repair.fix_normals/fill_holes`; report `mesh.is_watertight`, euler number; if still open, slicing tolerance-close polylines (join endpoints < 3·chord_tol apart); hard-fail with the z and gap size otherwise.
2. Open section polylines at slice z coinciding with mesh vertices/edges (co-planar facet ambiguity) → jitter station z by +0.1·chord_tol and re-slice on failure (retry ×3 with growing jitter); never place stations at exact vertex z-values (snap-away pass against sorted unique vertex z's).
3. Sliver/degenerate loops near tangent planes (dome ends) → A_min and bbox-thickness filters (sec.4); ε-inset; apex via extrapolated AddVertex, never by slicing.
4. Loft twist/spiral → single-edge periodic splines, common M, arc-length param, FFT-phase seam with drift clamp, winding normalization; post-loft slice-back verification catches residual twist and reports z (sec.3).
5. Spline seam mismatch across stations (seam vertex of section k not corresponding to k+1) → seam continuity check before lofting: max ‖seam_k − seam_{k+1}‖ projected to 2D must be < local point spacing ·2; else re-anchor from previous station.
6. Boolean failures from tangent/coincident surfaces (bore cutter face coincident with end cap; slot cutter flush with bore wall) → extend all cutters ±ε_cut = 10·chord_tol beyond ends/walls so intersections are transversal; `BRepAlgoAPI_Cut.SetFuzzyValue(chord_tol)`; `SetRunParallel(True)`; on failure retry with 3× fuzzy; check `HasErrors()` and dump `DumpErrors`.
7. Self-intersecting simplified loops (over-aggressive spline smoothing collapses star fillets) → after fit, sample fitted curve at 4M points, shapely `LinearRing.is_valid` / `is_simple`; if invalid, halve approximation tolerance and refit (loop, floor at interpolation); also enforce min feature size check: fillet radius from truth ≥ 3·chord_tol else tessellation itself can't carry it (gate the generator, not the pipeline).
8. Loop mis-assignment across stations with many similar holes (8 fin slots) → angular-position term in cost with wrap handling; reject assignments moving a centroid > half the angular pitch; unmatched ≠ forced match — becomes birth/death; sanity: chains must be monotone in z with no gaps > dz_min·2.
9. Tolerance/unit mismatch STL↔BRep↔STEP → single global `chord_tol` config that derives EVERY other tolerance (fit tol, sewing tol, fuzzy, A_min, ε) as documented multiples; units fixed to mm end-to-end; STEP writer `write.step.unit=MM`; scorer verifies re-imported bbox matches truth bbox within 0.1% (catches m-vs-mm 1000× errors instantly — this WILL otherwise bite SpaceClaim import).
10. Apex/degenerate-edge surfaces unmeshable by gmsh (AddVertex closure) → scorer's gmsh gate is the detector; automatic fallback ladder in pipeline: revolve path → AddVertex nose → r_min planar micro-cap; also run `ShapeFix_Shape` + `ShapeUpgrade_UnifySameDomain(shape, True, True, True)` before export (merges the coincident faces booleans leave behind, which otherwise produce sliver mesh surfaces).
(Bonus 11: floating-point station duplicates from adaptive insertion → unique-with-min-gap; bonus 12: ThruSections `SetContinuity(GeomAbs_C2)` + `SetParType(Approx_ChordLength)` + `SetMaxDegree(8)` — defaults can give C0 kinks between segments that gmsh meshes badly.)

Prior art (known, no install needed): FreeCAD "Part: Shape from mesh"+sewing produces one planar face per STL facet — exactly the anti-pattern this pipeline avoids; gmsh `ClassifySurfaces`/`createGeometry` reparametrizes STL into meshable-but-not-CAD surfaces (not STEP-solid quality); commercial equivalents (Geomagic, SpaceClaim skin-surface) confirm the slice-and-loft strategy is the standard for extrusion/loft-dominant parts like grains; openMotor/proPEP define grain families (BATES, star, finocyl) analytically — useful cross-check for M3/M4 parameterization. Key OCC citations for the agent: `BRepOffsetAPI_ThruSections(isSolid, isRuled, pres3d)`, `.AddWire/.AddVertex/.CheckCompatibility(False)`; `GeomAPI_Interpolate(pnts, periodic, tol)`; `GeomAPI_PointsToBSpline`; `BRepPrimAPI_MakeRevol/MakePrism`; `BRepAlgoAPI_Cut/Fuse` + `SetFuzzyValue`; `BRepCheck_Analyzer`; `ShapeUpgrade_UnifySameDomain`; `brepgprop.VolumeProperties`; `STEPControl_Writer/Reader`; `BRepMesh_IncrementalMesh(shape, lin_defl, isRelative=False, ang_defl, parallel)`; trimesh: `mesh.section(...).to_planar().polygons_full` (shapely polygons with holes), `trimesh.intersections.mesh_multiplane`.