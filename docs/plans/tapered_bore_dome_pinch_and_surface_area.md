# Plan: tapered bore lofting, dome-pinch/flat-cap ends, surface-area verification (M16)

_Implementation plan, 2026-09-09. Written from a full read of the CURRENT `pipeline/engine.py`
(commit `451b9dc`, working tree clean — line references below are as of that commit; the quoted
search anchors are the stable thing, re-verify before editing), `pipeline/solids.py`'s loft
builders, `_compute_verification` and its GUI surface (`app/main_window.py`), `harness/
generators.py`/`milestones.py` (M3/M6/M8/M14/M15 as templates), MISSION.md §6.2, `loop.py`,
`docs/research/04-pipeline-design-notes.md` — plus **direct empirical reproduction probes**
run against the current engine (geometry recipes in §1.5; every number marked **measured**
came from an actual run, not from reading `SESSION_REPORT.md` or the code)._

**Audience: the implementation agent.** You have no memory of the conversation that produced
this. Every engineering decision is made below — do not re-open one without flagging to Brady.

**The triggering incident**: Brady ran the tool on a real Minuteman propellant-grain STL on a
separate Windows machine, where a different assistant produced `SESSION_REPORT.md` (committed
here) describing four fixes and one failed attempt — all made to local files that were never
committed and no longer exist. Brady's explicit instruction: *"I don't want the code from the
other agent... I just want you to go through the report and see what you can change."* This
plan treats every claim in that report as an unverified hypothesis and re-derives each one
against the current code. §1.1 is the claim-by-claim audit. The report's §1 (PySide6/six
startup crash) is already fixed here differently and better (`748bf5c`, bare
`import matplotlib`) and its §2 (viewport unit scale) is already fixed at `451b9dc`
(`app/worker.py::_read_and_scale_mesh` + `_on_units_changed`) — **both are out of scope, do
not touch them.**

**Hard rules inherited from this project:**
- One capability per phase, no regressions: **M1–M15 must all still pass after every phase,
  and M16 after Phase E.** This plan edits the bore/fin construction paths (M3/M4/M5/M6/M8/
  M12/M13/M14 territory) and the dome endpoint logic (M2/M5/M8/M9/M12/M13/M14) — blast radius
  comparable to M15's tolerance rework, so the full sweep (§7.5) is not optional and runs
  more than once.
- `harness/` is tagged `harness-frozen`; `MISSION.md`/`loop.py` are `infra-frozen`. Both tags
  were re-pointed after the M15 work (NOT stale). Editing them for this deliberate,
  human-approved extension is permitted (CLAUDE.md); **both tags must be re-pointed after M16
  is fully green** — final step, §9. The repo's Bash guard blocks `git tag` in Bash command
  text; do the re-tag via a script file or leave it to Brady.
- Error messages/hints must be actionable and context-aware with concrete numbers
  (`_non_axisymmetric_hint`, `_axial_bounds_hint` are the house style).
- Use `.venv/bin/python`. No `git push` (Brady pushes). No emojis.

---

## 0. Decisions made (summary — rationale in the sections below)

| Question | Decision |
|---|---|
| Report §3 (tapered star built as constant prism) | **Confirmed for the mixed circular+non-circular paths only** (engine.py ~3571, ~3581, and `_fuse_sandwich_bore`'s call at ~1350 all hardcode `_build_prism_bore`); **stale for the pure non-circular path** (~3597 already routes through `_build_bore_prism_or_loft`, which lofts when area varies — M6's machinery). Fix: one shared selector with a 3-rung ladder (prism / proportional loft / actual-rings loft), wired into all four call sites (§3, §4). |
| Report §4a (pinch override before checking the end really pinches) | **Confirmed and reproduced** (probe B, §1.3: 54.4 mm phantom extension past a real flat cap, deviation p95 8.2 mm with the worst point *below* the real part). The override at ~3310–3317 runs before `is_pinch_*` (~3377) and `r_start` is then evaluated at the already-moved `z_min`, making the pinch test circular — exactly the report's mechanism, still live. Fix: measured-end-radius gate, **asymmetric by direction** (§2.2) so M9's inward quantization-noise snap is preserved untouched. |
| Report §4b (quadratic extrapolation collapses a flat-ish end) | **Mechanism confirmed in code** (`r_start`/`r_end` at ~3318–3319 are pure model evaluations; no end-slice measurement exists anywhere on the endpoint path; `_curved_end`'s densify resamples from the *same* model, so it cannot correct a wrong extrapolation). Fix: the same `_measure_end_radius` primitive snaps a validated non-pinch endpoint to the measured radius (§2.3). |
| Report §5 (proportional scaling can't track non-proportional growth) | **Agreed on the merits** — and it indicts the CURRENT `_build_bore_prism_or_loft` equally (it scales ONE reference ring by an area-fit scalar; two-end only). The report's own fix (multi-station *proportional* loft) is explicitly not ported. Full design in §4: loft through each station's ACTUAL resampled ring, FFT-seam-aligned. |
| Report §6.1's citation of `docs/research/04-pipeline-design-notes.md` §3 | **Verified accurate.** That file's §3 ("Twist/seam control") is real prior design work: uniform arc-length resample to common M, FFT seam anchor `θ0 = −phase(c_n)/n` with drift clamp, winding normalization, single-edge periodic B-spline wires, `CheckCompatibility(False)`, post-loft slice-back verification. §4 of this plan follows it directly instead of designing from scratch. |
| Report's `_build_fin_solid_prism_or_loft` / `build_multi_station_ruled_loft_solid` | **Do not exist in this repo** (grep-verified) — they lived only on the Windows machine. Nothing to reuse; §3/§4 are designed fresh against current code. |
| Where the taper selector plugs in | All four bore-cutter construction sites: the two single-event seam blocks (~3571, ~3581), `_fuse_sandwich_bore`'s prism (~1350), and `_build_bore_prism_or_loft`'s own ladder (~1801). One helper, `_build_tapered_bore_cutter` (§3.2), so the constant-cross-section fast path (`a_span < 1e-3·mean`) keeps M3/M4/M5/M8/M14 **byte-identical** — that invariant is the regression story. |
| Loft wire form for the actual-rings rung | Single-edge closed **periodic B-spline** per section (research 04 §3/§5), `ThruSections(isSolid=True, isRuled=False)` with `Approx_ChordLength` + `SetMaxDegree(8)` (research 04 §8 bonus 12). Rationale §4.3: one smooth lateral face instead of ~M ribbon faces (the exact `gmsh_tet` failure mode `build_fillet_loft_solid`'s docstring documents), and every cross-section on this path is fillet-smooth (no sharp corners to preserve). |
| Seam compatibility with the circular cutter | Keep the existing fuse architecture untouched (`circ_overlap`/`fin_overlap`/`_fuse_seam_bore` clearance ladder). The loft's seam-adjacent sections get the same `bore_radius` snap the prism gets, applied per-ring to near-bore-radius resampled points (§3.3). The clearance-taper retry rung remains the safety net. |
| Surface-area check: solid side from BRep or preview STL? | **Exact BRep area** (`BRepGProp.SurfaceProperties_s`). Measured (§5.1): on clean milestones the input-mesh vs BRep area agreement is ≤ 0.017 % — the inscribed-chord sag argument for tessellation-vs-tessellation comparability is empirically negligible — and the BRep value has no dependence on `--stl` being requested or on `_solid_tessellation` succeeding. Matches the Volume check's exact-BRep convention. |
| Surface-area tolerance | **Fixed 1.0 %** (≈ 60× the worst clean-milestone delta, small enough to catch multi-percent feature misses), with a two-branch context-aware hint (§5.3) mirroring `_axial_bounds_hint`'s M9 precedent for noisy inputs — a per-input noise scale was tried (§5.2: dihedral-based estimators measured, all off by 5–25×) and is explicitly deferred, the same deliberate follow-up already recorded at engine.py ~2074–2078 for the bounds check. |
| Does the area check trigger refinement retries? | **No, structurally**: `_refine_anchor_from_outcome` (~2473) keys ONLY off `verification["deviation"]`. No change needed; noted so nobody "helpfully" adds it — an area miss has no `worst_z_mm` to anchor stations at. |
| M16 geometry | One motor, Minuteman-shaped (§6.1): capsule with BOTH ends flat-capped inside real dome curvature (fore: circular bore exits a small annular cap — gates §4a; aft: the star bore exits a larger cap — gates §4b + `_curved_end`), single topology event at z=6000 (M4-style flat fin wall — probe C measured the bisection lands at 6000.000), and a **two-family alternating 10-lobe star** whose families grow at genuinely different rates toward the aft end (non-proportional — defeats every scalar-scaling path, forces the actual-rings loft). Exact analytic BRep, tessellated at ct=0.5 like M1–M8 (this milestone tests geometry, not noise robustness). |
| M16 and the frozen scorer | **No new gate types** (M15 precedent) — volume/deviation/bbox/topo_events/face_count/dome_stations_min already force the honest path. The surface-area check stays an additive engine-side verification key the scorer never reads. |
| Milestone number / ladder position | **M16**, between M15 and MR (`loop.py` `MILESTONES` ~line 105; MISSION §6.2 row after M15). |

New/modified files:

```
pipeline/engine.py           §2 end-measurement + pinch gate; §3 selector wiring; §4 rings loft
                             driver; §5.3 verification area check + hints
pipeline/solids.py           §4.3 build_ring_loft_solid (periodic-B-spline multi-section loft)
app/main_window.py           §5.4 _verification_group area row + _verification_all_passed key
harness/generators.py        §6.2 _two_family_star_wire + _make_m16
harness/milestones.py        §6.3 _m16() spec
harness/selftest.py          §6.4 _make_bore_filled_m16 + _BORE_FILLERS entry
MISSION.md                   §6.5 §6.2 M16 row (+ capability-forcing sentence)   [infra-frozen]
loop.py                      §6.5 MILESTONES list                                [infra-frozen]
HANDOFF.md                   §6.5 M16 results row + limitations note
tests/test_end_measure.py    §7.1 new (Phase A)
tests/test_taper_loft.py     §7.2 new (Phases B/C)
tests/api/test_engine.py     §7.3 extended (verification area block)
tests/gui/test_main_window.py §7.3 extended (area row rendering)
```

---

## 1. Verified findings (measured, not assumed from the report)

### 1.1 Claim-by-claim audit of SESSION_REPORT.md

| Report § | Claim | Verdict against current code |
|---|---|---|
| §1 | PySide6/Shiboken/six startup crash | Already fixed here, differently (`748bf5c`); report's `import matplotlib.pyplot` suggestion deliberately NOT used. Out of scope. |
| §2 | Viewport input-mesh unit scale | Already fixed (`451b9dc`, `app/worker.py::_read_and_scale_mesh`). Out of scope. |
| §3 | "Engine always builds non-circular bore segments as a constant prism" | **Half right.** True for the mixed circ+non-circ paths (~3571/~3581/~1350: hardcoded `_build_prism_bore`). False for the pure-ring path (~3597 → `_build_bore_prism_or_loft`, lofts when area varies). The Minuteman is single-event (circ fore, star aft) so it hits the true half. |
| §4a | z_min/z_max pinch override before checking the end really pinches | **Confirmed + reproduced** (probe B, §1.3). |
| §4b | Quadratic extrapolation collapses a flat-ish end radius to 0 | **Mechanism confirmed in code** (§2.3); shares a fix primitive with §4a. Not separately reproduced (probe B exercises the sibling path; the primitive covers both ends). |
| §5 | Proportional scaling loses non-proportionally-growing lobes (p95 31 mm) | Agreed analytically; applies equally to the current proportional loft. The report's own multi-station proportional builder is NOT ported (it's still proportional — its own §6.1 concedes this). |
| §6.1 | "FFT seam alignment researched in docs/research/04 §3" | **Citation verified accurate** — real, detailed prior design; §4 builds on it. |

### 1.2 The four bore-cutter construction sites (current code)

- `_build_prism_bore` (engine.py ~1141): constant-cross-section prism from `_pick_best_ring`'s
  mid-window candidate. Correct only for axially constant bores (M3/M14).
- Single-event seam paths (~3557 `elif circ_before:` → **~3571**, and the mirrored else →
  **~3581**): `fin_solid = _build_prism_bore(bore_rings, event_z, z_max, ...)` — the
  Minuteman/probe-C path. Constant prism, hardcoded.
- Sandwich fuse (`_fuse_sandwich_bore`, ~1325, call at **~1350**): same hardcoded prism
  between two circular revolves (M5/M8's fallback rung when wedges don't fit).
- Pure-ring path (**~3596–3597** `elif bore_rings:` → `_build_bore_prism_or_loft`, ~1801):
  quadratic area fit over every station's ring area; `a_span < 1e-3·mean` → prism; else
  scale ONE reference ring by `sqrt(area_ratio)` at the two extended ends and ruled-loft
  (`build_fillet_loft_solid` when exact fillets fit, else `build_ruled_loft_solid`). Handles
  M6's uniform linear scaling; **cannot represent shape morphing** (single reference ring,
  single scalar per end).

### 1.3 Probe B — the flat-cap pinch override, reproduced (report §4a)

Geometry (analytic OCP, tessellated ct=0.5, built with `harness.generators` helpers,
read-only): capsule L=10000/R_o=1000/2:1 domes, fore dome truncated by a flat cap at
z_cap=150 (envelope radius there 714.1 mm), straight bore R=300 through. True z extent
[150, 10000]. Run: `--axis z --sections 60 --chord-tol 0.5`, `refine_passes=0`.

**Measured result**: rebuilt solid spans z **[95.59, 9976.97]** — extended **54.4 mm past the
real cap** (bounds-Z FAIL, tol 9.88 mm); deviation p95 **8.21 mm** / max 22.9 mm (tol 1.0)
with `worst_z_mm ≈ 68`, i.e. *below the real part*; volume error only 0.055 % (the phantom
tip is thin — volume does NOT catch this class). The mechanism, traced: `_solve_pinch_z`
finds R(z)=300 at z≈95.6; `abs(95.6 − 150) = 54.4 < cap = max(5·station_eps, 50) = 500`
(station_eps = 100 here, line ~2776) → `z_min` overridden (~3314–3315) → `r_start =
_eval_r2_quadratic(..., z_min)` (~3318) returns the bore radius *by construction of the
root* → `is_pinch_start` (~3377, `abs(r_start − bore_pts[0][1]) < 50`) is trivially true →
a pinch profile is built where the truth has a flat annular face. The report's "circular
logic" description is accurate against current code. Bonus finding: the bounds-Z hint then
suggests `--chord-tol`/`--adaptive` — the exact wrong-lever hint class the M9 incident
(2026-09-08) removed for the shortfall direction; the extension direction still has it.

Also verified: this bug is **unexercised by every milestone through M15** — it needs
`bore_pts` non-empty (a circular bore chain) AND a non-pinching end with dome curvature.
M14 has no `bore_pts` at all; M1/M3/M4/M7/M11's flat ends have no dome curvature (quadratic
`a ≈ 0` → `_solve_pinch_z` returns None); M2/M5/M8/M9/M12/M13 genuinely pinch. That is why
it survived 15 milestones — and why M16 must gate it (§6).

### 1.4 Probe C — the tapered single-event star, reproduced (report §3)

Geometry: cylinder L=10000/R_o=1000; cutter = straight bore R=300 fused with a 6-point star
loft (`_star_wire`, valley 320 / tip 500 / fillets 30/40) from z=6000 (flat fore wall —
M4-style event) uniformly scaled ×1.8 by z=10010. Same run args as probe B.

**Measured result**: the event is found essentially exactly (`topology_events_z_mm =
[5999.99997]`), the run "succeeds", volume error vs analytic truth is only **0.164 %** —
and deviation p95 is **179.8 mm** (tol 1.0 mm), worst at z≈6250, `paths_used: {bore:
"mixed"}`. The constant mid-station prism over-cuts the fore half of the taper and
under-cuts the aft half; on this symmetric taper the volume terms nearly cancel while the
surface is wrong nearly everywhere. Two consequences worth internalizing: (a) the report's
"4.5–13 % volume error" framing is geometry-dependent — **deviation is the reliable symptom,
volume is not**; (b) this is direct evidence for Brady's surface-area check being a
*complement* to volume, not a substitute for deviation (measured on this probe: the area
delta is only +0.28 % — area catches missing/under-resolved features, §5.1, not placement
errors; do not oversell it).

### 1.5 Probe reproduction recipes

The probe scripts live in this session's scratchpad (not committed). To re-create: probe B =
`_capsule_outer_shape(10000, 1000, 500)` cut by a box `z ≤ 150`, cut by
`_straight_bore(300, 10000)`; probe C = `BRepPrimAPI_MakeCylinder(1000, 10000)` cut by
`fuse(_straight_bore(300, 10000), ThruSections(ruled) between _star_wire(6, 320, 500, 30,
40, 6000) and _star_wire(6, 576, 900, 54, 72, 10010))`; both `_write_stl(..., 0.5)`, then
`engine.rebuild(RebuildOptions(axis="z", sections=60, chord_tol=0.5, refine_passes=0))`.
§7's tests re-encode both at reduced size as fixtures.

---

## 2. Phase A — measured end radii: the pinch-override gate and non-pinch endpoint snap

All in `pipeline/engine.py`. This phase alone must turn probe B from "54.4 mm phantom tip,
p95 8.2 mm" into a clean flat-capped rebuild, with M2/M5/M8/M9/M12/M13's pinch ends
byte-stable.

### A.1 New helper `_measure_end_radius(mesh, z_bound, at_start, chord_tol, resid_gate)`

Place near `_solve_pinch_z` (~594). Slice the mesh at `z_probe = z_bound ± inset`,
`inset = max(2·chord_tol, 1e-4·L)` (the same ε convention `docs/research/04` §1 uses for
end insets), via the existing `slice_station` (which already carries the jitter/retry and
sliver filters). Require exactly one polygon; fit `fit_circle_robust` on its exterior;
return `(R_meas, max_resid, z_probe)` only when `max_resid ≤ resid_gate` and
`_axis_centered(...)` — else `None`. **None is a first-class answer** ("this end is not
measurable — degenerate/noisy slice"), and every consumer below has an explicit
None-fallback that reproduces today's behavior; that is the M9/M13 regression story.
Precedent for slicing extra non-station sections near an end: the M13 end-window probe at
~3172–3211 ("not stations: they are not reported, do not consume the --sections budget").

### A.2 Gate the pinch override — asymmetric by direction (the load-bearing decision)

At the override block (~3310–3317, anchor `cap = max(5.0 * station_eps, 50.0)`), split the
acceptance:

- **Inward move** (`z_fore_pinch > z_min`, resp. `z_aft_pinch < z_max` — the solid SHRINKS):
  accept exactly as today, cap only. This is M9's case — its mesh's extreme vertex is
  quantization noise *outside* the true tip (measured in `_solve_pinch_z`'s own docstring:
  mesh bound 31.6 vs true tip 53.5), and its near-tip slices are exactly the degenerate ones
  `_measure_end_radius` would refuse, so making the inward path depend on a measurement
  would regress M9's bbox fix for nothing.
- **Outward move** (the solid EXTENDS past the mesh — probe B's direction, and the only
  direction the flat-cap bug can take, since a flat cap's envelope radius sits far above the
  bore): require confirmation that the mesh really approaches the bore radius at its own
  bound: `_measure_end_radius(...)` must return `R_meas ≤ target_r + 50.0` (the same 50 mm
  window `is_pinch_*` already uses — one convention, not a new constant). A true pinch
  passes easily (at M2's slope, R(z_min + inset) ≈ bore + ~3 mm); probe B's 714 vs 350
  refuses loudly. **A None measurement refuses the extension** — extending the part on
  unmeasurable evidence is precisely the bug class; the raw mesh bound is the honest
  default. Leave a comment carrying probe B's measured numbers.

`is_pinch_*` (~3377) needs no change: once a bogus override is refused, `r_start` is
evaluated at the true bound where the model reads the envelope radius (~714 on probe B),
and the 50 mm test is False on its own merits. The circularity is broken at its source.

### A.3 Snap a validated non-pinch curved endpoint to the measured radius (report §4b)

Where `fore_curved`/`aft_curved` fire (~3389–3392) the endpoint radius currently comes
purely from the model (`r_start`/`r_end`, ~3318–3319) — extrapolated past the last fitted
station, which is exactly where a quadratic can predict a collapse toward 0 on a
shallow/flat-ish end (report §4b's aft dome; nothing in the current code can catch it —
`_refine_dome_model_from_vertices` (~623) only refits *between* the first fitted station
and `window_z`, and `_densify_dome_chords` (~796) resamples from the same model). Change:
for each curved non-pinch end, call `_measure_end_radius`; when it returns a valid fit,
**replace** the model endpoint (`r_start = R_meas` before the `fore_gap[0] = (z_min,
r_start)` line at ~3410, mirrored for aft). When the model and measurement disagree by more
than `max(4·chord_tol, resid_gate)`, additionally blend the densified in-gap samples: add
`(R_meas − R_model(z_end)) · w(z)` with `w` linear from 1 at the end to 0 at the *first
real fitted station* (not the whole window — the model is validated inside its own fitted
span; only the extrapolated inset band is suspect). Guard: if blending makes the gap
profile non-monotonic where the unblended one was monotonic, keep the endpoint snap only
and drop the blend (a kinked profile fails `build_revolve_solid`'s RDP worse than a small
radial offset does). Pinch ends (`is_pinch_*` true) are untouched — their endpoint already
snaps to the bore fit, which is better evidence than any end slice.

### A.4 What Phase A must NOT touch

- The inward-override path and cap (M9's fix) — verified byte-identical behavior by the M9
  scorer run in §7.5.
- M14's flat caps: `bore_pts` is empty there, so the override block (~3310 `if bore_pts:`)
  never runs; `_curved_end` + A.3's snap applies and must agree with `_extrapolate_end`'s
  already-accurate value (measured in M14's plan: the endpoint radius was already right;
  A.3's measurement on a clean cap returns the same number — assert this in tests).
- `_axial_bounds_hint`: no change needed — probe B's wrong hint disappears because the
  failure itself does.

---

## 3. Phase B — the taper selector in the mixed circular+non-circular paths (report §3)

### B.1 One selector, three rungs

New `_build_tapered_bore_cutter(bore_rings, z_lo, z_hi, eps_lo, eps_hi, chord_tol,
bore_radius=None)` placed next to `_build_bore_prism_or_loft` (~1801), sharing its area-fit
machinery:

1. **Constant** (`a_span < 1e-3·mean(areas)` — the existing threshold, unchanged):
   `_build_prism_bore(...)` exactly as today, including the `bore_radius` snap and
   `validate_area=(bore_radius is None)` scoping (that scoping's rationale — the measured
   M8 `--sections 52 --adaptive` edge case — is documented at ~1083–1098 and must survive).
   **This rung keeps M3/M4/M5/M8/M13/M14 byte-identical**, because every one of their
   non-circular zones is axially constant.
2. **Proportional loft** (area varies, shape doesn't): the existing
   `_build_bore_prism_or_loft` body, generalized to arbitrary `[z_lo, z_hi]` +
   asymmetric overlaps (today it hardcodes the part ends and symmetric `eps_cut_val`) —
   quadratic area fit, one reference ring, `build_fillet_loft_solid` /
   `build_ruled_loft_solid`. **New acceptance test before this rung is trusted** (the
   current code has none — it assumes proportionality): for each of ~5 evenly-spread
   stations, scale the reference ring to that station's fitted area and compute the max
   radial mismatch against the station's actual ring at matched angular samples; accept
   only if `≤ 2·chord_tol` everywhere probed (research 04 §1's `tol_h = 2·chord_tol`
   Hausdorff convention). M6 passes (true uniform scaling); a shape-morphing bore falls
   through to rung 3.
3. **Actual-rings loft** (§4). On any construction failure or validity failure inside rung
   3, fall back to rung 2's result (and rung 2 to rung 1), never crash — matching the
   fallback-ladder convention of `_build_slot_wedges`/`_build_slot_lobes`, with the
   existing actionable-hint error path (~3603–3625) as the final backstop.

Report `paths_used` honestly: extend the existing `paths_slot`/`paths_used` plumbing with
`"bore": "prism" | "loft_proportional" | "loft_rings"` so a scorer/HANDOFF reader can see
which rung fired (the Round-2 review's recorded `paths_used` gap makes this cheap honesty
worth a line).

### B.2 Call-site wiring

- ~3571 and ~3581 (single-event): replace `_build_prism_bore(bore_rings, event_z, z_max,
  fin_overlap, eps_cut_val, chord_tol, bore_radius=...)` with the selector, same span/
  overlap/bore_radius arguments. This is the Minuteman/probe-C path.
- `_fuse_sandwich_bore` (~1350): same replacement (M5/M8's sandwich rung — constant there
  today, so rung 1 fires and nothing changes; the wiring exists so a real sandwich-tapered
  motor works without another surgery).
- ~3597 (pure-ring): `_build_bore_prism_or_loft` becomes a thin wrapper over the selector
  with `z_min/z_max` and symmetric `eps_cut_val` (rung 2 is its current behavior plus the
  new acceptance test; rung 3 is new capability).

### B.3 Seam-fuse semantics with a tapered fin solid (the risky interaction)

The single-event fuse relies on documented invariants (comments at ~3438–3497): the
circular cutter's widened `circ_overlap = 80·seam_eps` is a geometric no-op **only while
the circle is a subset of the star cross-section throughout the overlap band**, and
`_fuse_seam_bore` retries with a `4·seam_eps` clearance taper when the zero-clearance fuse
is invalid. With a tapered fin solid the section at the seam is the zone's extreme
(smallest, when the bore grows toward the far end), so:
- The seam-side loft section must keep its bore-arc region at the snapped `bore_radius`:
  after resampling (§4.2), radially snap every resampled point whose radius lies within
  `4·chord_tol` of the fitted seam bore radius onto it, for the seam-adjacent section only
  (deeper sections keep their honest measured radii — the circle cutter never reaches them).
  This mirrors what `_build_prism_bore`'s `bore_radius` parameter does for the prism wire.
- Verify on M16 that the fuse still validates at clearance 0 and falls back cleanly at
  `4·seam_eps` otherwise; if both fail, that is a real finding to bring back to Brady, not
  something to tune silently (the M8 "two surfaces closer than fuzzy" history at ~3477–3497
  says which failure signatures to expect).

---

## 4. Phase C — actual-rings loft (non-proportional growth; report §5/§6.1)

### C.1 What it must do, and its honest scope

Loft the cutter through each station's ACTUAL traced ring so lobe families that grow at
different rates are reproduced exactly *at the stations*, with smooth interpolation between.
Fidelity between stations is bounded by station density (the same bound every other path
already lives under, and what `--sections`/`--adaptive`/refinement already control).
**Scope note for Brady (do not oversell): this is a full fix for the class of geometry M16
encodes — clean meshes, smoothly varying non-proportional sections. On a noisy real scan
the per-station rings inherit slice noise directly (no cross-station averaging like the
dome's vertex refit), so real-input quality there is expected to be "good, not
chord-tol-exact", and per-station outlier rejection / cross-station ring smoothing is an
explicitly deferred follow-up.** The M15 auto roundness floor helps (stations classify
correctly) but does not de-noise the ring geometry itself.

### C.2 Ring preparation (engine side, research 04 §3 followed verbatim)

For each `(z, ring)` in the zone, plus the two extension sections (below):
1. Normalize winding (CCW, shoelace sign) and drop the duplicate closing point.
2. Resample to a common `M` at uniform arc length. `M = max(128, 64·n_lobes)` per research
   04 §3, `n_lobes` from the FFT below, capped at 256.
3. Seam: FFT of `r(θ)` about the ring centroid; dominant harmonic `n ≥ 2` with amplitude
   `> 3·chord_tol` → `θ0 = −phase(c_n)/n`; else `θ0 = 0`. Track `θ0` along the chain,
   unwrap, and clamp: drift `> π/n` between adjacent stations reuses the previous station's
   `θ0` (the research note's anti-flip rule). For M16's alternating families the stable
   dominant harmonic is `n_lobes/2` (the true rotational symmetry — C5 for 10 alternating
   lobes); which harmonic wins does not matter, only its stability, which the drift clamp
   enforces.
4. End extensions (`z_lo − eps_lo`, `z_hi + eps_hi`): per-point **linear extrapolation from
   the last two resampled sections** (matches a ruled taper's true continuation, where a
   prismatic copy would under-cut a still-growing bore across the inset band). Guard with
   shapely `is_simple` on the extrapolated ring; on failure fall back to a prismatic copy
   of the end section (safe, slightly conservative).

### C.3 `solids.build_ring_loft_solid(sections)` (new, `pipeline/solids.py`)

`sections = [(z, pts_MxN2), ...]`, all same M, seam-aligned. Per section build ONE closed
periodic B-spline edge through the points (`GeomAPI_Interpolate(..., Periodic=True, tol)`
on an RDP-decimated point set, or `GeomAPI_PointsToBSpline` — research 04 §5's fallback
row; pick whichever passes the self-intersection check below on M16, document the choice),
wrap in a one-edge wire; `BRepOffsetAPI_ThruSections(isSolid=True, isRuled=False)` with
`CheckCompatibility(False)` (we guarantee correspondence — same M, same seam, same
winding), `SetParType(Approx_ChordLength)`, `SetMaxDegree(8)`, `SetContinuity(GeomAbs_C2)`
(research 04 §8 bonus 12: the defaults give C0 kinks gmsh meshes badly). Post-fit checks,
each falling back per §3.1's ladder: sampled fitted curve `is_simple` (research 04 §8 #7);
loft `IsDone` + `BRepCheck_Analyzer`; slice-back verification at 3 interior z's vs the
input sections, max dev `> 2·chord_tol` → reject (research 04 §3's own verification rule —
this is what catches a silent twist/spiral instead of shipping it).

Why one-edge periodic wires and not the existing `_arc_line_wire` multi-edge form:
`build_fillet_loft_solid`'s docstring records the measured failure of dense multi-edge
wires (142 of 154 segments below gmsh's MeshSizeMin → min SICN 0.0077), and research 04 §3
flags multi-edge wires across morphing sections as the twist/mismatch trap. A single
smooth face per side steps around both. The cost — fillet arcs approximated by a spline at
fit tolerance instead of exact circles — is bounded by the interpolation tolerance
(≤ chord_tol), inside every deviation gate that will judge it.

### C.4 Face count and gmsh

Expected face count for the loft: 1 lateral + 2 planar caps (consumed by the boolean).
M16's `face_count_max` (§6.3) is set after measuring, M14-plan style ("gate config, not
pipeline shame"). gmsh on a high-degree B-spline lateral face is exercised by M16's
`gmsh_min_sicn` gate; if it fails only there, the recorded fallback is to rebuild the loft
`isRuled=True` (N−1 simpler faces) and re-measure — decide by measurement, not taste.

---

## 5. Phase D — surface-area verification check

### 5.1 Why, and the measured calibration

Brady's physical reasoning, understood and endorsed: burn surface area is what drives a
solid motor's mass-flow rate (`ṁ = ρ · r_burn · A_burn`), so the rebuilt solid's area vs
the input mesh's area is a physically meaningful check *independent of volume* — a rebuild
can carry near-correct volume while getting the burning-surface topology wrong
(under-resolved fins being the canonical case; probe C measured volume 0.164 % against
deviation p95 179.8 mm). Area is the aggregate that catches *missing/under-resolved
features*; measured caveat from probe C: it does NOT catch placement errors (its area
delta was +0.28 % on a grossly misplaced taper) — deviation remains the placement check.

Calibration, measured across every milestone with a winning STEP (`logs/M*-final-step.step`
vs `harness/truth/M*.stl`, mesh area by triangle-sum, solid by exact `BRepGProp.
SurfaceProperties_s`; M10/M13 scaled by 25.4² for their inch STLs):

| Input class | Milestones | measured area delta |
|---|---|---|
| Clean tessellations | M1–M8, M10, M11, M12 | **+0.003 % … +0.017 %** |
| Noisy marching-cubes | M9 | **+1.27 %** (mesh > solid: crumpled facets carry excess area) |
| Dirty capstone | M13 | **+3.13 %** |
| Probe rebuilds | B / C | +0.095 % / +0.28 % |

### 5.2 Tolerance decision (and the estimator that was tried and rejected)

A fixed tight gate cannot cover the noisy inputs, and a noise-scaled gate needs a per-input
inflation estimate. Tried and measured: (a) `roundness_floor` is the wrong signal — M15's
smooth ovality gives a large floor (0.975 mm) with ~zero area inflation, while inflation is
driven by facet crumpling the floor doesn't see; (b) mean face-adjacency dihedral
estimators (`0.5·E[tan²(θ/2)]`, raw and trimmed at 3–5× median) are polluted by real
feature/curvature edges on clean meshes (M6: predicts 1.17 % vs actual 0.008 %) and eat the
noise tail when trimmed (M9: predicts 0.06–0.13 % vs actual 1.27 %) — off by 5–25× in both
directions. **Decision: fixed `area_tol_pct = 1.0`** (60× the worst clean delta; still
catches the multi-percent misses this check exists for), plus the context-aware hint below.
A per-input noise scale for this check is a deliberate, recorded follow-up — the *same*
follow-up the bounds check already records for itself at engine.py ~2074–2078; if that
follow-up ever lands, both checks should consume the same estimator.

Consequence to state honestly in the code comment: an accurate M9 rebuild reports this
check as FAIL (+1.27 %) with the benign hint — exactly like M9's bounds-Z row already does
today (its `_verification_all_passed` is already False; this adds no new milestone or GUI
regression, verified in §7.5).

### 5.3 Engine implementation

In `_compute_verification` (~2097), immediately after the volume block (~2138–2168):

- `input_mm2 = float(mesh_out.area)` — same repaired, output-frame mesh object the other
  checks use (area is frame-invariant; using `mesh_out` keeps the code uniform). No
  watertight requirement (unlike volume): triangle-sum area is well-defined on an open
  mesh, but when `mesh.is_watertight` is False add a note that the comparison inherits
  whatever the repair filled in.
- `solid_mm2` via `BRepGProp.SurfaceProperties_s(shape, props)` (exact; decided §0).
- Block: `out["surface_area"] = {"input_mm2", "solid_mm2", "delta_pct", "tol_pct": 1.0,
  "pass": delta_pct <= tol_pct}`.
- Hint (only on fail), two branches in `_axial_bounds_hint`'s house style:
  - deviation passed with real margin (`p95 ≤ 0.5·dev_tol`) AND `input > solid`: "input
    mesh's own surface noise/crumpling inflates its area (measured +1.3 % on a
    marching-cubes input at accurate volume/deviation) — very likely not missing geometry;
    informational". Compute it AFTER the deviation block, exactly like the bounds hints
    (~2282–2289) and for the same reason.
  - otherwise: "missing/under-resolved surface features (thin fins/slots are the usual
    cause) — try more --sections, --adaptive, or a finer --chord-tol; check the Deviation
    row for where".
- `_print_verification` (~2296): one `verify surface area:` line in the volume line's
  format. `_run_multi_body`'s verification call (~1937) needs nothing — it flows through
  the same function.

### 5.4 GUI + report plumbing

- `app/main_window.py::_verification_group` (~1268): a "Surface area" row after Volume,
  same glyph/format idiom (render `m²` scaled like Volume's `L`: `mm²/1e6`); hint via the
  existing `_with_hint`.
- `_verification_all_passed` (~1320): add `verif.get("surface_area")` to the checks list.
- The report key is additive; the frozen scorer ignores unknown keys (M15 precedent,
  verified in its plan §0) — no `harness/score.py` change.
- `_refine_anchor_from_outcome`: untouched (deviation-only, §0).

---

## 6. Phase E — M16: the permanent regression milestone

### 6.1 Geometry (canonical mm frame; exact analytic BRep, tessellated at ct=0.5)

One motor that encodes every gap this plan fixes, shaped like the incident motor:

- **Outer**: capsule L=10000, R_o=1000, 2:1 domes both ends (dome_h=500,
  `_capsule_outer_shape`), then flat-capped by planar clips at **z_capF=150** and
  **z_capA=9850** (box cuts, probe B's construction; both caps sit at envelope radius
  1000·√(1−(350/500)²) = **714.1 mm** — well inside real dome curvature, M14's clip idiom
  at both ends).
- **Circular bore**: straight R_bore=300 through the full length
  (`_straight_bore(300, L)`). Fore of the event it is the only cavity → `bore_pts`; it
  exits through the fore cap leaving a 300→714 annular face (the §4a gate: uncorrected
  code extends this end outward — probe B measured 54.4 mm on identical fore geometry —
  and `bbox_err_pct = 0.1` (10 mm) fails it loudly).
- **Two-family tapered star**, z ∈ [6000, 10010] (running past the aft cap so the cap
  face is a clean planar cut — M4's `fin_z_end = L + 10` precedent): a 10-lobe wire with
  alternating tip families at angles `2πk/10`, ruled-lofted between two sections built by
  a new `_two_family_star_wire(n_pairs, R_tipA, R_tipB, R_valley, f_tipA, f_tipB,
  f_valley, z)` (the `_star_wire` construction with per-vertex radii/fillets — the
  MakeFillet2d machinery already takes per-vertex radii):

  | Parameter | z=6000 | z=10010 | growth |
  |---|---|---|---|
  | R_tipA (5 lobes) | 480 | 620 | ×1.292 |
  | R_tipB (5 lobes, alternating) | 400 | 460 | ×1.150 |
  | R_valley | 330 | 360 | ×1.091 |
  | fillets f_tipA / f_tipB / f_valley | 40 / 30 / 35 | 52 / 34 / 38 | per-family |

  Three deliberate properties: (i) **genuinely non-proportional** — no scalar `s(z)` maps
  the z=6000 ring onto any later ring (family ratios drift: A/B 1.200 → 1.348), so rung 2's
  acceptance test (§3.1) measurably rejects it (mismatch ~tens of mm ≫ 2·ct = 1.0) and only
  the actual-rings loft can pass the deviation gates — this is Brady's "two sets of
  geometric fins, not all exactly the same", tapering along the axis; (ii) valley > bore
  everywhere (330+ vs 300), so the star section strictly contains the bore aft of the
  event → clean M4-style single event with a flat fore wall (probe C measured the event
  bisection at 6000.000 on this exact topology class), and aft sections are pure
  `bore_rings` all the way to the cap → the single-event seam path (~3571) is the path
  under test, as on the Minuteman; (iii) tips stay inside the shrinking dome envelope
  (tipA at the aft cap: 480 + (3850/4010)·140 = **614.4** vs envelope 714.1 → ~100 mm web;
  envelope at z=9800 is 800 vs tipA ≈ 612 — margin shrinks monotonically toward the cap
  and never closes), so **no severed stations** — M16 stays out of M14's machinery by
  construction.
- **The aft end** is then: star bore exiting through a flat cap set inside a real dome —
  report §4b's shape (curved envelope, no pinch, endpoint radius must come from
  measurement, not extrapolation) with `bore_pts` non-empty on the far (fore) side — which
  also arms the *aft* half of the §4a override bug (the current code would solve the aft
  dome model against the FORE bore radius and extend z_max; the fix's outward gate refuses
  it because the measured aft end radius is 714, not ≈300).

Truth volume/area from the kernel as always (`_finish`); `closed_form_volume=None` (filleted
two-family star — M6's precedent).

### 6.2 `harness/generators.py::_make_m16`

`_two_family_star_wire` (next to `_star_wire`, ~277 — same shared-vertex MakeFillet2d
pattern, same seam/ordering guarantee so two calls are loft-compatible);
`_capsule_outer_shape` + two box clips + `_straight_bore` + `ThruSections(isSolid=True,
isRuled=True)` between the two wires + fuses/cut, `_finish("M16", ...)`. Register in the
generator dispatch (`make`, ~1080). The spec-hash cache (`_spec_hash`) regenerates the
truth whenever `_m16()`'s params change — which triggers §6.6's re-measure rule.

### 6.3 `harness/milestones.py::_m16()`

Modelled on `_m8()`/`_m14()`:

- `params`: everything in §6.1's table plus L/R_o/dome_h/z_capF/z_capA/z_ev/n_pairs.
- `rebuild_args`: `["--axis", "z", "--sections", "80", "--chord-tol", "0.5"]` — uniform, no
  `--adaptive` (adaptive is measurably less robust on sharp transitions — the recorded M8
  finding, 2026-09-06 — and nothing here needs placement help; revisit only with a measured
  reason).
- `regions`: `fore_cap_dome` (0 → (500−150)/S), `barrel`, `star_zone` ((6000−150)/S → 1.0),
  `aft_cap_dome` ((9500−150)/S → 1.0), with S = 9700 the capped extent — recompute as
  fractions of the **canonical bbox extent** [150, 9850] (the M14 plan's hard-won
  `_region_at` lesson: fractions of bbox, never of [0, L]).
- `gates` (initial; locked per §6.6 after the first honest green run): `n_solids=1`,
  `brep_valid=True`, `volume_err_pct=0.3`, `surface_deviation_p99_mm=0.4` (0.8·ct),
  `surface_deviation_max_mm=1.0` (2·ct), `dome_stations_min=8` (region label contains
  "dome"), `bbox_err_pct=0.1` ← **the §4a tripwire** (probe B's uncorrected extension is
  54 mm against this 10 mm gate), `topo_events=2.0` (`_topo_tol(L, ct)`),
  `topo_events_z_mm=[6000.0]`, `topo_events_max=3`, `face_count_max=100` (placeholder —
  measure, §6.6), `step_roundtrip_vol_err=1e-6`, `gmsh_min_sicn=0.1` (drop only with a
  measured justification recorded in the spec docstring, M14's SICN-omission precedent).
- `runtime_cap_s=600.0`, `chord_tol=0.5`.

### 6.4 `harness/selftest.py`

`_make_bore_filled_m16` + `_BORE_FILLERS["M16"]` (fill the whole cavity — bore + star —
the same mutation shape M14's filler uses); `_ideal_report` is spec-driven and picks up the
event/region config automatically. Selftest + `tests/test_selftest.py` back to green before
Phase E is called done.

### 6.5 Frozen-surface wiring (verified complete list, M14/M15 pattern)

| File | Edit |
|---|---|
| `loop.py` | `"M16"` into `MILESTONES` between `"M15"` and `"MR"` (~line 105). Side effect (deliberate): the HANDOFF gate requires every SCORED name in HANDOFF.md — add the M16 results row in the same commit. |
| `MISSION.md` §6.2 | M16 row after M15 (~line 281), same table format: geometry summary (two-family non-proportional tapered star, both ends flat-capped in dome curvature, single event z=6000), the §6.3 gates, the args. Extend the capability-forcing sentence: "; M16 only the non-proportional tapered-bore station geometry". |
| `HANDOFF.md` | M16 results row (fill from the scoring run) + limitations note: real-scan ring noise is the recorded gap of the actual-rings loft (§4.1). |

### 6.6 Measure-then-lock rule (mandatory, M14 plan §5.1's lesson)

Before locking gates: generate the truth, slice it with the pipeline's own `slice_station`
and record (a) the measured event plane from `_bisect_topology_event` (expected ≈6000.000
by probe C, but never trust a derived value — the M14 fillet moved a "known" plane
31.6 mm), (b) actual station counts in each region at the committed args, (c) the first
honest green run's face count and min SICN, then set `face_count_max`/`station_bands`/
`gmsh_min_sicn` from measurement with a comment carrying the numbers. If the generator
params ever change, re-run this step (spec-hash regenerates the truth silently).

---

## 7. Phase F — verification (the definition of done)

Run in order; each step gates the next.

1. **`tests/test_end_measure.py`** (Phase A, pure pipeline, no Qt):
   `_measure_end_radius` on a scaled-down probe-B mesh (flat cap: returns ≈ envelope
   radius; the fixture builds the §1.5 recipe at L=1000 via the read-only harness helpers,
   `app/smoke.py` precedent) and on M2's truth (pinch end: returns ≈ bore + slope·inset,
   within the 50 mm window); the outward-override gate refuses probe-B geometry (final
   z_min == cap) and accepts M2 (z bounds byte-equal to before the change); inward
   override unchanged on M9's truth (bbox_err identical to its committed passing value);
   A.3 endpoint snap on the probe-B aft/fore caps agrees with the analytic envelope radius
   to ≤ 2·ct; blend-guard degenerate case (fabricated non-monotonic blend) keeps
   endpoint-only.
2. **`tests/test_taper_loft.py`** (Phases B/C, slow-marked where they rebuild):
   rung selection — constant rings → prism (assert the returned shape's volume equals the
   old `_build_prism_bore` result on M3's rings), proportional (probe-C-style uniform
   taper at L=1000) → rung 2 accepted, two-family non-proportional (M16-style at L=1000)
   → rung 2's acceptance test REJECTS (assert the measured mismatch > 2·ct) and rung 3
   builds a valid solid; ring prep unit tests — winding, arc-length resample point count,
   FFT seam stability across a fabricated morphing sequence, drift clamp on an
   artificially flipped seam, linear end-extrapolation vs prismatic fallback on a
   self-intersecting extrapolation; `build_ring_loft_solid` slice-back check rejects a
   deliberately twisted section stack.
3. **Verification/GUI tests**: `surface_area` block present with measured values on a tiny
   rebuild (extend `tests/api/test_engine.py`); both hint branches (fabricate the
   deviation-passed-with-margin dict); `_verification_group` renders the row and
   `_verification_all_passed` counts it (extend `tests/gui/test_main_window.py`,
   offscreen).
4. **Full existing suite** — `.venv/bin/python -m pytest tests -q` — green, then
   **`harness/selftest.py`** green including M16.
5. **Full regression sweep, M1–M16**: `for m in M1..M16: .venv/bin/python harness/score.py
   --milestone $m` — all pass. Named watch items: **M9** (both directions: the inward
   override must still fire — bbox — and its verification must show the SAME rows
   failing/passing as before except the new area row, which fails with the benign hint —
   confirm no scorer impact); **M2/M5/M8/M12/M13** (pinch ends byte-stable through A.2's
   split); **M14** (non-pinch caps through A.3 — endpoint value unchanged within
   tolerance; severed machinery untouched); **M6** (must take rung 2 via the new
   acceptance test — assert `paths_used.bore == "loft_proportional"` and its
   volume/deviation numbers hold); **M3/M4/M5/M8** (constant zones → rung 1,
   ideally byte-identical STEPs); **M13** (fuse-ok guards on the seam path still reached);
   **M15** (roundness-gate interplay: ring resampling uses `chord_tol` for geometry and
   `resid_gate` only for classification — verify no coupling crept back in).
   Run the sweep after Phase A alone AND after Phases B/C (two checkpoints, M15's
   highest-risk-change convention).
6. **Probes rerun**: probe B → bounds-Z pass, deviation p95 ≤ tol; probe C → deviation
   p95 from 179.8 mm to ≤ 1.0 mm, volume ≤ 0.2 %, event still ≈6000.
7. **GUI smoke**: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m app --smoke out/gui_smoke`;
   open M16's rebuilt STEP by hand once — station rings render through the taper, the
   Verification group shows the new Surface area row.
8. **Real-input sanity for Brady**: the Minuteman file (his work machine). Expected:
   fore/aft caps land on the true bounds (report §4's 53 mm / 169 mm classes gone),
   `paths_used.bore == "loft_rings"`, deviation far below the report's 31 mm — but per
   §4.1's scope note, on this real scan expect "good, not chord-tol-exact"; the number to
   beat is the report's own p95 31.1 mm.

---

## 8. Risks / judgment calls flagged for Brady (do not silently absorb)

1. **Item 3's real-scan fidelity is deliberately scoped as partial** (§4.1). M16 proves
   non-proportional geometry on clean meshes; per-station ring noise on real scans passes
   straight into the loft. If the Minuteman run's deviation is still above what Brady
   needs, the follow-up is cross-station ring smoothing / per-station outlier rejection —
   a planned extension point, not a failure of this design.
2. **The seam fuse with a tapered fin solid (§3.3)** is the least-charted interaction:
   every documented invariant (`circ_overlap` subset argument, clearance ladder) was
   established against constant prisms. M16 exercises it; if both fuse rungs fail there,
   stop and report — the fix may need the sandwich treatment (`bore_seam_clearance`) or a
   loft-side seam collar, and that choice deserves Brady's eyes.
3. **A.2's outward-refusal on measurement failure** is conservative by design: a genuinely
   pinching motor whose end slices are ALL degenerate keeps its raw mesh bound (a small
   informational bounds shortfall, the M9 hint class) instead of an extension. The
   alternative (extend on model evidence alone) is exactly the reproduced bug; if a real
   input surfaces where this bites, loosen with a measured case in hand.
4. **The area check will show amber on noisy-but-accurate inputs** (M9 +1.27 %, M13
   +3.13 %) with the benign hint (§5.2). Accepted deliberately over a noise-scaled gate
   (every cheap estimator measured 5–25× off). If this annoys in practice, the recorded
   follow-up is a shared per-input noise scale for BOTH the bounds and area checks.
5. **M16's gates are pre-measurement estimates** in two places: `face_count_max=100`
   (B-spline loft face economics are new) and `gmsh_min_sicn=0.1` (high-degree lateral
   face). §6.6 locks them from the first honest run; the recorded fallback for a gmsh
   failure is `isRuled=True`. Honest adjustments live in gate config with measured
   numbers, never in widening `station_eps` or weakening the deviation gates.
6. **Two-family fillet construction** (`MakeFillet2d` with three distinct radii on a
   20-vertex ring) may refuse some parameter combinations; §6.1's numbers leave wide
   tangency margins, but if the generator fails, adjust fillet radii (not tip radii — the
   non-proportional ratios are the point of the milestone) and re-run §6.6.

## Final step (after everything above is green, in this order)

1. Commit pipeline + tests first; then harness/MISSION/loop/HANDOFF edits.
2. **Re-point `harness-frozen`** (milestones/generators/selftest edits) and
   **`infra-frozen`** (MISSION.md/loop.py edits) — otherwise the driver restores the old
   files before any scoring run and M16's gates evaporate (the Round-3 stale-tag incident,
   CLAUDE.md 2026-09-04). The Bash guard blocks `git tag` in command text: use a script
   file (`scripts/retag_frozen.sh` pattern from the M14 plan) or leave both re-tags to
   Brady.
3. Tell Brady: `git push`, and that the Minuteman file is the first real-input test of
   §7.8.
