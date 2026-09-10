# HANDOFF v3 — STL → STEP rebuilder + desktop GUI (M1–M15 PASS, MR skipped, G1–G3 PASS)

**2026-09-09 addendum**: M15 (decoupled roundness tolerance — a real trap Brady hit on his own
motor: finely tessellated but genuinely out-of-round, so no single `--chord-tol` satisfied both
the roundness gate and the boolean/ShapeFix construction precisions) was added to the ladder and
fixed; `circle_max_resid` now takes an independent, mesh-measured (or `--roundness-tol`-stated)
roundness-noise floor alongside `chord_tol` (`pipeline/tol.py`). Real score.py run:
`pass: true, progress: 1.0`, volume err 0.0003% (gate 0.05%), dev max/p99 0.275/0.254 mm (gate
0.45/0.4, absolute — see §2). §5.5 item 2 and §6 item 2 below are updated for the new model.

**2026-09-08 addendum**: M14 (a real crash Brady hit on his own motor — end-of-burn island
severing at both dome tips) was added to the ladder and fixed; `out/score.M14.json` reports
`pass: true, progress: 1.0` (§2 has the numbers). Everything above this line describes the
Round 2/3 state as of iteration 84 and is unchanged; M14 is additive, not a revision of it.

Round 2 numbers (§2) were written at iteration 77 on commit `039291b` and are unchanged since —
`out/score.M1.json` … `out/score.M12.json` (driver's iteration-76 regression sweep,
2026-08-31 10:05–10:10) and `out/score.local.json` for M13 (2026-08-31 09:58). All fourteen
report `pass: true, progress: 1.0`. Round 3 (§7/§8) was written at iteration 83 on commit
`4198be5`: `tests/api -q` and `QT_QPA_PLATFORM=offscreen pytest tests/gui -q` both re-run clean
(7/7 passed each), a fresh `QT_QPA_PLATFORM=offscreen python -m app --smoke out/gui` exited 0
with 4 PNGs (53–208 KB) and `smoke.json.ok == true`, and the full M1–M13 selftest sweep was
last confirmed green at iteration 82 (`SELFTEST PASSED`, 102/102 checks, 0 failures,
`harness/selftest.py --skip-gmsh`, 494.9 s) on the same commit's `pipeline/` state — nothing in
`pipeline/` or `app/` has changed since that sweep ran. Nothing below is estimated; where a
number could not be measured it says so.

Iteration 84 re-verified this document's own instructions rather than the engine: the §8 launch
command was re-run from a clean directory (`--smoke out/gui_verify`, exit 0, `smoke.json.ok ==
true`, the same four PNGs at 54/65/163/209 KB, matching the §8 inventory), and the §7 scripted
example was executed verbatim (`paths_used == {'outer': 'revolve', 'bore': 'revolve'}`). Three
documentation defects found and fixed: the example needed the repo-root-on-`sys.path` note
(§7), `suggested_chord_tol_mm` was used by the example but missing from the `Analysis` field
list, and two cross-references pointed at a non-existent `§4.2`.

---

## 1. Summary

`rebuild.py` takes a solid-rocket-motor burnback-surface STL and reconstructs a true BRep solid
(STEP): it normalises the frame (units, `--axis auto`, off-origin translation), slices the mesh
at axial stations, classifies each cross-section's loops, decides per axial window whether the
outer and the bore are revolves, prisms, ruled lofts or swept slot wedges, bisects the exact z of
every topology transition, builds and boolean-combines the solids, and writes STEP plus a JSON
report.

**Proven** — on thirteen synthetic ground-truth motors of increasing difficulty (annular
cylinder → domes → star bore → finocyl → domes+fins → tapered loft → six satellite perforations
→ feature-aware adaptive stations → a noisy skewed marching-cubes input → inches/x-axis/
off-origin/small-scale → three separate bodies → near-end-of-burn cavity decomposition with
dome breakthrough → a 3.9 M-triangle unwelded noisy capstone), the rebuilt solid has the right
number of valid BRep bodies, volume error ≤ 0.203 % against closed-form truth, surface deviation
inside every gate, round-trips through STEP, and tetrahedralises in gmsh at min SICN ≥ 0.143
(gate 0.1). Runtimes are 1.6–7.7 s for everything except the two heavy-mesh cases (M9 28.9 s,
M13 279.6 s).

**Not proven** — anything on a real burnback STL. The `MR` slot exists and self-skips; no file
has ever been placed in `real_inputs/`, so **every result here is against a synthetic mesh whose
truth solid we generated ourselves**. Also unproven: bores with more than the loop topologies
M1–M13 exercise, non-axisymmetric outer walls (rejected outright, exit 4), and anything the
station-classification code has to guess at when the mesh is dirty in a way the M13 pathologies
did not cover. Treat §5's runbook as the first real experiment, not as a regression test.

---

## 2. Results

Gates in parentheses. `SICN` = gmsh tetrahedralisation min signed inverse condition number,
gate 0.1 everywhere. `dev` = surface deviation of the rebuilt STEP against the truth solid,
max / p99 in mm. Volume error is against the analytic closed-form `V_truth`.

| M | Capability forced | Vol err % (gate) | dev max / p99 mm (gate) | SICN | Stations | Bodies | Path fired (outer / bore / slots) | Faces (gate) | Runtime s |
|---|---|---|---|---|---|---|---|---|---|
| M1 | annular cylinder | 0.0043 (0.05) | 0.125 / 0.118 (0.6 / 0.4) | 0.450 | 40 | 1 | revolve / revolve / — | 5 (8) | 1.6 |
| M2 | 2:1 ellipsoidal domes | 0.0044 (0.05) | 0.265 / 0.162 (0.6 / 0.4) | 0.382 | 40 (10+10 dome) | 1 | revolve / revolve / — | 5 (40) | 1.8 |
| M3 | 6-point star bore (prism) | 0.0007 (0.1) | 0.077 / 0.072 (1.0 / —) | 0.367 | 60 | 1 | revolve / prism / — | 27 (100) | 2.1 |
| M4 | finocyl, 1 topology event | 0.0030 (0.2) | 0.213 / 0.108 (1.0 / —) | 0.301 | 80 | 1 | revolve / mixed / — | 44 (300) | 2.7 |
| M5 | domes + fins, 2 events | 0.0010 (0.2) | 0.448 / 0.190 (0.6 / 0.4) | 0.245 | 40 (12+12 dome) | 1 | revolve / mixed / — | 55 (400) | 3.8 |
| M6 | per-window **ruled loft** (tapered star) | 0.0010 (0.2) | 0.575 / 0.219 (1.0 / 0.4) | 0.363 | 60 | 1 | revolve / prism\* / — | 27 (200) | 6.3 |
| M7 | loop matching, 6 satellite bores, chain deaths | 0.0097 (0.1) | 0.126 / 0.116 (0.6 / 0.4) | 0.409 | 60 | 1 | revolve / revolve / — | 17 (60) | 2.2 |
| M8 | feature-aware **adaptive** stations, obround slots | 0.0083 (0.2) | 0.422 / 0.186 (1.0 / 0.4) | 0.306 | 80 | 1 | revolve / mixed / wedge | 77 (300) | 4.2 |
| M9 | noisy skewed marching-cubes input (σ=0.5, aniso 10/10/40) | 0.0481 (0.5) | 2.954 / 2.298 (30 / 5) | 0.186 | 80 | 1 | revolve / mixed / wedge | 82 (300) | 28.9 |
| M10 | **inches, +x axis, off-origin, ×1/40 scale** | 0.0085 (0.2) | 0.0105 / 0.0051 (0.025 / 0.010) | 0.289 | 80 | 1 | revolve / mixed / wedge | 77 (300) | 3.9 |
| M11 | **multi-body** (3 segments, one STL) | 0.0054 (0.05) | 0.125 / 0.118 (0.6 / 0.4) | 0.410 | 120 (40/body) | **3** | revolve / revolve / — | 15 (24) | 2.1 |
| M12 | near-burnout cavity decomposition, dome breakthrough | 0.0267 (0.3) | 0.448 / 0.165 (1.0 / 0.4) | 0.234 | 120 | 1 | revolve / mixed / wedge | 76 (400) | 7.7 |
| M13 | **capstone**: M12 in inches on +x, 3.9 M-tri unwelded noisy MC input | 0.2032 (0.5) | 6.087 / 2.874 (12 / 4) | 0.143 | 120 | 1 | revolve / mixed / wedge | 85 (400) | 279.6 |
| M14 | end-of-burn **island severing at both dome tips** (N disjoint outer loops per station) | 0.0039 (0.3) | 0.434 / 0.301 (1.0 / 0.4) | n/a (no gate — thin island tips, see §6) | 300 | 1 | revolve / prism / — | 67 (100) | 56.0 |
| M15 | **decoupled roundness**: finely tessellated (ct≈0.010) but genuinely out-of-round (0.8 mm ovality + 0.25 mm wobble) input | 0.0003 (0.05) | 0.275 / 0.254 (0.45 / 0.4, absolute mm) | 0.401 | 60 (14+14 dome) | 1 | revolve / revolve / — | 4 (10) | 48.9 |
| M16 | **NOT YET GREEN — see note below.** Non-proportional tapered-bore (dome-pinch-override fix + curved-endpoint snap + actual-rings loft) | 0.0167 (0.3) | 1.391 / n/a (1.0 / 0.4) — **FAILS**, see note | not reached | 160 (dome_stations_min 34) | 1 | revolve / loft_rings / — | not reached (gate 120) | 53.4 |
| MR | real burnback STL | — | — | — | — | — | — | — | **skipped — no real input** |

**M16 status (2026-09-10, second pass): still not green, but the cap-edge failure is fixed and
what remains is fully root-caused.** `surface_deviation_max_mm` is now **1.391 mm** against the
1.0 mm gate (was 2.136 mm), and the failure has MOVED: no longer the aft cap edge but one point
at z=7600.3 mid-star-zone. Every other reached gate passes: volume 0.0167% (gate 0.3%), bbox
0.0029% (gate 0.1% — the dome-pinch-override tripwire, clean), `dome_stations_min` 34 (gate 8),
`topo_events` worst-match 0.00003 mm, `paths_used.bore == "loft_rings"` (the actual-rings loft
is genuinely doing the work, not a proportional fallback). `surface_deviation_p99_mm`,
`face_count_max`, `step_roundtrip` and `gmsh_tet` are still never reached — the scorer fails
fast — but p99 was measured OUT OF BAND by replicating `metrics.surface_deviation` on the kept
artifacts: **0.4477 mm against a 0.4 mm gate**, so p99 would fail too. Full M1–M15 regression
re-run after these changes: all pass. Runtime 53.4 s (was 241.8 s).

**Fix 1 — the aft cap edge (the failure the previous note recorded).** Root cause was NOT the
end ring's fillets: the station loop insets its own placement by `station_eps` (100 mm on M16)
and the composed-cosine clustering bunches its last stations right AT that inset, so the last
real ring sat at z=9750.0 while the star bore's aft boundary (the flat cap) is at z=9850.0 —
a 100 mm band with no measured data at all. The loft's cross-section at the cap was therefore a
linear extrapolation evaluated a full 2x past the end of its own baseline. Measured (loft
section vs. the truth STEP's own section, perpendicular distance): **2.17 mm at z=9850**, worst
at a family-B lobe tip, exactly the point the scorer was failing on, while the same loft sat at
0.36–0.68 mm everywhere the stations actually cover. The previous pass's "direct measurement at
the end" never applied here: it targets `z_hi + eps_hi`, which is 5 mm PAST the mesh's own z_max
by construction (the cutter must overshoot the cap for a clean planar boolean), so the slice
always failed and always fell back to extrapolation. `_prepare_loft_rings` now adds one extra
DIRECTLY MEASURED **boundary anchor** ring an inset inside each zone boundary — z=9849.0 here,
measured **0.22 mm** from truth. The fore boundary (M16's flat topology-event wall at z=6000,
the more delicate of the two) was measured before being trusted: a slice 1 mm above the wall is
clean and lands 0.15 mm from truth vs 0.46 mm extrapolated.

**Fix 2 — a real latent bug the first fix exposed, and the more important of the two.** Rung 3's
plausibility screen accepted anything within an order of magnitude of `mean(area) * height`.
Once the anchors changed the section stack, the smooth (`isRuled=False`) fit stopped failing by
15 orders of magnitude and started failing by only **7.1x** — inside that band, so it was
ACCEPTED, and shipped a self-intersecting cutter that left the final solid **15% over volume and
26% under surface area**. The screen is now two independent checks: (1) an ENVELOPE check —
the loft's bounding box against the section point cloud's own, with the margin taken from the
data (largest point-for-point move between adjacent sections); measured, the good ruled surface
overshoots by 0.077 mm while the bad smooth one reaches r=12507 mm against a section envelope of
r=560 mm and runs to z=11325 against sections ending at z=9855; and (2) a VOLUME check against
the section stack's own TRAPEZOIDAL integral (2.2718e9 vs the ruled loft's tight-epsilon volume
2.2720e9, 0.01% — where `mean(area)*height` reads 2.40e9, biased by the deliberately-uneven
section spacing), with a deliberately loose 0.25x–4x band because `_solid_volume` is the FAST
default-epsilon call and reads 3.09e9 on that same correct solid — a 36% integration error on
the right answer. Without this, fix 1 would have shipped a broken solid.

**What is left, measured, and why it was not force-fit.**

1. **The input STL's own fidelity is the floor, and the gates sit on top of it.** M16's input is
   a ct=0.5 tessellation of the truth STEP; measured with the scorer's own metric, that input
   deviates from the truth by **max 0.9084 mm, p99 0.3361 mm pooled (0.3965 mm on the star
   surface alone)**. The gates are 1.0 and 0.4 — i.e. 10% and 1% of headroom over a floor no
   station-based rebuild can beat by reproducing the mesh. These gates were the plan's initial
   estimates (`2*ct` / `0.8*ct`, copied from the other star milestones) and were never measured
   against this geometry's own input; §6.6's measure-then-lock discipline covered only
   `face_count_max`/`gmsh_min_sicn`/`station_bands`.
2. **The remaining excess over that floor is OCCT's tessellation of our B-spline loft, not our
   geometry.** At the failing point both STEPs were sliced at exactly z=7600.32: the surfaces
   agree to **0.34 mm** overall and 0.22 mm at the failing angle (θ=54°, a valley) — yet the two
   0.25 mm-deflection tessellations are 1.378 mm apart there. The offending triangle in our own
   mesh spans ~3° (~20 mm of arc) across a 36 mm-radius valley fillet; the sag is exactly the
   1.38 mm. Exactly ONE point out of ~400 k exceeds 1.0 mm. This is the first milestone whose
   cutter is a general B-spline loft rather than a prism/revolve of analytic faces, which is why
   nothing before M16 exposed it.
3. **`M` (the section resample count, capped at 256) moves this a lot but erratically, so
   picking a value would be tuning, not fixing.** Measured against truth on the star surface
   (truth points → cutter tessellation at 0.25 mm): M=256 max 0.92/p99 0.529, M=320 0.82/0.468,
   M=512 **0.75/0.400** — all clean; but M=288 → 3.91, M=384 → 3.87, M=448 → 1.98, M=576 → 9.86
   (204 points over 1 mm), M=640 → 4.98. Thinning the sliver sections (minimum section spacing
   1 mm → 10 mm, which removes ruled faces of ~3000:1 aspect ratio) improves the max slightly
   and consistently (0.92 → 0.75 at M=256) but reproduces every one of those M failures to four
   decimals, so section spacing is not the cause. The plan's §4.3 self-intersection screen on
   each fitted section curve is genuinely missing from `build_ring_loft_solid`, but implementing
   it as written does not separate the good M values from the bad (shapely `is_simple` on a
   densely sampled fitted curve fires on M=320 and M=512 too, at overshoots of 0.000–0.089 mm).
4. **Beating the input floor is nonetheless possible, and that is the recorded next step.** At
   M=512 the reconstruction is measurably CLOSER to truth than the input mesh is (max 0.7472 vs
   0.8847, p99 0.3995 vs 0.4144) — the smooth fit recovers curvature the mesh's flat facets
   lose. So the honest route to green is a principled M-selection rule (a measured chordal
   criterion against the source ring, not the arbitrary 256 cap — note the plan's own formula
   `64*n_lobes` wants 320 here and the cap truncates it) PLUS a validity screen that actually
   discriminates, so a pathological M is rejected rather than shipped. Both are real work, not a
   constant to nudge. The alternative, and probably the better long-term answer, is arc/line
   fitting per station ring instead of a B-spline through raw slice points — the same trick the
   circular paths already use to beat their input mesh, and it would tessellate exactly.

Nothing here was force-fit: no gate was loosened, no deviation check weakened, no M chosen
because it happened to pass. `harness-frozen`/`infra-frozen` should still NOT be re-pointed
until this is resolved or a decision is made to accept it — and the first decision to make is
whether M16's deviation gates should stand at 1.0/0.4 given a 0.908/0.336 input floor.

Also worth knowing: implementing this exposed and fixed a REAL, unrelated regression risk in
`_fuse_sandwich_bore` (M5/M8's cavity-decomposition fallback) — an earlier version of this work
wired that function's fin-cutter through the new tapered-bore selector too, and on M8's real
merged bore+slot ring (in the `_build_slot_wedges`-fails fallback path only), the selector's
constant-cross-section threshold was fooled by ~7% real fillet-transition-band noise into
routing a plain constant prism through the actual-rings loft — a shape class it isn't designed
for, producing a 400,000+-entity pathological STEP export and multi-minute hangs where the
correct behavior takes ~3 seconds. That wiring was reverted; `_fuse_sandwich_bore` keeps its
original hardcoded prism. M5/M8 re-verified passing at their normal milestone args afterward.

\* **M6's `paths_used.bore` label is wrong-ish and you should know it.** `pipeline/cli.py:2152`
emits `"prism"` whenever any bore ring exists; the string `"loft"` is never emitted by the
reporter. M6 genuinely lofts — `BRepOffsetAPI_ThruSections(isSolid=True, isRuled=True)` at
`pipeline/solids.py:534` / `:588` — and that is what its 0.001 % volume error on a ×1.5-tapered
star proves. The report just cannot tell you which of the two builders ran. Fix in Round 3.

**Extra per-milestone checks that also passed** (not in the table):

| M | Check | Value | Gate |
|---|---|---|---|
| M10 | `frame_axis_err_deg` | 0.0000049 | 0.1 |
| M10 | `axial_extent_err_mm` | 0.00044 | 0.05 (4·ct) |
| M11 | `per_solid_volume_err_pct` | 0.0045 / 0.0075 / 0.0045 | 0.05 each |
| M12 | `min_edge_mm` | 49.91 | ≥ 0.1 |
| M13 | `frame_axis_err_deg` | 0.00033 | 0.1 |
| M13 | `axial_extent_err_mm` | **7.715** | 8 — thin margin, see §6 |
| M13 | `min_edge_mm` | 9.60 | ≥ 0.1 |
| M13 | `surface_deviation_p99_by_region` (breakthrough band) | **3.923** | 4 — thin margin |
| M14 | `dome_stations_min` (fore_dome / aft_dome) | 108 / 108 | ≥ 8 |
| M14 | `station_bands` (fore_islands / aft_islands) | 15 / 15 | ≥ 10 each |
| M14 | `topo_events` (worst match error, [250.42, 9749.58] mm) | 0.0017 | ≤ 2.0 (topo_tol) |
| M14 | `surface_deviation_p99_by_region` (worst band: aft_dome) | 0.364 | 0.4 |
| all | `step_roundtrip` | ≤ 7e-15 mm | 1e-6 |

Reference volumes, if you want to compare against what SpaceClaim reports (mm³, analytic truth):
M1 28 588 493 148 · M2 27 547 756 121 · M3 28 077 249 565 · M4 27 584 186 713 ·
M5 26 668 987 991 · M6 26 129 965 389 · M7 27 269 024 233 · M8/M9 20 595 735 261 ·
M10 321 808.36 · M11 24 669 356 312 · M12/M13 15 370 275 928 · M14 ≈ 15 775 189 000
(no closed form — M14's truth is graded against the analytic STEP's own OCCT-computed volume,
not a formula, same as every dilated-cavity milestone from M12 on).

---

## 3. Artifacts

**All artifact directories (`out/`, `logs/`, `harness/truth/`) are gitignored** — they exist on
the machine that ran the loop and are *not* in the repository. If you cloned this repo they will
be empty; regenerate any milestone with the command in §3.2.

### 3.1 Paths (on the loop machine)

For each milestone `Mk` (k = 1…13):

| What | Path |
|---|---|
| Winning output STEP | `logs/Mk-final-step.step` |
| Its pipeline report JSON | `logs/Mk-final-report.json` |
| Its stdout/stderr log | `logs/Mk-final-pipeline_log.log` |
| Truth solid (STEP) | `harness/truth/Mk.step` |
| Truth input mesh (STL) | `harness/truth/Mk.stl` |
| Truth metadata (`V_truth`, `A_truth`, hash) | `harness/truth/Mk.json` |
| Last scorer verdict | `out/score.Mk.json` (M13: `out/score.local.json`) |
| Last scorer's own STEP/report/log | `out/Mk.step`, `out/Mk.report.json`, `out/Mk.pipeline.log` |

M9 and M13 additionally have `harness/truth/M9.ref.stl` / `M13.ref.stl` — the clean reference
tessellation used for deviation, distinct from the noisy `Mk.stl` fed to the pipeline.
`out/score.json` holds the MR verdict (`skipped: "no real input"`).

### 3.2 Exact commands

The scorer runs `rebuild.py` with the arguments below (input/output paths are scorer temp files;
substitute the truth STL and any output path to reproduce by hand). All use
`.venv/bin/python rebuild.py`:

```
M1   <in.stl> --axis z    --sections  40 --chord-tol 0.5    -o <out.step> --report <rep.json>
M2   <in.stl> --axis z    --sections  40 --chord-tol 0.5    -o ...
M3   <in.stl> --axis z    --sections  60 --chord-tol 0.5    -o ...
M4   <in.stl> --axis z    --sections  80 --chord-tol 0.5    -o ...
M5   <in.stl> --axis z    --sections  40 --adaptive --chord-tol 0.5    -o ...
M6   <in.stl> --axis z    --sections  60 --chord-tol 0.5    -o ...
M7   <in.stl> --axis z    --sections  60 --chord-tol 0.5    -o ...
M8   <in.stl> --axis z    --sections  80 --adaptive --chord-tol 0.5    -o ...
M9   <in.stl> --axis z    --sections  80 --adaptive --chord-tol 5      -o ...
M10  <in.stl> --axis auto --units in --sections 80 --adaptive --chord-tol 0.0125 -o ...
M11  <in.stl> --axis z    --sections  40 --chord-tol 0.5    -o ...
M12  <in.stl> --axis z    --sections 120 --adaptive --chord-tol 0.5    -o ...
M13  <in.stl> --axis auto --units in --sections 120 --adaptive --chord-tol 8      -o ...
```

Note M11's `--sections 40` yields `n_stations: 120` in the report — 40 stations **per body**.

To re-score a milestone end to end (rebuild + all gates), from the repo root:

```
.venv/bin/python harness/score.py --milestone M13 --out out/score.local.json
```

Read `out/score.local.json`: `pass`, `progress`, `first_failure.hint`, and the `checks` array.

---

## 4. SpaceClaim checklist

Do this per milestone STEP (`logs/Mk-final-step.step`).

**4.1 Every milestone**

1. **Import** the STEP (File → Open, or Insert → File). Choose millimetres if asked; the STEP is
   written in mm for every milestone including the inch cases.
2. **Body count.** Structure tree → count solids. Expect **1** for every milestone **except
   M11, which must show exactly 3 separate solids** (segments A z∈[0,3000], B z∈[3500,6500],
   C z∈[7000,10000] — they do not touch). If M11 shows one body or four, the multi-body split
   at `pipeline/cli.py:1249` mis-grouped the shells.
3. **Solid, not surface.** Each entry must be a *Solid* in the tree, not a surface/patch set.
   Check the Properties panel reports a Volume; a surface body will not.
4. **Features present** — spin the model and confirm, per milestone:
   - M1, M2: one straight through-bore; M2 additionally has a rounded (2:1 ellipsoidal) cap at
     both ends.
   - M3, M6: a 6-point star bore. On **M6 the star must visibly grow** from the fore end to the
     aft end (×1.5 in radius); if it is a constant star, the loft path silently degraded to a
     prism and the milestone number in §2 is not what you are looking at.
   - M4, M5: a circular bore forward, transitioning to fins at z ≈ 6000 (M5 also at z ≈ 9500).
   - M7: the central bore plus **6 satellite holes** at r = 600, every 60°, that stop at
     z = 7000 against a flat end wall.
   - M8, M9, M10, M12, M13: a central bore plus **8 obround slots** with filleted end edges. On
     M12/M13 the aft slots **break through the dome** (open to outside) beyond z ≈ 9656.
   - M11: three separate annular segments, inner radii 300 / 450 / 300.
5. **Volume.** Properties → Volume, compare to the reference volume in §2. It should agree to
   within that milestone's volume gate (worst case M13, 0.5 %).
6. **Mesh it.** Fluent Meshing or SpaceClaim's own check: generate a tet mesh with default
   settings. It must complete with no failed regions. Our gmsh min SICN per milestone is in §2;
   the worst is M13 at 0.143.

**4.2 Extra checks for the inch / rotated-frame cases (M10 and M13)**

These two inputs are STLs written in **inches** on a **+x** motor axis with an off-origin
translation. The pipeline undoes the frame transform before export, so:

7. **Units.** Measure the overall length. **M10 must be ≈ 247.33 mm** (not 9.737 — that would
   mean the STEP was written in inches) and **M13 must be ≈ 9835 mm**. If SpaceClaim shows the
   number but with an "in" unit label, the STEP header units are wrong.
8. **Orientation preserved.** The output must be in the *input's* frame, not re-normalised to z.
   Both parts must lie with their **motor axis along +x**, translated to the input origin
   (M10: (254, −76.2, 101.6) mm; M13: (2500, −700, 1300) mm). A model sitting on the z-axis
   through the origin means `export.undo_axis_transform` did not run.
9. **Not mirrored.** Confirm the fore dome (the end with the *bore breakout*, small end wall)
   is at **low x**, not high x. This is the exact bug that cost iteration 76: `--axis auto`
   returned an eigenvector with an arbitrary sign, and the whole part was reported end-for-end
   even though the geometry was right. The fix canonicalises the sign, but a mirrored real part
   is the first thing to check if station/event z-values look nonsensical.

**4.3 Extra check for the multi-body case (M11)**

10. Select each of the three solids in turn and read its Volume. Per-solid volume error is gated
    at 0.05 % and measured 0.0045 / 0.0075 / 0.0045 %. Confirm the three are **disjoint** (no
    shared faces; there are 500 mm gaps between segments).

---

## 5. Real-STL runbook v2

### 5.1 Estimate `--chord-tol` first

`chord_tol` describes **how faithful the input mesh is**, not how much error you will tolerate in
the output. Estimate it as the **median triangle edge length of the STL** — that is exactly what
the MR scorer uses (`ct_est = median edge length`):

```
.venv/bin/python -c "
import trimesh, numpy as np
m = trimesh.load('real_inputs/yours.stl')
e = np.linalg.norm(m.vertices[m.edges[:,0]] - m.vertices[m.edges[:,1]], axis=1)
print('median edge mm:', np.median(e), ' p90:', np.percentile(e,90))"
```

If the STL is in inches, multiply by 25.4 before using it (the CLI's `--units` converts the mesh,
but you are computing this yourself). Sanity band from the milestones: a clean CAD tessellation
gave 0.5, a 10 mm-voxel marching-cubes mesh gave 5, an 8 mm-voxel one gave 8. Too small and the
pipeline over-fits mesh noise into extra faces; too large and it simplifies real features away.

### 5.2 The recommended first command

```
.venv/bin/python rebuild.py real_inputs/yours.stl \
    --axis auto --units mm --sections 120 --adaptive \
    --chord-tol <ct_est> \
    -o out/real.step --report out/real.report.json
```

Change `--units` to `in` if the file is in inches (STL carries no units; you must know). Start at
`--sections 120` with `--adaptive`; that is M12/M13's configuration and the most capable one.

### 5.3 What to read in the report JSON

The report contains exactly these keys: `n_stations`, `stations_z_mm`, `paths_used`,
`topology_events_z_mm`, `frame` and `axial_extent_mm`.

- **`frame.axis`** — the resolved motor axis as a unit vector in the *input's* frame. Check it
  matches your expectation (e.g. `[1,0,0]` for an x-aligned part). Its sign is canonicalised so
  the largest-magnitude component is positive; if that convention disagrees with your part's
  fore→aft direction, every z in the report is measured from the other end. **Recount before you
  conclude anything is misplaced** (see §4 item 9).
- **`frame.origin_xy_mm`** — the axis's off-origin offset. Large unexpected values mean the axis
  fit latched onto the wrong principal direction, usually because the part is nearly isotropic or
  because noise islands dragged the inertia tensor.
- **`frame.units`** — echoes what you passed to `--units`. Confirm it.
- **`axial_extent_mm`** — the part length. **This is the fastest units check there is:** if it is
  25.4× or 1/25.4× what you expect, `--units` is wrong. It is measured from the mesh bounds, so
  a noisy mesh inflates it slightly (M13 reads 7.7 mm long on a 9835 mm part).
- **`stations_z_mm` / `n_stations`** — where sections were actually taken, measured from the fore
  end along `frame.axis`. With `--adaptive` these should cluster in the domes and around feature
  edges, not be uniform. If they *are* uniform, adaptive placement found nothing to steer on.
- **`topology_events_z_mm`** — the bisected z of each circular↔non-circular transition. Compare
  against where you know the grain's features start and stop; these are the numbers most likely
  to expose a wrong-direction axis.
- **`paths_used`** — `{outer, bore, slots}`. `outer` is always `revolve` today.
  `bore` is `revolve` (circular all the way), `prism` (a non-circular bore, possibly lofted — see
  the §2 footnote) or `mixed` (both, split at a topology event). `slots` appears as `wedge` only
  when the slot-lobe cutter path fired.

**There is no `bodies` key and no `status` field in the report.** Body count is only visible in
the output STEP (and in stderr, `pipeline/cli.py:1262`, when a multi-body input is rejected).

### 5.4 What a failure tells you — the exit-code taxonomy

`rebuild.py` never raises to the caller; it returns a code and explains itself on stderr. **On
any non-zero exit no report is written**, so an absent `--report` file *is* the failure signal.

| Exit | Meaning | Where |
|---|---|---|
| 0 | success | — |
| 2 | unexpected exception (traceback tail on stderr) | `cli.py:2171` |
| 3 | input-mesh / body-count problem: components could not be split, or a component is not watertight | `cli.py:1263, 1273, 1330` |
| 4 | section topology problem: no section recovered at a z; not exactly one outer loop; the outer loop is not an axis-centred circle; zero interior holes; more than one axis-centred hole; hole topology inconsistent across stations | `cli.py:1372–1665` |
| 5 | the final solid failed `BRepCheck_Analyzer` validity | `cli.py:2128` |

### 5.5 The three most likely failure modes on a real part, and the knob for each

1. **Wrong units or wrong axis → exit 4 everywhere, or a plausible STEP at 25.4× scale.**
   Symptom: `axial_extent_mm` off by a factor of 25.4, or `frame.axis` not matching the part.
   **Knobs: `--units {mm,in,m}` and `--axis`.** `--axis` accepts `auto`, a named axis
   (`x`/`y`/`z`), **or an explicit comma-separated vector** (e.g. `--axis 1,0,0` or
   `--axis 0.0,-0.7071,0.7071`; it is normalised for you — `pipeline/io.py::parse_axis`).
   Prefer an explicit axis over `auto` whenever you know the answer — `auto` is an
   inertia-tensor fit and is the single most fragile stage on a dirty mesh. Check `frame` in
   the report before looking at anything else.

2. **The outer wall is not axisymmetric, or a station lands somewhere with the wrong loop count
   → exit 4 ("expected 1 outer loop", "not an axis-centered circle", "more than one
   axis-centered hole").** Some of these are hard rejections (a genuinely non-axisymmetric case
   is not supported at all, §6), but a surprising number are a *station in the wrong place* —
   right at a breakthrough edge, or in a gap between features. **Knob: `--sections`** (raise it,
   e.g. 80 → 120 → 200; it moves every station and usually moves off the pathological z) and
   **`--adaptive`** (on by default in the recommended command; turning it off is worth one try
   if adaptive placement is what put a station on the edge). Note the milestones are all
   ≤ 120 sections; higher values are untested for runtime.
   **"not an axis-centered circle" specifically (2026-09-09, decoupled roundness fix, M15):**
   this is NO LONGER a `--chord-tol` problem. Brady's own first real STL hit exactly this — its
   mesh faceting is extremely fine (~0.009 mm) but its true roundness needed ~0.8 mm of slack,
   and no single `--chord-tol` value could satisfy both the roundness gate and the boolean/
   ShapeFix construction precisions (raising `--chord-tol` cleared the roundness gate but then
   failed `BRepCheck_Analyzer`; lowering it back sent you in a circle). `--chord-tol` should stay
   at/near what Analyze suggests (the mesh's honest faceting precision); leave `--roundness-tol`
   on **auto** (the default) and it measures the real out-of-roundness from the mesh itself. Only
   pass `--roundness-tol` explicitly if the auto estimate under- or over-judges (the hint on a
   failing station names the exact value to try); `--roundness-tol 0` restores the old
   `chord-tol`-only behavior if you need to bisect whether a failure is roundness-related at all.

3. **Mesh noise gets reconstructed instead of smoothed, or real features get simplified away →
   the run succeeds but volume/deviation are bad, or the face count explodes.**
   Symptom: far more faces than the ~15–85 the milestones produce, or a visibly lumpy STEP.
   **Knob: `--chord-tol`.** Too low reproduces noise; too high eats features. Re-measure per
   §5.1 and bracket it ×2 / ÷2. Two traps we hit and you will too: (a) do **not** judge the
   mesh's dimensional fidelity from its bounding box — noise makes the bbox read oversized while
   the mean radius reads undersized; (b) an exact-SDF marching-cubes input can be *radially
   scaled* relative to the real part (M13's was ×0.99866), which is a volume-error floor no
   reconstruction can remove.

Also available but effectively untested: **`--refine-bands`** (never exercised by any passing
milestone) and **`--stl <path>`** (writes a tessellation of the output solid alongside the STEP —
useful for eyeballing the result against the input in a mesh viewer).

---

## 6. Known limitations

1. **No real burnback STL has ever been through this.** MR is `skipped`. Every claim in §2 is
   against a mesh we synthesised from a solid we authored.
2. **The outer wall must be axisymmetric.** `paths_used.outer` is `revolve` in every milestone;
   a station whose outer loop is not an axis-centred circle is a hard exit-4 reject
   (`cli.py:1386`). Real cases (external insulation steps, non-round cases) are not supported.
   As of M15 (2026-09-09), "axis-centred circle" no longer means "round to `1.5*chord_tol`" —
   the gate floats up to a mesh-measured (or user-stated) real out-of-roundness floor
   (`--roundness-tol`, auto by default), so a finely tessellated but genuinely slightly-oval part
   (a real CAD export or scan) now passes. That floor is capped at **2% of the fitted radius**
   (`_estimate_roundness_noise`) so a GENUINELY non-axisymmetric envelope (an ellipse, a
   polygon, external steps) still can't launder itself through — it hits a distinct hint
   ("...is genuinely non-axisymmetric, which is out of scope") rather than the old vague circle-
   fit failure. If a real part legitimately needs more than 2% before Brady is confident it's
   still "round with unusual noise", that message tells you the exact number to force via
   `--roundness-tol` — but a failure past the cap is much more likely a real non-axisymmetric
   part than noise, and should be treated as out of scope, not bisected around.
3. **M13's margins are thin.** `axial_extent_err_mm` 7.715 / 8, `surface_deviation_p99` in the
   breakthrough band 3.923 / 4, gmsh min SICN 0.143 / 0.1. The extent one has a known clean fix
   if it bites: measure axial extent from the coarse section-area sweep (MISSION §5.5.2) instead
   of `mesh.bounds`, which the input's σ = 0.8 mm noise inflates.
4. ~~The report's `paths_used` cannot distinguish a prism from a loft~~ **Fixed 2026-09-09**
   (`docs/plans/tapered_bore_dome_pinch_and_surface_area.md` §3.1): `paths_used.bore` now
   reports `"prism"` / `"loft_proportional"` / `"loft_rings"` honestly, from the new shared
   `_build_tapered_bore_cutter` selector. Still no `bodies` or `status` field on a failed run
   (§5.3).
5. **`--refine-bands` is a no-op** in every passing configuration. (`--adaptive` is *not* a no-op
   — contrary to the Round 1 handoff — it places 14 stations in a 295 mm feature band and 22 per
   dome inside M13's 120-station budget.)
6. **Runtime scales badly with triangle count.** 3.9 M triangles took 279.6 s against a 900 s
   gate; the gates were sized for these meshes, and a 20 M-triangle scan is not characterised.
7. **`--axis auto` sign is a convention, not a measurement.** It canonicalises to
   "largest-magnitude component positive". That is *a* rule, and it may disagree with your part's
   fore→aft sense; the scorer does not implement MISSION §7.2's dot<0 flip, so the pipeline owns
   the convention. See §4 item 9.
8. **Station count is capped by what was tested.** No milestone exceeds 120 sections (M14 is the
   exception at 300 — see item 9).
9. **M14's severed-island support (2026-09-08) is real but narrow.** A station whose
   cross-section splits into N disjoint SIMPLY-CONNECTED islands (an end-of-burn breakthrough at
   a dome tip) is only accepted as a CONTIGUOUS run touching the fore and/or aft end of the
   sampled sequence; the same topology appearing mid-part is still a hard exit-4 reject (a
   multi-body input or a genuine mid-grain feature, neither implemented). M14's own truth is
   flat-CAPPED 30 mm short of each island's exact zero-width pinch (the raw cusp is a valid but
   unmeshable BRep — gmsh "Invalid boundary mesh" regardless of fillet size, confirmed across 5
   variants by the geometry task) — the pipeline inherits that same clipped-end shape for free
   (its cutter already spans the part's full length), but has never been exercised on a REAL,
   UNCLIPPED motor that ends at (or in mesh noise around) the true burnthrough pinch: expect
   near-`a_min` island slivers and possibly empty end slices right at the tip (the empty-slice
   degenerate-severed placeholder exists for exactly this, untested against a real file) or a
   real pinch the flat-cap radius logic doesn't model. Brady's own incident motor is the first
   real test of this — see MR (item 1).
10. **The actual-rings loft (M16, rung 3 of `_build_tapered_bore_cutter`) is a full fix only for
    clean, smoothly-varying non-proportional geometry — not yet for real-scan noise, AND M16
    itself is not fully green yet even on clean input (see §2's M16 row: one deviation gate
    misses by 2.1x, localized at the aft cap edge).** On a real input, per-station ring noise
    passes straight into the loft undamped (there is no cross-station averaging the way the dome
    model gets via `_refine_dome_model_from_vertices`); expect "good, not chord-tol-exact"
    fidelity at best, not the numbers M16 gets on a clean tessellation. A per-station outlier-
    rejection / cross-station ring-smoothing pass is the
    recorded follow-up if this bites on a real motor with a genuinely non-proportional bore/fin
    (deliberately deferred, `tapered_bore_dome_pinch_and_surface_area.md` §4.1). Separately: this
    milestone is also what forced `harness/metrics.py::read_step` and `pipeline/engine.py`'s own
    verification volume check onto `BRepGProp.VolumeProperties_s`'s tight-epsilon (`1e-6`)
    overload instead of the library default — the default under-integrates a high-degree (8)
    B-spline lateral surface by several percent (a pure numerical-integration artifact, not a
    geometric error; see §2's M16 row and its footnote). That tight call costs real time (~6 s
    measured on M16's own solid, vs ~0.03 s at the default) — deliberately paid only once per
    rebuild (the final verification) and in the frozen scorer's own volume checks, NOT inside
    `_solid_volume` (used repeatedly during construction for cheap plausibility checks, where the
    default epsilon's few-percent error is irrelevant against a >=10x sanity threshold). If a
    future milestone's geometry needs the accurate volume somewhere hotter in the construction
    loop, don't reach for the tight epsilon by default — profile first.

### What to try next (engine)

- Decouple the report from the builders: emit `loft` vs `prism` honestly, add `bodies`, and add a
  `status` field so a failed run still produces a report.
- Replace the `mesh.bounds` axial extent with the section-area sweep (limitation 3).
- Actually implement `--refine-bands`, or delete the flag.
- Characterise the non-axisymmetric-outer case; today it is a reject, not a fallback.
- Get a real STL into `real_inputs/` and run MR. That is the highest-value single action left.

---

## 7. `pipeline.engine` — the callable API the GUI uses (built in Round 3, G1)

Round 2's gap analysis (below the line) asked for this module; it now exists at
`pipeline/engine.py` and is what `app/` calls. `rebuild.py`/`pipeline/cli.py` are unchanged in
behaviour — `pipeline/cli.py::_run` is now a thin wrapper that builds the same `argparse`
namespace `engine._rebuild_argparse` has always executed, so every number in §2 still applies
byte-for-byte (re-verified: iteration-82's full 102/102 selftest sweep ran *after* the G1
extraction).

**`analyze(input_path, axis="auto", units=None) -> Analysis`** — loads/repairs the mesh and
reports the frame WITHOUT building geometry: `frame_axis`, `origin_xy_mm`, `axial_extent_mm`,
`body_count`, `is_watertight`, `triangle_count`, `median_edge_length_mm`, `suggested_chord_tol_mm`
(== `median_edge_length_mm`; the §5.1 "auto from mesh" chord-tol suggestion, and what the GUI's
"auto from mesh" checkbox fills in), `axis_confidence` (0..1), `units`, `n_dropped_islands`,
`bounds_mm`. Cheap — this is what fills the GUI's Detected node before a Run is committed to.

**`rebuild(opts, on_progress=None, cancel=None) -> Result`** — runs the full pipeline.
`opts` is a `RebuildOptions` dataclass mirroring the CLI's argparse fields exactly (`input_stl`,
`output`, `axis="z"`, `units="mm"`, `sections=40`, `refine_bands=None`, `adaptive=False`,
`chord_tol=0.5`, `report=None`, `stl=None`). `on_progress(stage: str, frac: float, message: str)`
fires at coarse stage boundaries and once per station. `cancel()` is polled between stations; if
it returns `True`, `RebuildCancelled` is raised. On success, returns `Result(report: dict,
output_path: str, stl_path: str | None)`. On failure it raises one of four typed exceptions,
each carrying `.exit_code` matching the CLI's exit-code taxonomy (§5.4): `UsageOrCrashError`
(2), `InputError` (3), `TopologyError` (4), `GeometryError` (5) — replacing "parse stderr text"
with real exception types and messages.

**Minimal scripted-use example** (no GUI, no subprocess). `pipeline` is a plain package in the
repo root, not an installed distribution, so the repo root must be on `sys.path`: run the script
with the repo root as the working directory (or set `PYTHONPATH=/path/to/stl-rebuilder`).
Relative paths like `harness/truth/M2.stl` below are likewise relative to the repo root — from
anywhere else, `from pipeline import engine` fails with `ModuleNotFoundError`. Verified verbatim
at iteration 84.

```python
from pipeline import engine

a = engine.analyze("harness/truth/M2.stl")
print(a.frame_axis, a.axial_extent_mm, a.suggested_chord_tol_mm)

opts = engine.RebuildOptions(
    input_stl="harness/truth/M2.stl", output="/tmp/out.step",
    axis="z", sections=40, chord_tol=0.5, report="/tmp/out.report.json",
)
result = engine.rebuild(
    opts,
    on_progress=lambda stage, frac, msg: print(f"{stage} {frac:.0%} {msg}"),
)
print(result.report["paths_used"], result.output_path)
```

**What is still NOT built** (honest gaps, unchanged from the Round 2 analysis, listed here so a
future round doesn't have to re-derive them): no `--progress-json` CLI flag (the callback is
in-process only — `app/` doesn't need a subprocess, so this was never built); the report has no
`status`/`failure` block for a failed run (limitation 4 below still applies — a raised exception
carries the detail, but nothing is written to disk on failure); no first-class intermediate
result exposing pre-solid section polygons for preview (the GUI's viewport draws the *input*
mesh and station rings from the analysis/report instead, which turned out to be sufficient for
the G3 design bar); in-process repeat-call determinism was not explicitly tested (each `--smoke`
run and each GUI session so far has only called `rebuild()` once per process).

---

## 8. Round 3 — the desktop GUI (`app/`)

**Launch, from source, either OS — no packaging, no .exe.** Full setup is `WORK_SETUP.md`,
whose §6 is the authoritative, tested launch contract; the short version:

```
.venv/bin/pip install PySide6 pyvista pyvistaqt pyqtgraph qtawesome   # (.venv\Scripts\pip on Windows)
.venv/bin/python -m app                       # launches the window
.venv/bin/python -m app --smoke out/gui       # headless self-check: screenshots + smoke.json, exit 0
```

`--smoke` works under `QT_QPA_PLATFORM=offscreen` (what the driver and CI-style checks use; no
display attached, e.g. a headless machine) and without it (a real desktop). It generates
`harness/truth/M2.stl` on demand if missing, runs `analyze()` then `rebuild()` through the real
`QThread` worker path (`app/worker.py`), and writes:

| File | What it is |
|---|---|
| `out/gui/smoke.json` | `{"ok": true, "screenshots": [...], "report": {...}}` — the M2 rebuild report |
| `out/gui/01_launch.png` | Main window at launch — empty-state viewport, Input panel visible |
| `out/gui/02_analyzed.png` | After Analyze — input mesh in the viewport, Detected node populated |
| `out/gui/03_rebuilt.png` | After Run — rebuilt solid over the faded input mesh, legend, result manifest |
| `out/gui/04_stations.png` | Stations node selected — per-station table (z, loops, R_outer, R_bore, class) |
| `out/gui/M2_rebuilt.{step,report.json,preview.stl}` | The actual rebuild output from the smoke run |

All four PNGs are 53–208 KB (gate: ≥ 20 KB, i.e. not a blank/black frame). Re-run the command
above any time to regenerate them for review.

**Layout** (Ansys Mechanical reference, dark QSS theme, qtawesome icons): left **Outline** dock
(Input → Detected → Stations → Output tree), **Details** dock showing a formatted property grid
for whatever node is selected (never raw JSON), central **3D viewport** (`pyvistaqt.QtInteractor`
— input STL translucent over the rebuilt solid, station-plane rings, axis triad), bottom **Log**
dock (timestamped, level-coloured), status bar with a progress bar that is hidden while idle.
Engine calls run in `AnalyzeWorker`/`RebuildWorker` (`app/worker.py`) on a `QThread` — the main
window's controls stay responsive during a run, and Cancel is real (cooperative, polled between
stations via `engine.rebuild(..., cancel=...)`).

**G-ladder gate results (re-run at iteration 83, commit `4198be5`):**

| Gate | Command | Result |
|---|---|---|
| G1 | `.venv/bin/python -m pytest tests/api -q` | 7 passed |
| G1 | full M1–M13 scorer sweep | still green (see header note above — engine extraction, byte-identical CLI) |
| G2 | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/gui -q` | 7 passed |
| G2 | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m app --smoke out/gui` | exit 0, `smoke.json.ok: true`, 4 PNGs ≥ 20 KB |
| G3 | human/Fable visual review | **APPROVED** 2026-09-04 10:30 (three rounds of feedback, all addressed — see `PROGRESS.md` `## Notes from Brady`); `state/G3_APPROVED` present |

**Known GUI limitation:** a failed rebuild surfaces the raised exception's message and type in
the Log dock and a result-panel error state, but — matching engine limitation above — there is
no on-disk `status: "failed"` report for a failed run, so a crash mid-run leaves no artifact to
inspect afterward beyond the log text. Not exercised by any milestone or smoke run: a *very*
long-running rebuild's Cancel button under real user timing (only the cooperative-poll code path
is unit-tested, offscreen, with a synthetic instant cancel).
