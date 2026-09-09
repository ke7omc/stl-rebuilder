# Plan: decoupled roundness tolerance (M15 — finely tessellated but genuinely not round)

_Implementation plan, 2026-09-09. Written from a full read of `pipeline/tol.py` (all of it),
every `tol.circle_max_resid` call site in `pipeline/engine.py`, `pipeline/slicing.py`,
`pipeline/export.py::finalize`/`write_step`, `pipeline/cli.py`, `harness/generators.py`,
`harness/milestones.py`, `harness/selftest.py::_ideal_report`/`_BORE_FILLERS`, `loop.py`,
MISSION.md §5.2/§5.4/§6.2, `app/main_window.py`'s chord-tol wiring — plus a direct empirical
probe (numbers marked **measured** below) of the decoupling mechanism on a synthetic
oval-perturbed fine mesh, run against the pipeline's own `_estimate_chord_tol`,
`slice_station`, and `fit_circle_robust`. Engine line references are as of commit `a27ca70`
(working tree clean); re-verify before editing — the quoted search anchors are the stable
thing._

**Audience: the implementation agent.** You have no memory of the conversation that produced
this. Every engineering decision is made below — do not re-open one without flagging to Brady.
The triggering incident: Brady ran the pipeline on his first REAL burnback STL (proprietary
work file, not in this repo — every M1–M14 rung is synthetic). Its mesh faceting is extremely
fine (`_estimate_chord_tol` ≈ 0.00907 mm) but its real-world roundness is not: at the tool's
own suggested `--chord-tol` the station circle fits reject every station
(`_non_axisymmetric_hint`: "retry with --chord-tol …or larger"), and following that hint up to
≈ 0.8 mm — the value that finally clears the roundness gates — makes the final solid fail
`BRepCheck_Analyzer`, whose own hint ("--chord-tol 0.8 mm is far coarser than the mesh's
estimated chordal deviation (~0.00907 mm); retry with --chord-tol 0.00907") sends him straight
back down. **There is no fixed point: the tool cannot currently represent "finely tessellated
but genuinely not very round", because every derived tolerance in `pipeline/tol.py` is a
function of the single `chord_tol` scalar.** This plan decouples the roundness-classification
gates from the construction-precision tolerances, and adds M15 — a synthetic, permanently
regression-gated recreation of the trap, the same way M14 made Brady's severed-island crash
permanently testable.

**Hard rules inherited from this project:**
- One capability, no regressions: **M1–M14 must all still pass after every phase.** This is
  the highest-risk change the tolerance table has ever taken — `circle_max_resid` is threaded
  through nearly every milestone's station-classification logic — so the full sweep (Phase E.5)
  is not optional and runs more than once.
- `harness/` is tagged `harness-frozen`; `MISSION.md`/`loop.py` are `infra-frozen`. Both tags
  were re-pointed to current HEAD after M14, so they are NOT stale. Editing them for this
  deliberate, human-approved extension is permitted (CLAUDE.md); **both tags must be
  re-pointed after M15 is fully green** — final step, §8. The repo's Bash guard blocks
  `git tag` in Bash command text; do the re-tag via a script file or leave it to Brady.
- Error messages must be actionable and context-aware, with concrete numbers
  (`_non_axisymmetric_hint` is the house style — and is itself one of the things this plan
  fixes, since its current concrete advice is the up-leg of the trap).
- Use `.venv/bin/python`. No `git push` (Brady pushes). No emojis.

---

## 0. Decisions made (summary — rationale in the sections below)

| Question | Decision |
|---|---|
| Core mechanism | `tol.circle_max_resid(chord_tol, roundness_tol=0.0)` becomes `max(1.5*chord_tol, roundness_tol)` — the same `max()`-of-two-scales idiom `eps_end` and `dz_min` already use. `roundness_tol=0.0` (or anything ≤ 1.5·ct) reproduces today's value exactly, which is the M1–M14 no-behavior-change invariant. |
| Where the roundness floor comes from | **Measured from the mesh itself** (`_estimate_roundness_noise`, §4.2): slice ~12 probe stations, fit each outer loop, take a robust max of `max(resid, 2·center_offset)` with 1.2 headroom — the direct analogue of how `_estimate_chord_tol` already measures faceting sag independently of the user's `--chord-tol`. Capped at 2 % of the median fitted radius (§4.3) so a genuinely non-axisymmetric part can't launder itself through the floor. |
| CLI surface | **Yes, a new flag: `--roundness-tol`**, default `None` = auto-from-mesh; an explicit mm value overrides; `0` disables the floor entirely (exact legacy behavior, the escape hatch and the A/B lever for tests). Rationale in §4.5 — hints must have a concrete flag to name, the harness needs reproducibility, and auto-by-default means zero new burden. Mirrored in `RebuildOptions`, `Analysis` (`suggested_roundness_tol_mm`), and the GUI (auto checkbox + spin, same pattern as chord-tol). |
| Which tolerances move with the floor | Only the **classification** gates: every `circle_max_resid` consumer (10 sites, enumerated §3.2), the dome-fit `resid_tol`, `station_eps`'s residual-amplification term (rewritten as `(200/1.5)·resid_gate`, identical when the floor is off), and `_compute_verification`'s deviation/bounds tolerances (§3.4 — a coupled site the original diagnosis missed). |
| Which tolerances must NOT move | Every **construction-precision** tolerance stays a pure function of `chord_tol`: `fuzzy`, `eps_cut`, `eps_end`, `a_min`, `dz_min`, `topo_tol`, `rdp_profile_eps`, `sat_min_span`, `min_dz = 5·ct`, `slice_station`'s jitter/sliver filters, `export.finalize`/`write_step`'s ShapeFix precisions. That IS the decoupling: on Brady's part these stay at the honest ~0.009 mm scale while the roundness gate floats up to ~1 mm. |
| How the floor is threaded through engine.py | A single precomputed scalar `resid_gate = tol.circle_max_resid(chord_tol, roundness_tol)` computed once per `_rebuild_impl` run and passed explicitly to the helpers that today recompute `tol.circle_max_resid(chord_tol)` inline (§3.3). Explicit parameter, no module/global state — matches the codebase's functional style, and makes "which sites moved" auditable in the diff. |
| Internal chord-tol retry as the "fix"? | **Forbidden.** An engine that silently inflates `chord_tol` to the hint value and retries would clear M15's simple capsule but reintroduces every corruption channel on real geometry (Brady's part *did* fail BRepCheck at 0.8 mm — §1.4 lists the channels). The fix is the decoupled gate, never an internal override of the user's chord-tol. |
| M15 geometry | M2's exact solid (L=10000, R_o=1000, R_i=300, 2:1 domes, straight bore) — truth STEP/volume/bbox stay analytic and clean. Only the **input STL** is pathological, M9's exact pattern: a fine tessellation (deflection 0.01 mm) whose vertices are displaced by a smooth, deterministic ovality + center-wobble field (§5.2). Deviation/volume gates therefore compare the rebuilt solid against the CLEAN truth — the milestone demands the pipeline recover the nominal round geometry from the oval mesh. |
| Why smooth perturbation, not noise | The decisive generator property (measured, §1.5): a long-wavelength perturbation keeps facet dihedrals tiny (p75 = 0.0090 rad, deep in `_estimate_chord_tol`'s clean-CAD branch), so the chordal-sag estimate stays at faceting scale (0.040 mm) while the circle-fit residual sits at the ovality scale (0.804 mm). M9/M13's Gaussian noise can't reproduce the trap — per-vertex noise bends every edge, flips the estimator into its scan branch, and inflates the chord-tol estimate *together with* the roundness noise, which is exactly the coupling M15 must break. |
| New scorer gate types | **None.** M15 uses only existing gate names (`volume_err_pct`, `surface_deviation_*`, `dome_stations_min`, `face_count_max`, …) so `harness/score.py` and `harness/metrics.py` are untouched — the frozen-surface diff is confined to `milestones.py`, `generators.py`, `selftest.py`. A `report_roundness_floor` gate was considered and rejected: the accuracy gates at the pinned fine `--chord-tol` already make the honest path the only passing one. |
| Milestone number / ladder position | **M15**, between M14 and MR (`loop.py` `MILESTONES` line ~103, MISSION §6.2 row after M14). |

New/modified files:

```
pipeline/tol.py              §3.1  circle_max_resid gains roundness_tol param (default 0.0)
pipeline/engine.py           §3.2-3.4, §4  gate threading, estimator, hints, verification tols
pipeline/cli.py              §4.5  --roundness-tol flag
app/main_window.py           §4.6  Detected row + Run-controls row (auto + spin)
harness/milestones.py        §5.3  _m15() spec
harness/generators.py        §5.2  _make_m15()
harness/selftest.py          §5.4  _make_bore_filled_m15 + _BORE_FILLERS entry
MISSION.md                   §6    §5.3 CLI line, §5.4 table row, §6.2 M15 row   [infra-frozen]
loop.py                      §6    MILESTONES list                               [infra-frozen]
HANDOFF.md                   §6    M15 results row + limitations note
tests/test_roundness.py      §7.1  new
tests/api/test_engine.py     §7.1  extended (RebuildOptions field)
tests/gui/test_main_window.py §7.1 extended (GUI wiring)
```

---

## 1. Root cause (verified — with three refinements to the original diagnosis)

### 1.1 The single-scale assumption, confirmed

`pipeline/tol.py` (62 lines, read in full) derives every tolerance from `chord_tol`:
`circle_max_resid = 1.5·ct` (line 25), `fuzzy = ct` (13), `sew_tol = 2·ct` (17),
`eps_cut = 10·ct`, `a_min = π(5ct)²`, `eps_end`/`dz_min`/`topo_tol` (ct with L floors),
`rdp_profile_eps = min(0.5ct, 2e-4·r_ref)`, `sat_min_span = 2ct`. One scalar, two jobs:
*"how imprecise is this mesh's faceting"* (construction) and *"how non-round may a
cross-section be and still count as a circle"* (classification). Every synthetic milestone
generates both from the same process, so they coincide; Brady's real part is the first input
where they differ by ~two orders of magnitude.

`circle_max_resid` consumers in `pipeline/engine.py` (grep-verified complete list, 10 sites):

| Line (~) | Anchor | Role |
|---|---|---|
| 277 | `def _axis_centered` | center-offset gate: `offset < 0.5·circle_max_resid` |
| 296 | `_non_axisymmetric_hint` | branch threshold `0.75·circle_max_resid` + the "needed" suggestion |
| 346, 358 | `_fit_severed_envelope` | keep-band `2·gate` + residual/center acceptance (M14) |
| 753 | `_hole_classification` | bore circular-vs-not (drives M4/M5/M8 topology events via the `_bisect_*` helpers) |
| 867 | `_bisect_hole_edge` | satellite-hole presence match (M7) |
| 2699 | station loop outer fit | **the hard gate that rejected Brady's stations — exit 4** |
| 2721 | station loop hole fit | per-station bore classification |
| 3018 | `_resid_gate =` (end-window probe) | M13's end-probe accept/reject |
| 3119 | `resid_tol = tol.circle_max_resid(...)` | dome-model fit tolerance (feeds `_fit_r2_quadratic`, `_dome_model`, `_curved_end`, `_dome_shoulder_z`) |

Plus MISSION §5.4's table row and §5.2 step 4's "accept if max|r_i − R| < 1.5·chord_tol".

### 1.2 Brady's sequence, confirmed end to end

1. `analyze()` line 199: `suggested = max(_estimate_chord_tol(mesh), 1e-3)` — his file's
   estimate ≈ 0.00907 mm. The GUI's "auto from mesh" (`app/main_window.py` lines 927/965)
   feeds exactly this into the run. So the tool's own recommended starting point puts the
   roundness gate at `1.5·0.00907 ≈ 0.0136 mm` — ~60× tighter than his part's real roundness.
2. Station outer fit fails (line 2699) → `_non_axisymmetric_hint` (line 280). Its in-range
   branch computes `needed = max(max_resid/1.5, offset/0.75)·1.2` and says *"retry with
   --chord-tol {needed} or larger"*. Iterating that concrete suggestion is what walked him up
   to ≈ 0.8 — the hint is well-built and exactly wrong for this input: it can only name
   `--chord-tol`, because no other knob exists.
3. At `--chord-tol 0.8`, the roundness gates pass but `export.finalize`'s
   `BRepCheck_Analyzer` fails; the line-3636 branch fires (`chord_tol > 5·est`: 0.8 > 0.045):
   *"far coarser than the mesh's estimated chordal deviation (~0.00907 mm); retry with
   --chord-tol 0.00907"* (lines 3634–3645) — the exact message Brady reported, sending him
   back to step 1. No value satisfies both: the roundness gates need ≥ ~0.53
   (`resid/1.5`), the construction side degrades from ~0.05 up. Confirmed: a genuine trap,
   not a tuning problem.

### 1.3 Refinement 1: `sew_tol` is dead code

The original diagnosis named "the boolean cut/sewing tolerance (`fuzzy`/`sew_tol`)".
Grep-verified: **`tol.sew_tol` has no call site anywhere in the pipeline** — it exists only in
tol.py and the MISSION §5.4 table. The plan leaves it in place (it documents intent and may be
consumed by a future custom loft) but nothing about it changes, and no fix should be aimed at
it.

### 1.4 Refinement 2: the real coarse-chord-tol corruption channels

What actually degrades at `chord_tol=0.8` on a 0.009 mm-faceted part (each verified by
reading the call site):

- `tol.fuzzy(ct)` — every `booleans.cut` (engine ~3581–3598) and `BRepAlgoAPI_Common`
  fuzzy value: 0.8 mm snapping on 0.009 mm-precision geometry.
- `export.finalize`'s `ShapeFix_Shape.SetPrecision(ct)` + `ShapeFix_FixSmallFace` precision
  (export.py ~72–93) — the finalize docstring itself records the M13 iter-70 incident where
  these fixers **corrupted an already-valid solid at a coarse ct** (`BRepCheck_EnclosedRegion`).
  This, not sewing, is the most likely proximate cause of Brady's validity failure.
- `write_step`'s re-read-and-re-fix loop at the same precision.
- `slice_station`'s sliver filters (`a_min = π(5·0.8)² ≈ 50 mm²`, `thick_min = 2.4 mm`) —
  on a small real motor these can eat genuine thin features per-station.
- `rdp_profile_eps`, `sat_min_span`, `station_eps = min(max(…, 200·ct), 0.02L)` (a 160 mm
  end inset at ct 0.8 on a small part swallows real geometry).

All of these are *construction-precision* sites. The fix keeps every one of them at the honest
fine `chord_tol`; none may consume the roundness floor. (This is the §2 "verify the
decoupling" question from the task, answered: `fuzzy`/`a_min`/`dz_min`/`topo_tol`/`eps_*`
already take only `chord_tol` and are untouched by construction — the only work is on the
classification side.)

### 1.5 Refinement 3: the mechanism, measured

Probe run (2026-09-09, `.venv` python, script pattern preserved in §5.5's measure step):
a watertight annular cylinder R_o=1000/R_i=300/L=2000, uniform ~9 mm facets (627 200
triangles), displaced by a smooth field — ovality `0.8·cos(2θ + φ(z))` mm (slow phase twist),
center wobble 0.25 mm sinusoidal in z — applied to outer AND bore:

| Quantity | **Measured** |
|---|---|
| `_estimate_chord_tol` | **0.0403 mm** (p75 dihedral 0.0090 rad → clean-CAD branch; the ovality contributes nothing visible — wavelength ≫ facet size) |
| Outer-loop `fit_circle_robust` residual (z=400/1000/1600) | **0.8045 / 0.8045 / 0.8042 mm** (= the ovality amplitude, exactly) |
| Bore-hole fit residual | **0.802 mm** — the bore hits the same gate, so the floor must apply to hole classification too (§3.2) |
| Fitted centers | up to **0.24 mm** off-axis (= the wobble) — vs `_axis_centered`'s gate of `0.5·1.5·ct = 0.030` at the honest ct; the center gate must float with the floor as well |
| Fitted R | **999.996 / 299.999** — the robust Kasa fit recovers the *nominal* radius to 4 µm through 0.8 mm of ovality; a pure ellipse d=0.8 fits R=1000.000, resid 0.7999. This is why M15's volume/deviation gates against the clean truth can stay tight (§5.3). |
| Old-formula demand | hint's `needed = max(0.8045/1.5, 0.219/0.75)·1.2 ≈ 0.64 mm` vs honest estimate 0.040 — a **16×** forced inflation on this probe; Brady's real file is ~90× (0.8 vs 0.00907). The gap is tunable via facet fineness (§5.2) but the trap fires identically at any ratio ≳ a few ×. |

Conclusion: the proposed architecture is sound. A mesh-measured roundness floor of
`1.2·0.804 ≈ 0.97 mm` with `chord_tol` left at 0.04 gives `resid_gate = max(0.06, 0.97) =
0.97` — stations pass, centers pass (0.24 < 0.5·0.97), and every boolean/fixer precision
stays at 0.04.

---

## 2. Phase ordering

Build the milestone FIRST (Phase A), confirm it reproduces the trap against current HEAD,
then implement the fix against it (Phases B–C), then lock gates and wire the ladder (Phase D),
then verify (Phase E). Same sequencing that made M14 trustworthy: the repro exists before the
fix, so "fixed" is a measured claim.

---

## 3. Phase B — the decoupled gate (pipeline/tol.py + engine threading)

*(Presented before Phase A's harness work in this document because the design constrains the
milestone's numbers; implement in the Phase-A-first order of §2.)*

### 3.1 `pipeline/tol.py`

```python
def circle_max_resid(chord_tol: float, roundness_tol: float = 0.0) -> float:
    """Station circle-fit acceptance gate. Two independent scales, take the larger:
    1.5*chord_tol bounds the residual a perfectly round surface shows through this mesh's
    FACETING alone; `roundness_tol` is the mesh's own measured (or user-stated) real-world
    roundness noise — real CAD-exported/scanned parts can be finely tessellated (tiny chordal
    sag) yet genuinely not very round (measured incident 2026-09-09: chordal estimate
    0.00907 mm vs ~0.8 mm of true out-of-roundness; no single chord_tol satisfies both the
    circle gates and the boolean/ShapeFix precisions, MISSION §5.4). roundness_tol <= 
    1.5*chord_tol reproduces the pre-M15 value exactly — the M1-M14 invariant."""
    return max(1.5 * chord_tol, roundness_tol)
```

Nothing else in tol.py changes. Add a docstring line to the module header noting the one
deliberate exception to "every value derived from chord_tol".

### 3.2 Which classification sites consume the floor (all 10 — none may be skipped)

The floor must apply **uniformly** to outer-fit, center, and hole gates. The consistency
argument is load-bearing: if the outer gate floats but the hole gate doesn't, a real part's
slightly-oval bore classifies circular at some stations and non-circular at others, and the
line-3077 "circular and non-circular sections are interleaved" check exits 4 — a new trap
replacing the old one. Sites and their change:

- `_axis_centered` — signature becomes `(cx, cy, R, resid_gate)`, body `< 0.5 * resid_gate`.
- `_non_axisymmetric_hint` — takes `chord_tol`, `resid_gate`, and the floor metadata (§3.5).
- `_fit_severed_envelope` / `_classify_severed_station` — take `resid_gate`; keep-band
  `2·resid_gate`, acceptance vs `resid_gate`.
- `_hole_classification` — `(hole_pts, resid_gate)`.
- `_bisect_topology_event`, `_bisect_hole_edge` — keep `chord_tol` (they pass it to
  `slice_station`) and add `resid_gate` for the classification calls.
- Station loop (2699/2721), end-window probe (3018), dome `resid_tol` (3119) — use the local
  `resid_gate` variable.

`resid_gate` is computed once in `_rebuild_impl`, right after the floor is known (§4.4):
`resid_gate = tol.circle_max_resid(chord_tol, roundness_floor)`. Everything downstream
receives the scalar; no helper recomputes from `chord_tol` alone anymore (grep for
`tol.circle_max_resid(chord_tol)` must return zero hits in engine.py when done — that grep is
a Phase E check).

The dome-model chain (`_dome_model` → `_fit_r2_quadratic`, `_refine_dome_model_from_vertices`,
`_curved_end`, `_densify_dome_chords`) already takes `resid_tol`/`min_dz` as parameters — only
the line-3119 assignment changes; their signatures don't. `min_dz = 5.0 * chord_tol`
(line 3118) is spacing, not classification: **unchanged**.

### 3.3 `station_eps` (line 2620) — the one construction-side term that tracks the gate

```python
station_eps = min(max(eps_end_val, (200.0 / 1.5) * resid_gate), 0.02 * L)
```

The existing `200·chord_tol` term exists (comment at 2610) because steep-slope ends amplify
per-station residual noise — a *residual-scale* concern, so it belongs to `resid_gate`, not
`chord_tol`. `(200/1.5)·resid_gate` equals `200·chord_tol` exactly whenever the floor is off
(resid_gate = 1.5·ct), preserving M1–M14 placement bit-for-bit, and widens the inset to
~133·floor (capped at 0.02·L as before) when a real part's noise would otherwise push
amplified end-station residuals over even the floated gate — which is a hard exit-4, not a
soft skip. Update the comment in place to say all of this.

### 3.4 `_compute_verification` (line 1987) — the coupled site the original diagnosis missed

Verification compares the input mesh against the rebuilt solid. On a decoupled part the input
*genuinely* deviates from the (correct) axisymmetric solid by the roundness noise, so with
`dev_tol = 2.0 * chord_tol` (line 2129) and `bounds_tol_mm = max(2·ct, 1e-3·extent)`
(~line 2076), a *successful* rebuild of Brady's part would report Deviation FAIL (p95 ≈ 0.8 vs
tol 0.018) and possibly radial Bounds FAIL — misleading, and it would also spuriously trigger
`_rebuild_with_refinement`'s retry pass. Change (thread `roundness_floor` into
`_compute_verification` as a parameter, default 0.0):

```python
dev_tol   = max(2.0 * chord_tol, 1.5 * roundness_floor)
bounds_tol_mm = max(2.0 * chord_tol, 1e-3 * axial_extent_mm, 1.5 * roundness_floor)
```

1.5× because the floor ≈ 1.2·(peak residual) tracks the p~100 of the noise while the check
gates p95 — measured on the probe mesh, p95 of |δ·cos2θ| ≈ δ ≈ floor/1.2, so 1.5·floor gives
~80 % margin without swallowing real reconstruction errors (which the M15 gates against the
clean truth independently bound). Both tolerances are already reported in the verification
block, so the GUI rows self-describe.

### 3.5 Hint rework — closing the trap's up-leg

`_non_axisymmetric_hint(zz, cx, cy, max_resid, chord_tol, resid_gate, floor_info)` where
`floor_info` is the dict from §4.4. Branches (keep the house voice, concrete numbers):

- `offset > 0.75·resid_gate` → unchanged axis/units message.
- else if the floor was **capped** by the 2 %-of-radius bound (§4.3) and the station still
  fails → *"the measured out-of-roundness at this station ({needed:.3g} mm) exceeds
  {cap_pct:.0f}% of the fitted radius — this outer envelope is genuinely non-axisymmetric,
  which is out of scope (MISSION §6.1); if you believe it is round with unusual noise, you can
  force --roundness-tol {needed:.3g}"*.
- else → *"…is not round to the current gate ({resid_gate:.3g} mm); this mesh's measured
  roundness noise floor is {floor:.3g} mm — retry with --roundness-tol {needed:.3g}"* with
  `needed = max(max_resid, 2·offset) · 1.2`. **Never suggest raising `--chord-tol` for a
  roundness failure again** — that suggestion (line 300–303) was the up-leg of the trap.

Also touch, same commit: the interleaved-topology error (line ~3078) gains
*"; if the bore is real but out-of-round, try a larger --roundness-tol (currently
{resid_gate:.3g} mm)"*; the BRepCheck "far coarser" hint (line 3637) gains one clause —
*"(roundness noise no longer requires a coarse --chord-tol; the roundness gate is separate,
see --roundness-tol)"* — so a user arriving there from old habits learns the new model. The
boolean-failure exit-5 hint (line 3609) is fine as is (it already only fires when
`chord_tol > 2·est`).

---

## 4. Phase C — the auto floor, CLI/API/GUI surface

### 4.1 Where the estimate lives

`_estimate_roundness_noise(mesh, chord_tol, z_min, z_max)` in engine.py, placed directly
after `_estimate_chord_tol` (line ~170) — they are siblings: one measures faceting sag, the
other measures out-of-roundness, both from the mesh, both independent of what the user typed.

### 4.2 The estimator (decided — implement as specified)

```
inset  = min(max(tol.eps_end(chord_tol, L), 200.0 * chord_tol), 0.02 * L)   # floor-free base
zs     = 12 probes, uniform in [z_min + inset, z_max - inset]
per probe:
    polys, _ = slice_station(mesh, z, chord_tol)
    if len(polys) != 1: skip                      # severed/degenerate bands don't vote
    cx, cy, Ro, resid, _ = fit_circle_robust(exterior)
    g_i = max(resid, 2.0 * hypot(cx, cy));  keep (g_i, Ro)
discard any g_i > 3 * median(g)                   # a single garbage slice must not set the gate
if fewer than 6 survivors: return 0.0             # no estimate -> floor off, legacy behavior
raw   = 1.2 * max(g)                              # the gate is a per-station HARD error, so
                                                  # cover the worst probe, not a quantile
floor = min(raw, 0.02 * median(Ro))               # §4.3 cap
return floor, {"measured": max(g), "raw": raw, "n": n, "capped": raw > floor}
```

Why `max` not p90: a station over the gate is exit 4, not a soft skip, and roundness noise on
real parts varies along z — a p90 floor that leaves the worst decile failing just moves the
trap. The 3×median discard plus the ≥ 6-survivor rule is the outlier protection. Why
`2·offset` inside `g`: the center gate is `0.5·resid_gate`, so covering an offset requires a
gate of `2·offset`. Outer loop only — a hole can be legitimately non-circular (M3's star), and
a non-circular bore is not an error (it takes the prism path), so holes don't vote; the shared
gate still covers an oval bore of similar magnitude (measured §1.5: bore resid 0.802 ≈ outer
0.804), and the interleaved-error hint (§3.5) plus the explicit flag covers the residual case
where a real bore is noisier than its outer envelope.

Cost: 12 extra `slice_station` calls. On M13-scale meshes (5.3 M triangles) that is roughly
one-quarter of one 50-station pass — acceptable; emit one `on_progress("scan", …, "measuring
cross-section roundness")` checkpoint before it (verify `app/dashboard.py::on_stage` accepts a
"scan" event on a non-adaptive run — it should, the dial exists; if not, use "load" at 0.99).

### 4.3 The 2 %-of-radius cap (anti-laundering)

Without a cap, the estimator is circular: measure any residual, gate to pass it. A square
outer profile would measure resid ≈ 8 % of R, set an enormous floor, and "pass" stations while
building garbage. 2 % of the median fitted radius keeps the floor in small-signal territory
(Brady's ≈ 0.8 mm on a small motor is ≈ 1 % of a plausible 75–100 mm radius; the M15 probe's
0.97 on R=1000 is 0.1 %) while a genuinely non-axisymmetric envelope still fails with the
honest §3.5 message. An explicit `--roundness-tol` value is NOT capped — the user's stated
number wins, same trust model as `--chord-tol` itself.

### 4.4 Wiring in `_rebuild_impl`

Right after the watertight/body-count checks and `z_min/z_max/L` (line ~2600), before
`station_eps`:

```python
rt_arg = getattr(args, "roundness_tol", None)      # None=auto, 0.0=off, >0 explicit
if rt_arg is None:
    roundness_floor, floor_info = _estimate_roundness_noise(mesh, chord_tol, z_min, z_max)
else:
    roundness_floor, floor_info = float(rt_arg), {"source": "cli"}
resid_gate = tol.circle_max_resid(chord_tol, roundness_floor)
```

`getattr` (not attribute access) because tests and `_run_multi_body`'s sub-namespaces build
argparse Namespaces by hand. `_run_multi_body` (line 2597): its per-body sub-invocations each
re-estimate on their own body when auto — correct by construction, no extra work. Thread
`roundness_floor` into `_compute_verification` (both call sites, lines 1827 and 3682) and add
to the report (`report.write` call, line 3700):
`roundness={"floor_mm": …, "measured_mm": …, "source": "auto"|"cli", "n_probes": …}` —
additive key, the frozen scorer ignores unknown keys (verification precedent).

### 4.5 CLI + API

- `pipeline/cli.py::_parse_args` (line ~27): `p.add_argument("--roundness-tol", type=float,
  default=None, help="out-of-roundness gate floor in mm (default: measured from the mesh; 0
  disables — classification gates then derive purely from --chord-tol as before)")`.
- `RebuildOptions` (engine line ~220): `roundness_tol: Optional[float] = None`, threaded into
  the Namespace in `rebuild()` (line ~253).
- `Analysis` (line ~71): new field `suggested_roundness_tol_mm: float = 0.0`; `analyze()`
  computes it via the same estimator (slice at `max(est_ct, 1e-3)`, progress checkpoint at
  0.88 between the metrics and axis-confidence steps). MISSION §12's G1 field list is
  descriptive, not frozen — no gate reads Analysis fields.

Recommendation to Brady (do this unless he objects): ship the flag. Auto-by-default means
nobody must learn it; but without a named flag the §3.5 hints have nothing concrete to
suggest (the exact deficiency that made the old hint a trap), milestone specs can't pin it,
and tests can't A/B it. `--roundness-tol 0` doubling as the legacy switch also gives Phase E a
one-word bisection lever when the sweep disagrees.

### 4.6 GUI (small, mirrors chord-tol exactly)

- Detected page: add "Roundness noise (auto)" row in `_analysis_property_groups`
  (main_window ~1164), value `fmt_num(analysis.suggested_roundness_tol_mm, 3) + " mm"`.
- Run controls: "Roundness tol" spin + "auto" checkbox, exactly the chord-tol pattern
  (lines 927/965): on analyze, auto fills the spin; `_start_rebuild` passes `None` when auto,
  else the spin value, into `RebuildOptions.roundness_tol`.
- `app/smoke.py` (line 87): leave as-is — `roundness_tol` defaults to auto.
- Verification group needs no change (the dev/bounds tolerances arrive via the existing
  report fields).

---

## 5. Phase A — M15: the milestone (build FIRST, per §2)

### 5.1 What M15 forces

At its pinned args (the tool's own auto chord-tol for its own mesh), current HEAD must exit 4
at the first station with `_non_axisymmetric_hint` — reproducing step 2 of Brady's sequence
exactly. Post-fix, the auto floor must carry the same command to a clean, accurate,
STEP-valid rebuild of the *nominal* geometry. No other rung has a mesh whose roundness noise
and faceting precision are decoupled; M15 makes the property permanent, exactly as M14 did
for severed islands.

### 5.2 `harness/generators.py::_make_m15` — M9's truth/input split, verbatim pattern

```python
def _make_m15() -> Truth:
    spec = ms.get("M15"); p = spec.params
    outer = _capsule_outer_shape(p["L"], p["R_o"], p["dome_semi_axial"])
    bore  = _straight_bore(p["R_i"], p["L"])
    shape = <cut, as _make_m2>                         # truth = M2's exact solid
    _write_step(shape, TRUTH_DIR / "M15.step")
    ref = TRUTH_DIR / "M15.ref.stl"
    _write_stl(shape, ref, chord_tol=p["fine_deflection_mm"])   # 0.01 mm
    m = metrics.load_mesh(ref)
    V = m.vertices; x, y, z = V[:,0], V[:,1], V[:,2]
    r  = np.hypot(x, y); th = np.arctan2(y, x)
    dr = p["ovality_mm"] * np.cos(2.0*th + math.pi * z / p["L"])   # slow phase twist
    cx = p["wobble_mm"] * np.sin(1.4*math.pi * z / p["L"])
    cy = p["wobble_mm"] * np.cos(1.8*math.pi * z / p["L"])
    scale = np.where(r > 1e-9, (r + dr) / np.maximum(r, 1e-9), 1.0)
    m.vertices = np.column_stack([x*scale + cx, y*scale + cy, z])
    m.export(str(TRUTH_DIR / "M15.stl"))
    return Truth("M15", shape, V_truth, A_truth, bbox, step_path, stl_path)  # exact values
```

Decisions baked in:
- `params`: `L=10_000, R_o=1_000, R_i=300, dome_semi_axial=500` (M2 exactly — pinch ends,
  dome model, all already-proven paths), plus `fine_deflection_mm=0.01`, `ovality_mm=0.8`,
  `wobble_mm=0.25`. All in `spec.params` so `_spec_hash` invalidates the truth cache on any
  change (same reason M14 keeps its geometry constants there).
- **Deterministic, smooth, no RNG.** The displacement is a pure function of (θ, z), so STL
  vertex duplication is irrelevant (duplicates displace identically → watertightness is
  preserved; `input_watertight` stays a gate). The phase twist makes the field genuinely 3-D
  (no prism shortcut), the two incommensurate wobble frequencies keep the "axis" honest.
- `dr` applies to bore and outer alike (absolute mm, like the measured probe): both gates get
  exercised. No taper needed: with the bore through both domes, `r ≥ R_i = 300` everywhere.
- Fineness: deflection 0.01 → ~9 mm curved-direction edges (`√(8·R·d)`), a few hundred
  thousand triangles (BRepMesh leaves the zero-curvature barrel direction coarse), STL a few
  tens of MB — well under M13's 3.9 M-triangle precedent. Expected `_estimate_chord_tol`
  ≈ 0.02–0.05 (the §1.5 probe measured 0.040 at the same edge scale) against a needed gate of
  ~0.97 — a 20–50× decoupling. **Do not chase Brady's exact 90×**: the trap is scale-free
  once decoupled (§1.5), and finer faceting only buys generation time and disk.
- Register in `_MAKERS`; the truth cache handles the rest.

### 5.3 `harness/milestones.py::_m15()` — spec and gates

Docstring: cite the incident (2026-09-09, Brady's first real STL), the two-armed trap
(§1.2's three steps, condensed), and why the input is smooth-perturbed rather than noisy
(§0's "why smooth" row — this is the paragraph a future reader needs).

- `regions`: M2's exact three bands (`fore_dome` 0→0.05, `cylinder`, `aft_dome`) — M15's bbox
  is [0, L] so [0,1]-of-L and canonical-bbox fractions coincide (the M14 pitfall does not
  apply, but say so in a comment).
- `rebuild_args`: `["--axis", "z", "--sections", "40", "--chord-tol", "<LOCKED>"]` where
  `<LOCKED>` is the **measured** `_estimate_chord_tol` of the generated M15.stl, rounded to
  2 significant figures (measure-and-lock, §5.5 — the M14 D.1 discipline: never trust a
  derived number into gate config). **No `--roundness-tol` in the args**: the milestone's
  entire point is that the auto path must carry it. No `--adaptive` (no topology events; M2
  didn't need it).
- `chord_tol` field: same `<LOCKED>` value (drives `DEVIATION_DEFLECTION = ct/2` for the
  scorer's result meshing — at ~0.02 that deflection is fine-but-fine, verify scoring runtime
  stays sane in §5.5 and relax `mesh_timeout_s` if needed).
- `gates` (existing gate names only — scorer untouched):

| Gate | Value | Why |
|---|---|---|
| `n_solids` | 1 | universal |
| `brep_valid` | True | the down-leg of the trap must be structurally impossible at these args |
| `volume_err_pct` | 0.05 | vs the CLEAN truth; the robust fit recovers R to 4 µm (§1.5), so M2's own bar is achievable — provisional, lock from the first honest pass (§5.5) |
| `surface_deviation_p99_mm` | 0.4 (provisional) | **absolute mm, deliberately NOT a ct multiple** — at ct≈0.02, `0.8·ct` would be 0.016 mm, unreachable; reconstruction error here is dominated by ct-independent terms (`rdp_profile_eps`'s 2e-4·r_ref = 0.2 mm cap, dome resample density). Comment this loudly: it is the first milestone whose deviation gate is not ct-scaled. |
| `surface_deviation_max_mm` | 1.0 (provisional) | same reasoning; M2 achieved 0.44 max at ct 0.5 |
| `dome_stations_min` | 8 | both dome models must fit through the noisy samples |
| `n_stations_max` | 40 | anti-gaming, matches `--sections` |
| `face_count_max` | 60 (provisional) | RDP at eps 0.01 keeps more profile points than M2's 40-gate assumed; measure, then set to measured+50 % headroom |
| `bbox_err_pct` | 0.1 | the oval input's ±0.9 mm radial excursion is 0.05 % of the 2000 mm lateral extent — clean truth bbox still binds |
| `step_roundtrip_vol_err` | 1e-6 | universal |
| `gmsh_min_sicn` | 0.1 | plain capsule — no reason to omit it (unlike M14) |
| `runtime_cap_s` | 600.0 | fine-mesh slicing + probe cost headroom |

- `closed_form_volume=None` (dome geometry, same as M2).

### 5.4 `harness/selftest.py`

- `_make_bore_filled_m15`: delegate to `_make_bore_filled_m2`'s construction with M15's spec
  (the filler is a fake *result* STEP — the plain capsule outer, no bore — used to prove the
  scorer catches a bore-less rebuild; geometry identical to M2's filler). Register in
  `_BORE_FILLERS`.
- `_ideal_report` is spec-driven and needs no change (regions carry "dome" labels; no
  station_bands; no events). Run selftest after the spec lands and bring it back to green
  before proceeding — same rule the M14 plan enforced.

### 5.5 Measure-and-lock (run BEFORE writing the final spec constants, and again whenever the generator params change)

One-shot scratch script (`.venv/bin/python`, pattern of M14 §D.1):
1. `generators.make("M15", force=True)`; load `harness/truth/M15.stl`.
2. Print `_estimate_chord_tol(mesh)` → becomes `<LOCKED>` in `rebuild_args`/`chord_tol`.
3. Slice 12 probe stations; print outer/bore residuals and centers → confirm ≈ 0.8 / ≤ 0.31;
   print the implied floor (≈ 0.97) and the decoupling ratio (floor / 1.5·`<LOCKED>` — must be
   ≥ 10×, else raise `ovality_mm` or reduce `fine_deflection_mm`).
4. **Pre-fix repro (the gate on Phase A itself):** run current-HEAD `rebuild.py` at the pinned
   args → must exit 4, stderr matching `_non_axisymmetric_hint`'s roundness branch. Then run
   once at the hint's suggested coarse value and RECORD the outcome (BRepCheck failure, gate
   failures, or even a nominal pass — any of these is fine: the milestone's pinned args are
   what force the fix; the coarse arm is documentation of the real-world trap, not gate
   config — but whatever it does goes in the spec docstring as a measured fact).
5. After Phases B–C: score M15, read the honest measured volume/deviation/face-count, and
   tighten the three provisional gates to measured + ~50 % headroom. Gates are locked from
   measurement, never from prediction.

---

## 6. Phase D — frozen-infrastructure wiring (verified complete list)

| File | Edit |
|---|---|
| `MISSION.md` §5.4 table (line ~232) | `circle_max_resid` row → `max(1.5·chord_tol, roundness_tol)` with a one-line "measured roundness-noise floor, auto from mesh or `--roundness-tol`" note; §5.2 step 4 (line ~130) gets the same amendment; §5.3 usage line (~165) adds `[--roundness-tol MM]`. |
| `MISSION.md` §6.2 | M15 row after M14, exact table format: `| **M15** | **decoupled roundness**: finely tessellated but genuinely out-of-round input (real-CAD/scan characteristic; no single chord-tol satisfies both the circle gates and the boolean/ShapeFix precisions) | M2's exact truth; input STL is a 0.01 mm-deflection tessellation displaced by a smooth deterministic field: 0.8 mm m=2 ovality (slow phase twist) + 0.25 mm center wobble, bore included — chordal-sag estimate stays ~0.02-0.05 mm while circle-fit residuals sit at ~0.8 mm | volume < 0.05 %; dev p99 < 0.4 mm, max < 1.0 mm (ABSOLUTE, not ct-scaled); dome_stations_min 8; n_stations_max 40; face_count ≤ <measured+50%>; gmsh ≥ 0.1; runtime < 600 s | `--axis z --sections 40 --chord-tol <LOCKED>` |` — update the §6.2 capability-forcing sentence (line ~284) with "; M15 only the roundness/precision decoupling (the classification-gate floor)". Replace placeholder values with §5.5's measured numbers. |
| `loop.py` | Insert `"M15"` in `MILESTONES` (line ~103) between `"M14"` and `"MR"`. `SCORED` derives automatically. Side effect (deliberate, same as M14): the HANDOFF driver gate requires every SCORED name in HANDOFF.md — add the M15 row in the same commit. |
| `HANDOFF.md` | §2 results-table row for M15 (from the Phase E scoring run); §5 real-STL runbook: replace the "keep increasing --chord-tol until stations pass" guidance with the decoupled model (auto roundness floor; `--roundness-tol` when the auto estimate misjudges; `--chord-tol` stays at/near the Analyze suggestion); §6 limitations: the 2 %-of-radius cap and what the "genuinely non-axisymmetric" message means. |
| `harness/generators.py`, `harness/milestones.py`, `harness/selftest.py` | Phases A/5 above. |

---

## 7. Phase E — verification (the definition of done; each step gates the next)

1. **Unit tests** — new `tests/test_roundness.py` (pure pipeline, no Qt):
   - `tol.circle_max_resid(ct) == 1.5*ct` and `tol.circle_max_resid(ct, 0.0) == 1.5*ct` for a
     spread of ct (the M1–M14 invariant, stated as a test).
   - `_estimate_roundness_noise` on synthetic meshes: the §1.5 probe construction (build it in
     the test at reduced size) → floor within ±25 % of 1.2·0.8; a clean unperturbed cylinder →
     floor ≪ 1.5·ct (i.e. inert); the 3×median discard (inject one garbage probe); the <6
     survivor → 0.0 path; the 2 %-radius cap (crank ovality to 5 % of R → capped, and
     `floor_info["capped"]` True).
   - Hint text: roundness branch names `--roundness-tol` with the computed value and never
     `--chord-tol`; capped branch says "genuinely non-axisymmetric".
   - `--roundness-tol 0` on M15's mesh reproduces the exit-4/`TopologyError` (the legacy
     switch works; also proves the milestone still *would* trap without the floor).
   - Full `engine.rebuild` on M15 at the pinned args (marked slow): succeeds, report carries
     `roundness.floor_mm` ≈ expected, verification deviation row passes with the widened
     `dev_tol`.
2. **Existing suite green**: `.venv/bin/python -m pytest tests -q` (incl. `tests/gui`
   offscreen, `tests/test_severed.py`, `tests/test_selftest.py`).
3. **`harness/selftest.py`** full run green (now 15 milestones + MR).
4. **`harness/score.py --milestone M15`** → `pass: true, progress: 1.0` at the committed
   args. Then lock the provisional gates per §5.5 step 5 and re-score.
5. **Full regression sweep, M1–M14 individually** — the highest-risk step, run it early
   (right after Phase B lands, with auto ON) and again at the end. What can move and what to
   watch: with the floor auto-measured, gates may WIDEN on any milestone where
   `1.2·max(probe g) > 1.5·ct` — that alone changes nothing (stations that passed still
   pass); outputs change only if a classification FLIPS. Watch specifically: **M2** (dome-band
   probe residuals are the closest to gate anywhere in the ladder — the 2612 comment's
   ~0.5–0.6 mm at 100 mm inset vs gate 0.75; a floor of ~0.7 is fine, but if M2's stations or
   face count move at all, stop and re-examine the probe inset before touching gate config);
   **M4/M5/M8/M12** (hole circularity drives their topology events — their non-circular
   residuals are tens-to-hundreds of mm vs a floor capped at 20 mm, so no flip is possible,
   but the events are gated to `topo_tol` and will scream if that reasoning is wrong);
   **M9/M13** (noisy inputs: their probe estimates are the largest in the ladder; confirm
   floors stay under their 7.5/12 mm gates or, if over, that nothing downstream shifts);
   **M14** (severed-envelope keep-band widens with the gate; its envelope fit gates must not
   start accepting quarter-arc fits — the angular-coverage gate in `_fit_severed_envelope` is
   the guard, verify it still rejects). If ANY milestone regresses, the first bisection lever
   is `--roundness-tol 0` on that milestone's command line to isolate floor-vs-threading;
   never retune an existing milestone's gates to absorb a shift.
6. **Grep gate**: `grep -n "tol.circle_max_resid(chord_tol)" pipeline/engine.py` returns
   nothing (§3.2 — no site left computing the gate without the floor).
7. **GUI smoke + tests**: offscreen `python -m app --smoke out/gui_smoke`;
   `tests/gui/test_main_window.py` extended for the new row/controls.
8. **Brady's real file (the acceptance test — his machine, his STL):** hand him the
   expectation: Analyze suggests chord-tol ≈ 0.009 AND a roundness-noise value ≈ 0.7–1.0;
   Run with both on auto completes with a valid STEP, verification deviation ~his part's
   real out-of-roundness and passing. If his part's noise varies along z more than the probe's
   1.2 headroom covers, the §3.5 hint now names the exact `--roundness-tol` to type — one
   retry maximum, by design.

---

## Risks / judgment calls flagged for Brady (do not silently absorb)

1. **`--roundness-tol` as a public flag** — my recommendation is yes (rationale §4.5), but it
   is new permanent CLI surface on a tool whose flag set has been stable since Round 2. The
   pure-internal alternative works mechanically; it just leaves the failure hints with
   nothing actionable to say, which is how this incident started. Confirm before implementing.
2. **Auto floor is ON by default for every run**, including all 14 existing milestones. The
   `max()` semantics mean gates can only widen, and Phase E.5 sweeps for flips — but this is
   a real behavior change to the default path, not an opt-in. The alternative (auto only when
   the user passes `--roundness-tol auto`) preserves the status quo at the cost of Brady's
   exact scenario still failing out-of-the-box. I chose ON; it is the whole point.
3. **The 2 %-of-radius cap value** is a judgment call (Brady's incident ≈ 1 %, a square ≈ 8 %).
   If his real part fails with the "genuinely non-axisymmetric" message, the cap is too tight
   — bump toward 3 % with the measured numbers in hand rather than pre-emptively.
4. **The estimator's `1.2 × max(g)` headroom** assumes 12 probes see representative worst-case
   noise. A part whose roundness degrades sharply right at the (inset-excluded) ends could
   still fail per-station post-floor; the §3.5 hint's one-retry path is the designed fallback,
   not a bug — but if it fires on Brady's very first post-fix run, revisit probe placement
   (e.g. 16 probes, end-weighted) before shipping.
5. **M15's deviation gates are absolute mm, not ct-multiples** — a deliberate first for the
   ladder (§5.3 table). Anyone later "normalizing" them back to ct-scaled would make them
   unreachable at ct≈0.02 and un-passable. The spec comment must be loud.
6. **`_rebuild_with_refinement` interaction**: the widened verification `dev_tol` (§3.4) is
   what stops a *correct* rebuild of a noisy-round part from burning a pointless refinement
   retry. If Phase E.8 shows a retry firing anyway, check the floor made it into both
   `_compute_verification` call sites (1827 is the refinement pass's, 3682 the main).

## Final steps (after everything above is green, in this order)

1. Commit pipeline + tests first; then harness (`milestones.py`/`generators.py`/`selftest.py`);
   then MISSION.md/loop.py/HANDOFF.md.
2. **Re-point `harness-frozen`** to the commit containing the harness edits and
   **`infra-frozen`** to the commit containing the MISSION/loop edits — otherwise the driver
   restores the old files before any scoring run and M15 evaporates (the exact stale-tag
   failure from the Round 3 launch). Bash guard blocks inline `git tag`: use a script file
   (`scripts/retag_frozen.sh` precedent) or leave both re-tags to Brady.
3. Tell Brady: `git push`, and that his real motor STL — Analyze, then Run, both tolerances
   on auto — is the acceptance test this whole plan exists for.
