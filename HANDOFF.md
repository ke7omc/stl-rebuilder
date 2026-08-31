# HANDOFF v2 — STL → STEP rebuilder (M1–M13 PASS, MR skipped)

Written at iteration 77 on commit `039291b`. Every number in §2 is read out of a scorer JSON on
disk — `out/score.M1.json` … `out/score.M12.json` (the driver's iteration-76 regression sweep,
2026-08-31 10:05–10:10) and `out/score.local.json` for M13 (2026-08-31 09:58). All fourteen
report `pass: true, progress: 1.0`. Nothing below is estimated; where a number could not be
measured it says so.

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
| MR | real burnback STL | — | — | — | — | — | — | — | **skipped — no real input** |

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
| all | `step_roundtrip` | ≤ 7e-15 mm | 1e-6 |

Reference volumes, if you want to compare against what SpaceClaim reports (mm³, analytic truth):
M1 28 588 493 148 · M2 27 547 756 121 · M3 28 077 249 565 · M4 27 584 186 713 ·
M5 26 668 987 991 · M6 26 129 965 389 · M7 27 269 024 233 · M8/M9 20 595 735 261 ·
M10 321 808.36 · M11 24 669 356 312 · M12/M13 15 370 275 928.

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
  conclude anything is misplaced** (see §4.2 item 9).
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
2. **The outer wall must be axisymmetric.** `paths_used.outer` is `revolve` in all thirteen
   milestones; a station whose outer loop is not an axis-centred circle is a hard exit-4 reject
   (`cli.py:1386`). Real cases (external insulation steps, non-round cases) are not supported.
3. **M13's margins are thin.** `axial_extent_err_mm` 7.715 / 8, `surface_deviation_p99` in the
   breakthrough band 3.923 / 4, gmsh min SICN 0.143 / 0.1. The extent one has a known clean fix
   if it bites: measure axial extent from the coarse section-area sweep (MISSION §5.5.2) instead
   of `mesh.bounds`, which the input's σ = 0.8 mm noise inflates.
4. **The report's `paths_used` cannot distinguish a prism from a loft** (§2 footnote), and has no
   `bodies` or `status` field (§5.3).
5. **`--refine-bands` is a no-op** in every passing configuration. (`--adaptive` is *not* a no-op
   — contrary to the Round 1 handoff — it places 14 stations in a 295 mm feature band and 22 per
   dome inside M13's 120-station budget.)
6. **Runtime scales badly with triangle count.** 3.9 M triangles took 279.6 s against a 900 s
   gate; the gates were sized for these meshes, and a 20 M-triangle scan is not characterised.
7. **`--axis auto` sign is a convention, not a measurement.** It canonicalises to
   "largest-magnitude component positive". That is *a* rule, and it may disagree with your part's
   fore→aft sense; the scorer does not implement MISSION §7.2's dot<0 flip, so the pipeline owns
   the convention. See §4.2 item 9.
8. **Station count is capped by what was tested.** No milestone exceeds 120 sections.

### What to try next (engine)

- Decouple the report from the builders: emit `loft` vs `prism` honestly, add `bodies`, and add a
  `status` field so a failed run still produces a report.
- Replace the `mesh.bounds` axial extent with the section-area sweep (limitation 3).
- Actually implement `--refine-bands`, or delete the flag.
- Characterise the non-axisymmetric-outer case; today it is a reject, not a fallback.
- Get a real STL into `real_inputs/` and run MR. That is the highest-value single action left.

---

## 7. What Round 3 (the PySide6 GUI) needs from the engine

**There is no `pipeline.engine` module today, and no `--progress-json` flag.** MISSION §9 asks
this section to list them; what follows is the honest gap analysis rather than a description of
something that exists.

**Current programmatic surface.** `rebuild.py` is a four-line shim over
`pipeline.cli.main(argv) -> int`. All orchestration lives in `pipeline/cli.py::_run(args)` — a
~840-line function reached only through an `argparse.Namespace`, printing to stdout/stderr and
returning an exit code. The supporting modules are importable and reasonably clean
(`io`, `slicing`, `stations`, `fitting`, `solids`, `booleans`, `export`, `report`, `tol`), but
the sequencing that turns a mesh into a solid is not callable except as a process.

**What the GUI needs, in dependency order:**

1. **`pipeline/engine.py` — a callable API.** Extract from `_run`:
   `run(input_stl, *, axis, units, sections, adaptive, refine_bands, chord_tol, output, stl,
   progress=None) -> Result`, where `Result` carries the report dict, the output paths, the exit
   code and the collected diagnostics. The CLI then becomes a thin adapter over it. Without this
   the GUI is stuck shelling out and scraping stdout.
2. **`--progress-json` (and the `progress=` callback behind it).** Emit one JSON object per line
   to a chosen stream: `{"stage": ..., "frac": 0.0–1.0, "msg": ...}` at each of the pipeline's
   real stages — load/repair, frame resolution, station placement, slicing, loop
   classification, event bisection, solid build, booleans, export. M13 takes 279.6 s; a GUI with
   no progress signal over that window is unusable. The CLI flag and the in-process callback
   should be the same mechanism.
3. **Structured errors instead of exit codes.** Today §5.4's taxonomy is stderr text plus an
   `int`. The GUI needs the exit code *and* the failing z, the loop counts found vs expected, and
   the stage name, as data — i.e. always write a report, with `status: "failed"` and a
   `failure: {code, stage, z_mm, detail}` block (limitation 4).
4. **Cancellation.** A cooperative cancel token checked at station boundaries and before each
   boolean. There is no way to stop a run today short of killing the process.
5. **Intermediate geometry for preview.** The GUI will want to show stations and section polygons
   before the (slow) solid build. `slicing`/`stations` already produce these; the engine API
   should expose them as a first-class intermediate result rather than discarding them inside
   `_run`.
6. **In-process determinism.** `_run` currently assumes a fresh process (module-level caches,
   OCP global state, `REBUILD_DEBUG_*` env flags). Before the GUI calls it repeatedly in one
   process, verify two consecutive `engine.run()` calls on the same input produce byte-identical
   STEPs.
