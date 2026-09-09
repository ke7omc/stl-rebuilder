# Plan: M14 — multi-lobe dome breakthrough (N disjoint outer loops at a station)

_Implementation plan, 2026-09-08 (revised same day against the FINAL committed M14 geometry,
commit `75e8377`). Written from a full read of `pipeline/engine.py`, `pipeline/stations.py`,
`pipeline/slicing.py`, `pipeline/tol.py`, `harness/generators.py`, `harness/milestones.py`,
`harness/score.py`, `harness/selftest.py`, MISSION.md — plus direct empirical measurement of
`harness/truth/M12.stl` and the final `harness/truth/M14.stl` (regenerated from `75e8377`'s
spec: R_valley=550, island_clip_margin=30). Every number marked **measured** came from slicing
the real truth files with the pipeline's own `slice_station`, not from reading specs — the
spec's own derived z-values are provably wrong for gating (§1.3). Engine line references are as
of `df1803d` (`75e8377` touches only `harness/`); re-verify before editing — the quoted search
anchors are the stable thing._

**Audience: the implementation agent.** You have no memory of the conversation that produced
this. Every engineering decision is made below — do not re-open one without flagging to Brady.
The triggering incident: Brady ran the pipeline on a real near-burnout 6-point-star-grain motor
and hit `TopologyError: rebuild.py: expected 1 outer loop at z=14.5... (unsupported topology)`
at station 1 of 300. M14 (landed at `75e8377`, deliberately with only universal sanity gates —
its docstring says "a separate planning pass owns deciding how a FIX's station/topology
handling on this milestone should be graded"; that planning pass is this document) reproduces
the scenario exactly, and this plan makes the pipeline pass it — permanently, as a
regression-gated rung like M1–M13.

**Hard rules inherited from this project:**
- One capability, no regressions: M1–M13 must still pass after every phase (this project's
  regression gate exists because M5's fix once silently broke M4).
- `harness/` is tagged `harness-frozen` and `loop.py`/`MISSION.md` are `infra-frozen`. Editing
  them for this deliberate, human-approved extension is explicitly permitted for an interactive
  assistant (CLAUDE.md), but **both tags must be re-pointed after M14 is fully green** — final
  step, §7. The repo's Bash guard blocks `git tag` in Bash commands; do the re-tag via a script
  file, or leave it to Brady.
- `harness/selftest.py` is **green as of `75e8377`**, including M14: `_m14()` carries the
  universal sanity gates and `_BORE_FILLERS["M14"]`/`_make_bore_filled_m14` exists
  (`tests/test_selftest.py`, all 14 milestones + MR, passes). Phase D *extends* M14's gates
  (station/topology grading); after each harness edit, selftest must be brought back to green
  before moving on.
- Error messages must be actionable and context-aware (Brady's standing rule; see
  `_non_axisymmetric_hint`, `_axial_bounds_hint` for the house style).
- Use `.venv/bin/python`. No `git push` (Brady pushes).

---

## 0. Decisions made (summary — rationale in the sections below)

| Question | Decision |
|---|---|
| How the severed band's geometry gets built | **No new solid construction.** The severed islands emerge naturally from the existing boolean: outer envelope revolve (which follows the dome down to the flat clip cap) minus the full-length star/gear prism cutter (`_build_bore_prism_or_loft` already spans `z_min−eps_cut .. z_max+eps_cut`). This is exactly how M12's breakthrough already works ("the boolean cut naturally opens the slots through the dome surface... no special-case geometry needed"). The engine's only real gap is that the **station loop crashes before it ever gets there**. |
| Do islands get chained/lofted per-island (M7-style chains)? | **No.** The islands are the *complement* of the cavity, not holes; chaining or lofting 6 island solids and fusing them to the annulus would be a tangent-fuse nightmare and duplicates what the cut already produces. M7's chain machinery is the right mental model for the *event* (N↔1 is a topology event, localized by bisection like chain birth/death), not for the geometry. |
| New station class | `severed`: a station whose slice returns N ≥ 2 disjoint polygons, every one simply-connected (0 interiors). Also covers two degenerate cousins allowed only at the extreme ends: 1 simply-connected poly, and an empty slice (real-input robustness — the clipped M14 no longer produces either, see the thin-web row below). No fixed N. |
| Prior-history requirement | **None.** Classification is per-station geometry only, so the very first station being severed (Brady's crash; **measured** M14's first station at z=212.418) is handled identically to a mid-ladder one. A post-loop validation instead enforces *where* severed stations may occur (contiguous run at the fore and/or aft end of the sampled sequence); a severed run in the middle of the part stays a typed `TopologyError`, now with an actionable hint. |
| What a severed station contributes | An **outer-envelope sample** `(z, R_env)` for `outer_pts` (the islands' outermost arcs lie on the dome surface — **measured** on the final truth at z=200: circle fit of the max-radius points across all 6 islands gives R=799.840 vs true case radius 800.000, resid 0.290 < `circle_max_resid(0.5)=0.75`, center within 0.01 mm of the axis, 383 points kept) — plus its z in `all_zz`/the report. It contributes **nothing** to bore/hole processing. If the envelope fit fails its gates, the sample is skipped (no error) and the dome model interpolates. |
| Both ends | Yes, symmetrically — classification and the post-loop run validation are end-agnostic; the fore/aft dome models (`_dome_model(at_start=True/False)`) each consume whatever envelope samples exist on their side. No separate fore/aft code paths are added; the existing `at_start` parameterization already covers it. |
| Topology-event convention for M14 | **One event per severed end: the measured merge plane** (severed↔single-loop transition), localized by bisection on `len(polys) != 1`. The flat clip caps at the part's ends are the axial extent, *not* events — consistent with M2/M12 never counting a bore/dome pinch as an event. So M14 reports **2 events**: **measured** fore **250.422**, aft **9749.578** (canonical mm). The analytic breakthrough detector (engine.py ~2837–2862) is **suppressed per-end when that end has a severed run** — it would fire at ~the same z and duplicate the event. |
| The spec's descriptive values are wrong for gating | `_m14()`'s `topo_events_z_mm=[282.055, 9717.945]` and its `regions` fractions are both unusable as gate config (the spec's own docstring says they are descriptive only). Two independent errors: (a) they come from the **unfilleted** R_tip crossing — the star's tip fillet pulls the true severing plane **31.6 mm** inboard (282.06 predicted vs **250.42 measured**; `topo_tol` is only 2 mm); (b) `RegionBand` fractions are interpreted by `score.py::_region_at`/station_bands as fractions of the truth's **canonical bbox extent** `[112.418, 9887.582]`, not of `[0, L]` as `_m14()` computed them. Phase D replaces both with measured/recomputed values. |
| M14 gate set | **Extend the committed universal set, don't replace it** (Phase D table): keep n_solids=1, brep_valid, volume_err_pct=0.3, step_roundtrip, bbox_err_pct=0.1, face_count_max=100 (with a measured-adjustment rule); add surface deviation p99 < 0.8·ct / max < 2·ct, dome_stations_min 8, station_bands {fore_islands ≥ 6, aft_islands ≥ 6}, n_stations_max=300, topo_events (tol 2.0) = [250.42, 9749.58], topo_events_max=4. **Do NOT add `gmsh_min_sicn`** — its absence is a measured, deliberate decision by the geometry task (island tips are intrinsically thin, min SICN ~0.02 well clear of the clip; an honest property, not a defect). Keep the committed args `--axis z --sections 300 --adaptive --chord-tol 0.5` (300 was chosen with the R_valley=550 band-width tuning; see D.2). |
| Non-pinch dome ends (the clip caps) | The part **does not pinch** — at z_min the envelope is R≈631.8 vs the star valley ≈550, outside the 50 mm snap window, so `is_pinch_*` is correctly False and the flat-cap end is handled like M1's flat end. **But** the 100 mm `station_eps` end gap then gets bridged by a straight chord across real dome curvature — **measured geometry: ~12.8 mm mid-gap deviation from the true ellipse** (chord (112.42, 631.8)→(212.42, 818.0) vs ellipse 737.7 at z=162.4), an instant surface_deviation_max failure and a ~0.075 % volume term. Phase B therefore extends the existing `_densify_dome_chords` resample to fire on a *validated curved dome window at a non-pinch end*, not only on pinch ends. The earlier draft's "generalize the pinch target radius to the ring valley" idea is **dead — do not build it** (nothing pinches in M14; it only becomes relevant for Brady's real unclipped motors, §6 risk 4). |
| Thin-web / cusp degeneracy | **Resolved by the committed geometry, independently confirming this plan's earlier risk #1.** The truth is capped `island_clip_margin=30` mm short of each exact zero-width island pinch because the raw cusp is a valid-but-unmeshable BRep (gmsh "Invalid boundary mesh (overlapping facets)", reproduced across 5 fillet variants — topological, not tessellation quality). The rebuilt solid inherits the same property for free: its envelope ends at the mesh's own z-bounds (the clip planes), where islands are huge (**measured** min island area at z=112.45: ~11 361 mm²) — no cusp, no slivers, no `1e-6`-margin guard needed on this path. The `pipeline/solids.py` ~262–266 margin idiom stays the cited precedent if a *real-input* run ever pinches (risk 4). |
| GUI | **No changes.** Verified: `app/main_window.py::_station_rows` (line ~1425) classifies any loop count from slicing the rebuilt solid; `app/widgets.py::StationTable` renders whatever `n_loops` it's given; report fields are unchanged in shape. |

---

## 1. Root cause (verified, not hypothesized)

### 1.1 The raise site

`pipeline/engine.py::_rebuild_impl`, **lines 2400–2403** (anchor: the literal string
`"expected 1 outer loop at z="`):

```python
if len(polys) != 1:
    print(f"rebuild.py: expected 1 outer loop at z={zz:.3f}, got {len(polys)} "
          f"(unsupported topology)", file=sys.stderr)
    return 4
```

This is the **third statement of the main per-station loop** (`for _i, z in enumerate(zs):`,
line ~2390), immediately after `slice_station`. Exit code 4 maps to `TopologyError` via
`_EXIT_CODE_TO_EXC` (line 67; raised at line ~269 in `rebuild()`). **Reproduced on the final
M14 truth** (uniform, 40 sections, ct 0.5):

```
rebuild.py: expected 1 outer loop at z=212.418, got 6 (unsupported topology)
```

z=212.418 is exactly `z_min + station_eps` = 112.418 + 100.0 (line ~2360:
`station_eps = min(max(eps_end, 200*chord_tol), 0.02*L)`; 200·0.5 = 100 dominates over
eps_end and the 0.02·L≈195.5 cap) — i.e. the **first station**, matching Brady's "station 1 of
300, z=14.5" on his own (smaller) motor. This inset is also why the geometry task had to widen
the exposed band (R_valley 700→550): at 700 the band was ~89 mm, entirely swallowed by the
100 mm inset, and the crash never fired.

Two more per-station requirements would fail right behind it and are part of the same fix:
- line ~2416: `expected at least 1 interior hole at z=..., got 0` — islands are
  simply-connected;
- line ~2409: the outer circle fit + `_axis_centered` check — an individual island's exterior
  is an arc-sliver, nothing like an axis-centered circle.

### 1.2 Why "M12 already handles this" is false — and what M12's machinery actually is

MISSION §6.2's M12 row says "stations there have 8 disjoint outer polygons", which is what
prompted the hypothesis that multi-outer-loop machinery exists and merely fails to engage
without chain history. **Measured refutation**: slicing `harness/truth/M12.stl` at 64 z's
across [9600, 9917] (the whole breakthrough band and beyond), *every single section is ONE
polygon* — 1 interior everywhere except 9 interiors in z∈[~9731, ~9750] (the 8 slot lobes
detach from the bore near the slot end-fillet). M12's slots never sever a planar section,
because the slot's own meridian end-fillet (r=250, `slot_z_hi`=9750) pulls the slot's radial
reach below the local dome envelope everywhere the envelope has dropped below `slot_outer_r`.
The MISSION row's "8 disjoint outer polygons" describes the 3-D surface opening, not any
section the pipeline ever slices.

Consequently M12's "multi-loop handling" is exactly two things, neither of which slices a
severed station:

1. **Analytic breakthrough event detection**, engine.py ~2827–2862 (anchor: `# M12's dilated
   slot/bore cavity has a max radial reach`): solve the already-fitted dome model for the z
   where its envelope radius equals the cavity ring's max radius. The comment two lines down
   states the limitation outright: *"re-slicing exactly inside the breakthrough band would
   itself return >1 disjoint polygons, which the per-station loop above cannot handle."*
2. `_station_has_cavity_features` (line ~591) returning True for `len(polys) != 1` — used
   only by `_bisect_slot_zone_edge` to widen zone brackets.

So the refined root cause: **there is no multi-outer-loop station path anywhere in the
pipeline; M12 passes because its geometry never produces one.** Chain history is irrelevant —
the crash happens before any chain/dome code runs, and the dome-fitting functions
(`_dome_model`, `_fit_r2_quadratic`, `_refine_dome_model_from_vertices`, `_dome_shoulder_z`,
lines 307–528) never see the station at all. M14 is the first geometry whose severed band is
wider than `station_eps`, so stations land inside it — at both ends, starting with station 1.

### 1.3 The M14 geometry (measured off the final `harness/truth/M14.stl`, commit `75e8377`)

Construction (`harness/generators.py::_make_m14`; spec `harness/milestones.py::_m14()`): the
L=10000/R_o=1000 capsule (2:1 domes, dome_h=500) minus a **constant-cross-section** 6-point
star prism (R_valley=550, R_tip=900, fillet_tip=40, fillet_valley=50) spanning the full
length, then clipped by flat caps `island_clip_margin=30` mm inside each exact island pinch
(`z_clip_lo≈112.42`, `z_clip_hi≈9887.58`). The shrinking dome envelope does all the topology
work. (Minor doc nit to fix in passing in Phase D: `_m14()`'s docstring says "50 mm used here
for headroom" but the code sets 30.0 — the code and the measured bbox agree on 30.)

| Quantity | Measured value (final truth) |
|---|---|
| Solid bbox z (canonical) | [112.418, 9887.582]; mesh identical; extent 9775.16 |
| Fore severed band | z ∈ [112.42, **250.422**] — 6 islands, 0 interiors each (~138 mm wide) |
| Aft severed band | z ∈ [**9749.578**, 9887.58] — mirror image |
| Single-loop region | z ∈ [250.42, 9749.58] — 1 poly, 1 non-circular interior (the star "gear") |
| Behavior at the clip cap | z=112.45: still 6 full islands, min area ≈ 11 361 mm² — no slivers, no empty slices anywhere (the clip removed the degeneracy) |
| Envelope sample check (z=200) | max-radius-band circle fit: R=799.840 (true 800.000), resid 0.290, center (−0.010, −0.005), 383 pts |
| First station / crash | station_eps=100.0 → z=212.418, severed → `got 6`, exit 4 (reproduced) |
| Truth volume | 15 775 795 980 mm³ |
| Fillet effect on the severing plane | unfilleted R_tip crossing predicts 282.055; measured 250.422 — **31.6 mm off**, ≫ topo_tol=2. Never trust derived z's; measure (§5.1). |

Pipeline path implications, all already existing: every connected station classifies its one
hole as a non-circular central ring → `bore_rings`; `bore_pts` stays empty → the
`elif bore_rings:` branch at line ~3073 → `_build_bore_prism_or_loft` (line 1442), which for a
constant cross-section (star area span ≈ 0) takes `_build_prism_bore` — **M3's machinery — and
already extends the cutter over `z_min − eps_cut .. z_max + eps_cut`, straight through both
severed bands.** The envelope revolve minus that prism *is* the severed geometry, and both end
at the flat clip planes (`z_min`/`z_max`), so the rebuilt solid has the same
no-cusp/meshable-ends property the geometry task engineered into the truth.

---

## 2. Phase A — engine: severed-station classification (the crash fix)

All in `pipeline/engine.py`. This phase alone must take M14 from "crashes at station 1" to
"builds a watertight single solid".

### A.1 New pure classifier (place near `_station_has_cavity_features`, ~line 591)

```python
def _classify_severed_station(polys, chord_tol):
    """Given slice_station's polys for one station, decide whether this is a 'severed' section:
    the annulus cut into disjoint simply-connected islands by an end-of-burn breakthrough
    (M14; MISSION §6.2). Returns either None (not severed — caller applies the normal
    single-loop rules/error) or a dict:
        {"n_islands": int, "env": (Ro, max_resid) or None}
    Rules (decided, do not relax):
      - N >= 2 polygons, EVERY one with 0 interiors -> severed.
      - N == 1 polygon with 0 interiors -> severed-candidate too (a real scan's sliver filter
        can eat all but one island right at a tip; the clipped M14 never produces this, but
        Brady's unclipped real motors can); the post-loop position validation (A.4) is what
        keeps this from silently accepting a genuinely unsupported bore-less part.
      - Any polygon with interiors alongside others -> None (mixed topology, unsupported as
        before).
    env: fit_circle_robust over the union of all islands' exterior points restricted to
    r >= r_max - 2.0*tol.circle_max_resid(chord_tol); accepted only if max_resid <=
    tol.circle_max_resid(chord_tol) AND _axis_centered(...) AND >= 12 points survive AND the
    kept points' angular extent covers >= pi radians -- an ill-conditioned quarter-arc fit
    must not feed the dome model. On rejection env is None; the station still counts, it just
    contributes no radius."""
```

The 2.0×`circle_max_resid` keep-band and the residual/center gates reuse the exact tolerances
the normal outer-loop fit uses (lines 2408–2411), so a severed station's envelope is held to
the same standard as a normal station's outer loop. (Measured on the final M14 at z=200 these
gates pass with ~2.5× margin; §1.3.)

### A.2 Station-loop restructure (lines ~2390–2466)

Replace the `len(polys) != 1 → return 4` block with:

```python
severed = _classify_severed_station(polys, chord_tol) if <not the plain 1-poly-with-holes case> else None
if severed is not None:
    all_zz.append(zz)
    severed_idx[len(all_zz) - 1] = severed          # new dict, index-in-all_zz -> info
    if severed["env"] is not None:
        outer_pts.append((zz, severed["env"][0]))   # feeds the dome model like any station
    continue                                        # no interior-hole processing
if len(polys) != 1:
    ... existing error, now via _multi_loop_hint(zz, polys)   # A.5
```

Ordering guarantee: `zs` is sorted, so `outer_pts` stays z-ordered with mixed severed/normal
samples — `_fit_r2_quadratic`/`_dome_model` need no change. The `expected at least 1 interior
hole` check (line ~2416) is untouched — it now only ever sees non-severed stations, which is
exactly its old contract.

Also handle the empty slice: the current `if not polys: return 4` (line ~2398) becomes: if the
station is *end-adjacent* — i.e. every station between it and the nearer end of `zs` (there
are none for the first/last station) is itself severed-or-empty — record it as a degenerate
severed station (`n_islands=0, env=None`) and continue; otherwise keep the error. This is a
**real-input robustness guard, not an M14 requirement**: the clipped M14 slices 6 full-size
islands right at its cap (§1.3), but Brady's real motor meshes end at the true burnthrough
pinch where near-`a_min` slivers and empty retried slices are exactly what a first/last
station can hit. Mid-part empty slices still error exactly as before.

### A.3 What severed stations must NOT touch

Verified by reading each consumer; the `continue` in A.2 gives most of this for free, but two
places index `all_zz` positionally and need the severed indices excluded from *adjacency*:

- **Ring-chain adjacency** (line ~2568, anchor `zz_index[z] != zz_index[lz] + 1`) and the
  redundant-chain check `_bore_rings_z` neighbors (lines ~2598–2605): build the index maps
  over the **connected subsequence** (`[z for i, z in enumerate(all_zz) if i not in
  severed_idx]`). For M14 this is a no-op (severed runs are at the ends, sat/ring chains
  mid-span), but it keeps a severed station from splitting a chain into two + spurious events
  on some future geometry.
- **Chain end extension**: everywhere `i_first == 0` / `i_last == len(all_zz) - 1` means
  "reaches the part end, extend to `z_min−eps_cut`/`z_max+eps_cut`" (sat cutters ~2513–2521,
  ring chains ~2643–2652), the test becomes "first/last **connected** station, with only
  severed stations beyond it" — the cavity does not die at a severed neighbor, it *broke
  through*; bisecting a death there would carve a false end into the cutter. For M14's own
  path (`_build_bore_prism_or_loft`) no change is needed — it already spans full length — but
  the mixed `bore_rings and bore_pts` branches share these idioms and get the same treatment
  so a future slotted-and-severed part doesn't regress silently.

### A.4 Post-loop validation (insert right after the station loop, before chain matching)

```python
# Severed stations are only supported as contiguous runs touching the fore and/or aft end of
# the sampled sequence (end-of-burn breakthrough at a dome tip, M14). Anything else is a
# genuinely unsupported topology -- keep the typed exit-4, with a hint.
```
Compute `fore_run` = maximal prefix of indices in `severed_idx`, `aft_run` = maximal suffix.
If `severed_idx` ⊄ `fore_run ∪ aft_run` → print an actionable error (mid-part severed band:
"the cross-section at z=… splits into N disjoint islands in the middle of the part — only
end-of-burn breakthrough at the dome tips is supported") and `return 4`.
If **every** station is severed (no connected station at all) → error: no bore/cavity can be
reconstructed ("every station is disjoint islands — no connected cross-section to fit a
cavity from; try more --sections or check --axis").
Also require ≥ 2 connected stations with `bore` content (the existing downstream code paths
already effectively require this; fail early with a clear message instead of deep in
`_build_prism_bore`).

### A.5 Actionable hint for the residual error path

Replace the bare two-line message at 2400–2403 with `_multi_loop_hint(zz, polys, chord_tol)`
(house style of `_non_axisymmetric_hint`, line 280): report N, whether the islands are
simply-connected, and — since after this plan the supported case is handled — say what the
*unsupported* remainder means: mid-part severing → "not an end-of-burn breakthrough; if this
is a multi-body input check the mesh, if it is a real mid-grain feature this topology is not
yet implemented". Keep exit 4.

---

## 3. Phase B — non-pinch dome ends: resample the station-inset gap

The dome model gets its data from Phase A's envelope samples. What remains is the endpoint
logic around lines 2774–2898 — and the requirement here changed materially with the final
clipped geometry, so read this carefully:

### B.1 The part does not pinch — and must not be made to

At z_min the envelope radius is ≈631.8 (R_case at the clip plane) while the star ring's
valley radius is ≈550: `is_pinch_start` (line ~2868, `bool(bore_pts) and abs(...) < 50.0`) is
False twice over (`bore_pts` is empty AND the radii differ by ~82 mm), and **that is correct**
— the truth ends in a flat cap, exactly like M1's flat ends. The earlier draft's
`_end_cavity_radius` pinch-target generalization is **not to be built**: there is no pinch to
snap to, and inventing one would pull `z_min`/`z_max` off the real clip planes and fail
`bbox_err_pct`. (It resurfaces only for real unclipped motors — §6 risk 4, follow-up, not this
plan.)

### B.2 But the 100 mm end gap now crosses real dome curvature un-resampled

With no pinch, `_densify_dome_chords` never runs, and the profile bridges from the endpoint
`(z_min, r_start≈631.8)` (via `_extrapolate_end`'s quadratic — the endpoint *radius* is
accurate) straight to the first station `(212.42, ≈818.0)` with one chord. **Measured
geometry: that chord sits ~12.8 mm inside the true ellipse at mid-gap** (ellipse R(162.4) =
737.7 vs chord 724.9) — an instant `surface_deviation_max` catastrophe (gate 1.0 mm) and a
~0.075 % volume bite. This is precisely the "excluded inset band with no real station data"
case `_densify_dome_chords`'s docstring already describes for pinch ends.

**Change (decided)**: widen the resample trigger from `is_pinch_start/_end` to
`is_pinch_* OR _curved_end(...)`, where `_curved_end` is true when that end's dome model is
validated AND genuinely curved across the end gap:
- the fitted quadratic exists (`coef.size == 3`) with real curvature (`a` significantly
  nonzero), and
- `|R_model(z_min) − R_model(first fitted station z)| > resid_tol` (the model itself predicts
  more radius change across the gap than the fit tolerance).

When `_curved_end` fires without a pinch, run the same
`_densify_dome_chords(..., model=...)` resample over `[z_min, window_z]` and keep the
endpoint value `r_start` from `_extrapolate_end` (no snap — nothing to snap to; the existing
`fore_gap[0] = (z_min, r_start)` line does this naturally). Flat-ended parts are provably
unaffected: M1/M3/M4/M11's outer profiles are cylinders (`a ≈ 0`, ΔR ≈ 0 → `_curved_end`
False), and every pinch-ended milestone (M2/M5/M8/M9/M12/M13) already takes the `is_pinch`
branch unchanged. Add `curve_windows` for the resampled band exactly as the pinch branch does
(line ~2889), so `build_revolve_solid` treats it as a curved window.

### B.3 What the multi-loop region's "dome cap" IS (the design question, answered)

Decision: **the dome model fits the ENVELOPE of the islands — there is no separate multi-loop
end handling.** The severed band is not a special cap: the outer surface there is the same
ellipsoidal dome the connected dome band has, just with the cavity open through it. Phase A's
envelope samples make the quadratic-in-R² fit see it as ordinary dome data (and
`_refine_dome_model_from_vertices` already refines against mesh vertices in the window — the
island outer-arc vertices lie exactly on the dome, the star-flank vertices are excluded by its
4·chord_tol seed band). The station-free band that the dome-cap-fit viewport layer shows
shrinks accordingly, since real stations now exist deep into the dome; no `station_eps` change
is made.

---

## 4. Phase C — topology events

### C.1 Measured severing events

New helper `_bisect_severed_edge(mesh, z_severed, z_connected, chord_tol)` — clone of
`_bisect_slot_zone_edge` (line 607) with the predicate `len(polys) > 1` (use `> 1`, not
`!= 1`, so a degenerate empty midpoint slice shrinks toward the connected side instead of
oscillating). For each of `fore_run`/`aft_run` that is non-empty, bisect between the run's
inner-most severed station and the adjacent connected station → one event z per severed end.
Append both to the event list that feeds `topo_events_z_mm` (line ~3310, anchor
`topo_events_z_mm = sorted(`), as a new `severed_events` term.

### C.2 Suppress the analytic duplicate

The analytic breakthrough block (lines ~2837–2862) fires for M14 too (its precondition —
cavity ring max radius exceeding the dome envelope inside the model's domain — is precisely
the severing condition), landing near the measured event. Per end, **skip the analytic solve
when that end has a severed run** (the measured plane is strictly better evidence). M12 (no
severed stations, ever) keeps its analytic event untouched — this is what protects M12's
`topo_events_z_mm=[5750, 9656, 9750]` gate from any change.

### C.3 Expected M14 events

`[250.42, 9749.58]` canonical (engine z minus `axial_origin_z`; M14's frame is canonical and
its fore dome model still solves an apex near z≈0, same mechanics M12's gate already
exercises — verify `axial_origin_z` lands near 0 on the first real run, since the model is now
fit partly from severed-band samples). `topo_tol = max(4·ct, L/5000) = 2.0` mm; the engine's
bisection on the ct=0.5 tessellation lands well inside that.

---

## 5. Phase D — harness/MISSION/driver wiring (the frozen-infrastructure edits)

### D.1 Re-measure before wiring (do this first, every time the generator changes)

The truth is spec-hash-cached (`harness/generators.py::_spec_hash`); if anyone touches
`_m14()`'s params the numbers move (this already happened once: R_valley 700→550 shifted every
band edge, and the fillet moved the severing plane 31.6 mm off the spec's derived value). The
one-shot measurement script (run it, paste the numbers into D.2/D.3):

```python
# scratch script -- .venv/bin/python
import sys; sys.path.insert(0, ".")
import trimesh
from harness import generators
from pipeline.slicing import slice_station
t = generators.make("M14"); m = trimesh.load(str(t.stl_path))
zmin, zmax = float(m.bounds[0][2]), float(m.bounds[1][2])
def n(z): return len(slice_station(m, z, 0.5)[0])
def bisect(a, b):                      # a severed (n>1), b connected (n==1)
    assert n(a) > 1 and n(b) == 1
    for _ in range(40):
        mid = 0.5*(a+b)
        a, b = (mid, b) if n(mid) > 1 else (a, mid)
    return 0.5*(a+b)
# on the 75e8377 truth: 112.418 9887.582 250.422 9749.578
print(zmin, zmax, bisect(zmin+30, zmin+160), bisect(zmax-30, zmax-160))
```

### D.2 `harness/milestones.py::_m14()` — add the station/topology grading gates

The committed spec deliberately carries only universal sanity gates and says the grading pass
owns the rest — merge, don't replace:

- **Keep** `rebuild_args=["--axis", "z", "--sections", "300", "--adaptive", "--chord-tol",
  "0.5"]` — 300 is load-bearing: R_valley=550 was tuned so the exposed band clears the 100 mm
  inset *at these args*, and 300 mirrors Brady's real run. Set `n_stations_max=300` to match
  (the scorer ties `n_stations_max`/`station_bands` to the same reported list — see
  score.py's stations_consistent block ~890).
- **Keep** the committed gates (n_solids=1, brep_valid, volume_err_pct=0.3,
  step_roundtrip_vol_err=1e-6, face_count_max=100, bbox_err_pct=0.1). face_count_max=100 has
  never seen a passing rebuild; if the first honest green run exceeds it (the resampled dome
  windows + star prism cut through both domes may cost more faces than M3's ~97), raise it
  toward M12's 400 with a comment citing the measured count — gate config, not pipeline shame.
- **Add** `surface_deviation_p99_mm=0.4, surface_deviation_max_mm=1.0` (0.8·ct / 2·ct, M12's
  ratios), `dome_stations_min=8`, `station_bands=True`, `n_stations_max=True`,
  `topo_events=2.0` (= `_topo_tol(L, ct)`).
- **Do NOT add** `gmsh_min_sicn` — deliberate, measured omission by the geometry task (min
  SICN ~0.02 is intrinsic to thin island tips even well clear of the clip). Respect it.
- `topo_events_z_mm=[250.42, 9749.58]` (measured, D.1 — replaces the descriptive unfilleted
  values), `topo_events_max=4` (2 expected + headroom; an event-per-station gaming report
  still fails).
- `station_bands={"fore_islands": 6, "aft_islands": 6}` — the fore band a station can occupy
  is `[z_min+100, 250.42]` ≈ 38 mm; at 300 sections the doubled-cosine end clustering plus
  adaptive's loop-count-change anchors (`_detect_topology_anchors` sees the 6↔1 transitions
  in its coarse scan) should clear 6 comfortably. **Before locking it, measure the actual
  placement** (call `stations.adaptive_stations` on the truth mesh with the D.2 args and
  count stations in each band); if the honest draw brackets differently, tune the count —
  never widen `station_eps` to satisfy it (its 200·ct floor exists for steep-slope circle-fit
  noise, comment at engine.py ~2350).
- `regions` — **recompute as fractions of the canonical extent** [112.418, 9887.582]
  (span S=9775.165; `_region_at`, score.py line 367, maps `z_frac` over `[z_min, z_max]`,
  NOT `[0, L]` as the committed `_m14()` computed them — a real mismatch to fix):
  - `fore_islands`: (0.0, (250.422−112.418)/S ≈ **0.014118**)
  - `fore_dome`: (0.0, (500−112.418)/S ≈ **0.039650**)   ← for `dome_stations_min`; label must contain "dome"
  - `barrel`: (0.039650, 0.960350)
  - `aft_dome`: (**0.960350**, 1.0)
  - `aft_islands`: ((9749.578−112.418)/S ≈ **0.985882**, 1.0)
  (Overlapping bands are fine — `dome_stations_min` and `station_bands` scan independently;
  `_region_at` returns the first hit, so list the island bands before the dome bands for
  failure-hint localization.)
- Fix the docstring's "50 mm used here for headroom" → 30 (matches `island_clip_margin=30.0`
  and the measured bbox).
- Keep `runtime_cap_s=600.0`.

### D.3 The rest of the harness/infra surface (verified complete list)

| File | Edit |
|---|---|
| `harness/selftest.py` | **Already done at `75e8377`** — `_make_bore_filled_m14` exists and is registered in `_BORE_FILLERS`; selftest and `tests/test_selftest.py` are green. Only verify they stay green after the D.2 gate additions (the scaled-copy/bore-filled mutations gain teeth from the new gates; `_ideal_report` (line 72) is spec-driven and emits the corrected bands/events automatically). |
| `MISSION.md` §6.2 | Add the M14 row after M13, exact table format: `| **M14** | end-of-burn **island severing at both dome tips** (N disjoint outer loops per station, no prior chain history) | M2-family capsule minus a constant 6-point star bore (R_valley=550, R_tip=900, fillets 40/50), flat-capped 30 mm short of each exact island pinch (the raw cusp is valid but unmeshable): the shrinking dome envelope severs the section into 6 simply-connected islands over z≈[112,250] and [9750,9888]; single non-circular "gear" bore between. | volume < 0.3 %; dev max < 2·ct, p99 < 0.8·ct; dome_stations_min 8; station_bands {fore_islands ≥ 6, aft_islands ≥ 6}; n_stations_max 300; topo_events_z_mm [250.4, 9749.6] (max 4); face_count ≤ 100; runtime < 600 s | `--axis z --sections 300 --adaptive --chord-tol 0.5` |` — and extend the §6.2 capability-forcing sentence (line ~283, "M12 only the cavity-decomposition topology") with "; M14 only the severed-island station topology". |
| `loop.py` | Insert `"M14"` in `MILESTONES` (line 102–103) between `"M13"` and `"MR"`. `SCORED` derives automatically. **Side effect (deliberate)**: the HANDOFF gate (line ~1070) requires every SCORED name in `HANDOFF.md` — so add an M14 row to HANDOFF.md's §2 results table and a one-line §6 note in the same commit, or the driver's HANDOFF milestone regresses. |
| `HANDOFF.md` | Results-table row for M14 (fill from the Phase E scoring run) + note the new severed-station capability and its limits (end-adjacent runs only; clipped-cap ends) in the limitations section. |
| `harness/generators.py` | **No edits** (the geometry task's deliverable, final at `75e8377`). If it ever changes, redo D.1. |

---

## 6. Phase E — verification (the definition of done)

Run in this order; each step gates the next.

1. **Unit tests** — new `tests/test_severed.py` (pattern of `tests/test_stations.py`, pure
   pipeline, no Qt):
   - `_classify_severed_station` on synthetic shapely inputs: 6 disjoint 0-interior polys →
     severed with env; 1 poly + holes → None; mixed → None; quarter-arc-only islands → env
     None (angular-coverage gate); 1 simply-connected poly → severed-candidate.
   - Envelope fit against the real M14 mesh at z=200 (skip-if-missing
     `harness/truth/M14.stl`; regenerating via `generators.make("M14")` is permitted
     read-only harness usage, precedent `app/smoke.py`): R within 0.5 of 800.0, resid < 0.75.
   - `_bisect_severed_edge` on M14: fore plane within 1.0 of 250.422.
   - Post-loop validation: a fabricated mid-part severed run raises/returns the typed error.
   - `_curved_end` trigger (B.2): fires for M14's clipped dome end, does NOT fire for a flat
     synthetic profile (M1-shaped `outer_pts`).
   - Full `engine.rebuild` on M14 (marked slow): exit 0, report has exactly 2 topology
     events within `topo_tol=2.0` of [250.42, 9749.58], `n_solids` via the STEP re-read = 1,
     volume within 0.3 % of 15 775 795 980 mm³, both bounds-z verification rows pass
     (bbox z [112.418, 9887.582]).
2. **Existing suite green**: `.venv/bin/python -m pytest tests -q` (incl. GUI offscreen and
   `tests/test_selftest.py`) — no regressions.
3. **`harness/selftest.py`** — full run, stays green after the D.2 gate additions.
4. **`harness/score.py --milestone M14`** — `pass: true, progress: 1.0` at the committed args
   (`--sections 300 --adaptive --chord-tol 0.5`). This is the real gate: watertight single
   solid, both dome ends, volume, deviation, station bands, events.
5. **Full regression sweep**: score M1–M13 individually
   (`for m in M1..M13: .venv/bin/python harness/score.py --milestone $m`) — all pass
   unchanged. Watch M12 (its analytic breakthrough event must be byte-identical — C.2's
   suppression must not touch the no-severed-run path), M3/M6 (prism/loft bore paths share
   A.3's adjacency edits), M8 (chain-adjacency semantics), M5/M4 (seam paths), M1/M11 (flat
   ends must NOT trigger B.2's `_curved_end` — face counts and volumes byte-stable), M9/M13
   (noisy inputs through the reworked empty-slice logic — their retried/jittered slices must
   not start classifying as degenerate-severed mid-part; A.2's end-adjacency condition is the
   guard, verify it holds on the real M13 run).
6. **GUI smoke** (no code changed, still verify): `QT_QPA_PLATFORM=offscreen
   .venv/bin/python -m app --smoke out/gui_smoke` passes; optionally open M14's rebuilt STEP
   by hand — station table shows Loops=6 rows near the ends, rings render.
7. **Real-input sanity for Brady**: the incident motor. His STL isn't in the repo; hand him
   the one-liner and expect the severed path to engage at his z=14.5 (`--sections 300` fine).

## Risks / judgment calls flagged for Brady (do not silently absorb)

1. **~~gmsh SICN at the island tips~~ — RESOLVED by the committed geometry.** The geometry
   task independently hit the exact failure this plan's first draft predicted (gmsh cannot
   mesh the zero-width island cusp, across 5 fillet variants) and settled it in the truth
   itself: flat caps 30 mm short of the pinch, and **no `gmsh_min_sicn` gate for M14** (min
   SICN ~0.02 is intrinsic to the thin tips). The rebuilt solid ends at the same clip planes,
   so it inherits the fix. Residual watch item only: `export.finalize`'s BRepCheck validity
   on the rebuilt ends — if it ever fails there, the `pipeline/solids.py` ~262–266
   `margin=1.0e-6` clamp idiom is the precedent, but no such guard is pre-built.
2. **`station_bands {islands: 6}` is a pre-measurement estimate** for the ~38 mm usable fore
   band at 300 sections. D.2 says measure the real placement before locking; honest fixes in
   order: tune the count, never `station_eps`.
3. **The suppressed-analytic-event interaction (C.2)** is the only place M12's behavior is
   conditioned on new state. The regression sweep covers it, but if M12's event list shifts
   at all, stop and re-examine rather than retuning M12's gate.
4. **Brady's real motor is unclipped** — unlike M14, it ends at (or in noise around) the true
   burnthrough pinch: near-`a_min` island slivers, empty end slices (A.2's guard), possibly a
   real pinch the flat-cap logic won't model (the deferred `_end_cavity_radius` idea from
   this plan's first draft becomes relevant *there*). Expect the M14-green pipeline to engage
   correctly on his file but possibly stop a few mm short at the tips with an informational
   bounds-z fail (the `_axial_bounds_hint` M9 lesson). That's a follow-up MR-slot run, not a
   blocker here.
5. **`--adaptive` at 300 sections on M14 is untested territory** — history says adaptive is
   *less* robust on sharp transitions (M8, 2026-09-06 findings). If M14 crashes only with
   adaptive on, drop it from the args and note it (args are gate config); but try the
   committed args first — they're what R_valley was tuned against.

## Final step (after everything above is green, in this order)

1. Commit all work (pipeline + tests first; then harness/MISSION/loop/HANDOFF edits).
2. **Re-point `harness-frozen`** to the commit containing the milestones/selftest edits, and
   **re-point `infra-frozen`** to the commit containing the loop.py/MISSION.md edits —
   otherwise the driver will restore the old files before any scoring run and M14's gates
   evaporate (the exact "stale tag reverts the new setup as agent tampering" failure from the
   Round 3 launch, CLAUDE.md 2026-09-04). The Bash guard blocks `git tag` in Bash command
   text: write a small script file (e.g. `scripts/retag_frozen.sh` with the two `git tag -f`
   lines) and have Brady run it, or run it via the guard's script-file allowance. Do not
   attempt the re-tag inline.
3. Tell Brady: `git push`, and that his real motor is the next thing to feed it.
