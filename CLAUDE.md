# stl-rebuilder — project context

## Current status & next steps
- **2026-09-08 — Help/Manual + guided-demo tour system + no-install launcher, all 4 phases
  complete (`68162bd`→`641c5a0`→`11571f6`→`f03bdb7`).** Brady asked for an in-app manual, 13
  click-to-start guided demos (one per milestone) with speech-bubble popups anchored to the real
  controls (arrow, not text-in-a-panel), including M9 deliberately hitting its real known
  verification failure and teaching the actual lesson, plus a no-admin double-click desktop
  launcher. Planned first by a dedicated Fable 5.1 research pass
  (`docs/plans/help_and_demo_system.md`, ~600 lines, untracked/gitignored-adjacent — read it for
  the full spec before touching any of this) — Brady confirmed 4 decisions before build: demo
  names as proposed, a CUSTOM app icon (not the plan's original generic-icon default), soft
  input-lock on demo action steps, TELEMETRY cyan bubble color. Built via 4 sequential Opus
  agent passes, each screenshot-verified before the next started (this project's established
  discipline paid off again — every single phase's screenshot pass caught a real bug invisible
  from code alone, see below). 266 tests passing (was 166 before this feature, +100).
  - **Phase A — `app/help.py` + `app/help_content/*.md`**: Help → Quick Start Guide (F1) / Manual,
    11 pages covering every config, view toggle, and verification-row meaning, non-modal
    searchable dialog. Screenshot pass caught: a silently-dropped QSS stylesheet (unescaped `}}`
    in an f-string), zero paragraph spacing (`setMarkdown` bypasses the document default
    stylesheet — needed a manual `_space_out_blocks()`), and troubleshooting's Symptom/Fix lines
    collapsing into one paragraph (markdown eats single newlines).
  - **Phase B — `app/tour.py`**: the generic coachmark engine (`TourStep`/`CoachmarkBubble`/
    `TargetHalo`/`TourController`), two frameless top-level `Qt.Tool` windows (not a child overlay
    — the central viewport is native OpenGL, child widgets over it are unreliable), soft
    click-lock during action steps, an `expect` corrector for wrong settings. Added `MainWindow`
    signals `analyze_finished`/`rebuild_finished(bool)`/`run_failed`/`input_loaded` +
    `_verification_all_passed()` (deliberately returns **False**, not the plan's stated True, on a
    missing/empty verification block — right polarity for M9's branch). Screenshot pass caught
    the waiting-hint text colliding with the button row at 340px width.
  - **Phase C — `app/demos.py` + `app/demo_scripts.py`**: all 13 demos (names verbatim from the
    plan — Simple Tube Grain, Domed Capsule, Six-Point Star Bore, Finocyl, Domed Finocyl, Tapered
    Star, Central Bore + Six Satellites, Mid-Burn Slotted Grain, **Noisy Scan Input — Reading a
    "Failed" Check** (M9), Tiny/Tilted/Inches, Three-Segment BATES, Near-Burnout, Dirty Real-World
    Capstone), meshes generated on demand via the existing `harness.generators.make()` (NOT copied
    into the repo — `harness/truth/` is gitignored, up to 253MB for M13, so on-demand generation
    IS the portable form). M9's script runs at the real official settings
    (`z/mm/80/adaptive/5.0`), branches on the real verification outcome, and its failure-path text
    tracks `pipeline/engine.py::_axial_bounds_hint`'s actual current wording (~21mm short at the
    aft dome tip, no chord-tol/adaptive value moves it, Deviation p95 ≈2.3mm/10mm is the real
    signal). Screenshot pass caught a SERIOUS bug: the mesh worker's completion slots were lambdas,
    and Qt's connection-type inference from a receiver with no thread affinity ran them on the
    WORKER thread — the tour was silently building on the wrong thread and never fired. Also
    caught near-white demo-picker rows (`alternate-base` unthemed) and truncated blurb text
    (`heightForWidth` needed, same lesson as the bubble's own sizing). Bonus real bug found writing
    M10's script: **`chord_tol_spin` was a 2-decimal spinbox**, silently unable to enter M10's
    official 0.0125mm value — any inch-scale part needing sub-0.01mm tolerance was broken before
    this; now 4 decimals.
  - **Phase D — `scripts/create_desktop_shortcut.py`**: macOS primary = a generated `.app` bundle
    (Dock presence, no lingering Terminal window) + a `.command` fallback for debugging; Windows =
    `.bat` + a PowerShell-generated Desktop `.lnk` targeting `pythonw.exe` (OneDrive-safe Desktop
    resolution) — generated on every platform, executed only on win32, explicitly flagged as
    **designed but unverified** since this all ran on a Mac. `Help → Create Desktop Shortcut…` is
    live. **The custom app icon is hand-drawn with Pillow** (no image-gen tool, no network,
    same reasoning as the hand-drawn LED digits earlier this session): a motor-grain meridian
    profile (barrel + shallow elliptical domes, blue `#3f9fdc`) with the bore knocked out as true
    transparency and a dashed TELEMETRY-cyan axis line extending past both dome tips, on the
    viewport's own dark gradient — converted to `.icns` via macOS's built-in `iconutil`, never
    committed as a binary (regenerated every run). Screenshot pass caught the FIRST icon design
    reading as the digit "0" at 128px (semicircular dome ends looked like a zero) — fixed to
    genuinely elliptical domes.
  - **Not yet verified — needs Brady on a real screen** (flagged consistently across all 4
    phases' reports, not glossed over): the coachmark bubble/halo's actual stacking above the
    native OpenGL viewport and the halo's opacity pulse (nothing offscreen can prove this); one
    real end-to-end M9 demo run to watch the actual failure and branch fire; the icon at real
    size in Finder/Dock; the one-time Gatekeeper right-click→Open on the generated `.app`; the
    entire Windows launcher path (`.bat`/`.lnk`/pythonw) is untested by construction.
  - **19 commits ahead of origin — nothing pushed yet. Brady: `git push` when ready.**
- **2026-09-08 — Dome-shape robustness spot-check + T+/NOMINAL LED styling (`0c04ed6`).** Brady
  asked point-blank whether the reconstruction actually works across dome sizes, not just the M8
  screenshots already taken. Ran three real end-to-end cases live (not relying on history): M10
  (M8's dome at 1/40 scale, inches, off-origin, rotated axis), M12 (the one milestone with a
  genuinely CLOSED dome apex — its bore stops ~83mm short of the true tip, unlike every other
  dome milestone which opens straight through), and M2 (full-scale baseline) — all three passed
  volume/bounds/deviation cleanly, and the new dome-cap viewport layer rendered correctly and
  distinctly at both the tiny and full scale. **One thing NOT confirmed**: a clean close-up
  screenshot of M12's actual pole convergence — my own ad hoc verification camera-framing script
  wasn't precise enough to frame that ~83mm-tall region (a tooling limitation, not a product
  issue); Fable's own dome-cap report already flagged this same "closes to an apex" behavior as
  unit-tested on synthetic spheres but not screenshot-verified on real geometry, and that's still
  true. If it matters, load M12 live and rotate to the pole by hand.
  Separately, extended the LED-clock styling (from the per-dial percentage readouts) to the T+
  mission clock and NOMINAL status word, per Brady's direct follow-up request: `MissionClock` now
  paints a bezeled 6-digit HH:MM:SS LED plaque (new `_draw_led_clock` in `app/dashboard.py`,
  reusing the existing 7-segment digit renderer) with lit colons between the groups — kept here
  unlike the dial's "%" readout, since this number genuinely tells elapsed time. `StatusStrip`
  (NOMINAL/STANDBY/RUNNING/FAULT) got a matching dark bezel via QSS but stays plain letter-spaced
  text, since most letters have no honest 7-segment shape. 166 tests passing (+2).
- **2026-09-08 — Dome-cap end-band viewport layer added (Fable 5.1, `389a27f`).** Brady looked
  at the new station rings on M8 and asked why they stop abruptly ~190mm short of each dome tip,
  wondering if rings were being hidden or if he misunderstood "Stations." Neither — genuinely
  confirmed by reading the engine directly: `pipeline/engine.py:2360`'s `station_eps` deliberately
  insets station PLACEMENT from both ends (~0.02·L, capped), because right where a dome curves
  steeply the input STL's own facet tessellation biases a raw per-station circle-fit LOW and
  one-signed (measured on M8: -0.24 to -0.08mm, doesn't average out — see
  `_refine_dome_model_from_vertices`'s docstring, `engine.py:393`). So that end band is built by a
  DIFFERENT, more careful method entirely — a quadratic-in-R² curve refit directly against the
  mesh's own dense vertices there, extended analytically to the true tip — not by station-loft at
  all, and it's already numerically verified (Bounds Z, Deviation), just invisible in the 3D view.
  Fable's fix: a new mint "Dome-cap fit (not sectioned)" layer (own View-menu toggle + legend row,
  independent of Stations/Topology) that slices the ALREADY-BUILT solid (not a re-derived fit —
  deliberately chosen over exposing the engine's internal fit coefficients, to avoid any chance of
  silently drifting from what was actually built) at tip-clustered z's inside each end band,
  drawing faint rings + longitudinal meridian curves reaching the true axial extreme — visually
  distinct from real stations in three channels (color, no tick comb, longitudinal not
  circumferential) so it can't be mistaken for a real section. Closes at an on-axis apex only when
  slices genuinely converge (M8's domes open into the star bore, so correctly no apex drawn there).
  164 tests passing (+6). **Verified on M8 screenshots (both dome tips, edge-on, with/without
  toggle) — NOT yet screenshot-verified on a closed-apex milestone like M2** (the apex-closing
  branch is only unit-tested there, on synthetic sphere/cylinder geometry) — worth a look if this
  matters for M2 specifically. Also flagged as a possible tuning knob: the meridian lines are
  intentionally subtle (anti-wallpapering) — if it reads too faint live, `line_width`/opacity in
  `Viewport.show_dome_caps` is a one-line change.
- **2026-09-08 — Station/topology ring rendering fixed (Fable 5.1 design pass, `d22fc6a`).**
  Brady's M9 screenshot showed the yellow station rings nearly invisible looking down a
  transverse axis, and no rings at all visible when zoomed into a local fin feature — he asked
  whether he was misunderstanding what "Stations" means. He wasn't: a station really is a z where
  the mesh gets actually sectioned, and curvature between sparse stations legitimately comes from
  the fit/loft between real loops, not a verification gap. The actual bug (found before handing
  off to Fable): `show_station_planes`/`show_topology_events` in `app/viewport.py` drew EVERY
  ring at one shared GLOBAL max radius instead of that station's own local cross-section size —
  so rings near a small feature sat off-frame out at the full-body silhouette (explains "no rings
  near the fin"), and a flat disc with a 1%-wide band at 0.35 opacity collapses to a near-
  invisible sliver edge-on (explains "invisible down the X axis"). Fable's fix: a new
  `_station_cross_sections` (`main_window.py`) slices the solid once per station and feeds both
  the Stations table and the 3D rings, which now trace each station's REAL loop (not a circle —
  matters most for a bore's star/slot shape, previously invisible entirely), nudged off-surface
  by a radial homothety; edge-on visibility solved with a 4-azimuth tick comb per station that
  can't collapse to a line from any camera angle, so adaptive density reads as tooth density —
  the real z-distribution, nothing smoothed for effect. Verified against real M8 screenshots
  (edge-on, zoomed-to-dome, down-axis showing the star bore loops) that Fable read itself, not
  code-reading alone. 158 tests passing (+7). One known caveat carried forward, not caused by
  this change: M8 with `--adaptive --sections 60` still hits the documented 2026-09-06 adaptive
  fragility, so the dense-cluster comb pattern was verified via uniform placement + geometry
  tests, not an adaptive screenshot.
- **2026-09-08 — Round 5 of live-testing feedback, committed `f2a13eb`.** Four fixes from
  Brady's own hands-on GUI testing: (1) removed the viewport ground floor plane (added in Round
  4) — it read as a stray artifact and interfered with the transparency/ghost-overlay comparison
  mode; SSAO itself stays. (2) **M9's persistent Bounds Z verification failure investigated and
  root-caused, but NOT geometrically fixed** — Brady tried every chord-tol/adaptive combination
  (3.5 crashed, 4.25, auto-suggested 4.93) at M9's own official settings and the ~21mm aft-dome-
  tip shortfall never moved, because it's a genuine characteristic of M9's noisy/coarse input
  (median edge length 40mm) at the single most-extreme point, not a chord-tol/adaptive problem —
  confirmed by Deviation (a robust p95 stat) passing comfortably at 2.29mm/10mm the whole time.
  `pipeline/engine.py`'s new `_axial_bounds_hint` stops suggesting a fix that can't help and
  explains why instead. **The underlying shortfall itself is still open** — a real follow-up
  would loosen this specific check's tolerance for genuinely coarse/noisy inputs (e.g. using the
  mesh's own median edge length as a per-input noise scale), not something to paper over quickly.
  (3) Verification hint text in the Details dock was getting lost — widened the wrap column and
  added an unmissable "WHAT TO DO:" label ahead of the hint. (4) Redrew the percentage readout
  under each dashboard gauge dial as a hand-drawn 7-segment LED display (bezeled housing, no
  colon, existing state colors kept) per Brady's "old mission control" reference photo — hand-
  drawn because this repo's Bash guard blocks fetching a segment font. 151 tests passing.
- **2026-09-07 — Full-day GUI polish push (4 rounds), complete and self-corrected.** Brady asked
  for an honest design evaluation ("make it look fantastic... enterprise like grade... NASA
  rocket theme"), then live-tested it, then asked for research-grounded inspiration from real
  commercial CAD software, then had the result reviewed a second time. Each round was verified
  against real rendered screenshots, not code reading alone — and the review round genuinely
  caught real bugs, which matters for trusting this pattern going forward.
  - **Round 1 (Fable design review, commit `59aa943`)**: dark shell/verification checklist/dial
    concept already solid; fixed dial captions clipping off, station rings wallpapering the
    solid at real section counts; added the mission clock/status strip, section-view slider,
    deviation heatmap (first version), render-mode cycle, radius-profile chart, camera-
    orientation widget. 20 tests, 137 passed.
  - **Round 2 (Brady's own live testing, commit `f5ba847`)**: dials were `Policy.Fixed`
    vertically so they stayed small no matter how tall the dock was — now `Expanding` in both
    directions, a real fix Brady could see immediately; "100%" readout overlapping the dial's
    own rim and "0"/"100" end-tick labels clipping at the widget edge, both fixed with a
    provably-correct margin formula; two competing orientation widgets (plain VTK triad + Qt
    X/Y/Z buttons) consolidated into one once Brady confirmed live that the camera-orientation
    widget's own click/drag already snaps axis views; **solid color reverted from an
    experimental steel-grey back to the original blue** — Brady's explicit preference after
    seeing both.
  - **Round 3 (Fable research pass into SpaceClaim/SolidWorks/Fusion 360/Onshape/KeyShot +
    mission-control/avionics design, commit `7115106`)**: SSAO + SSAA + a gradient viewport
    background + ground floor plane (all verified working in the installed pyvista 0.48.4/VTK
    9.6.2 at sub-second cost, even on a 77k-cell real motor mesh); solid shading changed to
    `smooth_shading=True, split_sharp_edges=True` — re-verified directly on M8's star bore (the
    exact geometry that broke plain smooth shading back on 2026-09-04) that this fixes the
    pinwheel artifact rather than reintroducing it; IBM Plex Sans/Mono typography (already
    installed on this Mac) and a full Phosphor icon-set swap (both bundled dependencies, no new
    installs); "edges" render mode changed from raw triangulation wireframe to a real
    `extract_feature_edges` geometric-edge overlay; new in-viewport display-toggle overlay
    (Fusion-360/Onshape-style), wired bidirectionally with the View menu.
  - **Round 4 (Fable reviews Round 2+3's actual implementation, commit `e322fe3`)** — the
    payoff of having a second pass check the first: found the **deviation heatmap was computing
    the wrong metric** (nearest-VERTEX distance instead of true point-to-surface distance) and
    rendering a saturated, meaningless "everything over tolerance" field on a run that actually
    passed at p95 0.32mm — fixed with `compute_implicit_distance`, re-verified against the
    engine's own reported number. Also found the camera-orientation widget was being recreated
    (leaked) on every file open, and that the home button's position landed **inside** the
    widget's own footprint despite a code comment claiming otherwise — both fixed, the latter
    verified by direct geometry/coordinate checks since the widget itself still can't render in
    this offscreen sandbox. Plus several LOW polish items (transparent empty-state background,
    last non-Phosphor icon, dial vertical centering).
  - **Process note worth remembering**: Round 3's own code comments confidently asserted the
    orientation-widget fix was correct ("sitting below-left... rather than overlapping it") —
    that claim was wrong, caught only because Round 4 did the geometry math instead of trusting
    the prose. Self-review of visual/GUI work is worth the cost; a first pass's own confidence in
    its comments is not evidence it's correct.
  - Full suite 146 passed as of the last commit. 6 commits ahead of `origin/main` as of this
    entry (`59aa943`, `e0af0d4` fixing the axis-ring bug — see next entry below — `f5ba847`,
    `7115106`, `e322fe3`, plus this handoff commit). **Brady: `git push`.**
  - **Still genuinely unverified, needs Brady's own eyes on a real screen**: the camera-
    orientation widget's on-screen look and exact position (it's disabled under
    `QT_QPA_PLATFORM=offscreen` by design, so no screenshot in this whole 4-round process has
    ever actually shown it) — the fixes above are geometrically/programmatically verified, not
    pixel-verified. Also worth a real burnback STL run since everything above is still only
    proven on synthetic milestones.
- **2026-09-06 — Interactive GUI hardening from Brady's live testing, plan complete.** Ran the
  GUI by hand against real-scale synthetic STLs (M8, M13) and fixed everything that surfaced,
  closing out the plan at `~/.claude/plans/serialized-percolating-bachman.md` end to end:
  - **Dashboard**: added a 5th ANALYZE dial (LOAD/SCAN/SECTIONING/BUILD already existed) wired to
    a real progress signal threaded through `engine.analyze()`/`pipeline/io.py::load_and_orient`
    (previously silent — Analyze on a big mesh looked hung). Needle "creeps" forward
    asymptotically between sparse real checkpoints (an absolute 0.93 ceiling, 80s time constant,
    never-move-backward invariant), calibrated against ACTUAL M13 timing data (`_weld_by_radius`
    alone burns ~160 of ~182s with only one checkpoint) — a first design based on theoretical
    checkpoint spacing was proven wrong by replaying real timestamps and would have saturated
    early then sat flat, or jumped backward.
  - **Two real bugs found via live use, both fixed**: (1) clicking Analyze then Run before
    Analyze finished silently started a second, redundant `AnalyzeWorker` (Analyze/Run now
    disabled for the whole in-flight duration); (2) station rings drawn with a stale CACHED
    Analyze axis instead of the current rebuild's own recomputed axis (`--axis auto` re-detects
    fresh every rebuild, origin-refine is chord_tol-dependent) produced a huge spiral of rings on
    large-extent parts — now prefers `report["frame"]["axis"]` from the rebuild itself.
  - **Actionable, context-aware error hints** (Brady's explicit ask — "nail down all of these
    types of errors...they should have some actionable advice"): crash/volume hints no longer
    say "try --adaptive" when it's already on (say to turn it OFF instead — confirmed by direct
    reproduction that `--adaptive` is measurably LESS robust than uniform placement on M8's
    slot/fillet transitions, 4/4 uniform pass vs 1/4 adaptive pass across sections
    40/52/60/100); the non-axisymmetric-loop crash hint now computes a concrete suggested
    `--chord-tol` value instead of a vague nudge, and distinguishes real off-axis centers from
    ordinary chordal roundness noise.
    Also fixed the M13-without-`--adaptive` crash's ROOT CAUSE in `pipeline/solids.py`'s fillet
    clamp (a fillet exactly filling half the wedge's span made two meridian points coincide,
    crashing `BRepBuilderAPI_MakeEdge` on a zero-length edge — now clamped a hair short via a
    `1e-6` margin), restored the fallback ladder's exception guard, and left a backstop
    `GeometryError` hint for the case nothing converges.
  - **Watertight confirmation surfaced end-to-end**: `_compute_verification` now reports it; the
    Analyze log line is a descriptive pass/fail checklist (watertight, single body, dropped
    islands) instead of a raw data dump; the Output page's Verification group leads with a
    Watertight row.
  - **Viewport** (done earlier in this same session, verified still working): clickable legend
    rows (`LegendRow` in `app/viewport.py`, synced to the View-menu checkboxes via
    `layer_toggled` signal) replacing the old inert `QLabel` legend, plus independent "Rebuilt
    solid: transparent" / "Input mesh: solid color" View-menu toggles for A/B'ing fin geometry
    against the input mesh.
  - 95 tests pass (`tests/api`, `tests/gui`, `tests/test_stations.py`, `tests/test_solids.py`).
    Committed at `ffd23ef`. **Brady: `git push`.**
  - **Not yet done / next**: none of this has been run against a REAL burnback STL yet — still
    the big open item from Round 2/3 (see below). The M13-without-adaptive fillet-clamp fix and
    the new hints are unit/regression-tested but worth re-confirming by hand on the actual M13
    milestone file (`harness/truth/M13.stl`) at `--sections 50` with no `--adaptive` if that
    specific repro matters again.
- **2026-09-04 10:52 — ROUND 3 COMPLETE. ALL THREE ROUNDS DONE.** G1 (engine API) and G2
  (PySide6 app) each passed on their first iteration; G3 took three Fable visual-review rounds
  (feedback via PROGRESS Notes → `state/G3_APPROVED`); HANDOFF v3 written and verified against
  the code. Round 3: 7 iterations, ≈$29, 3 hours. Project totals: 84 iterations, ≈$469,
  Aug 29 → Sep 4. The deliverables: `rebuild.py` CLI (13/13 milestones), `pipeline/engine.py`
  API (`analyze`/`rebuild` w/ progress+cancel), `python -m app` GUI (dark theme, 3D viewport,
  station table; screenshots in `out/gui/`), `WORK_SETUP.md` (work-machine setup, written for
  an AI assistant), `HANDOFF.md` v3 (484 lines). **Remaining items are all human:** (1) `git
  push` done through Sep 4 by Claude-via-script when Brady asks; (2) SpaceClaim checklist
  (HANDOFF §4); (3) THE big one — a real burnback STL through the §5 runbook / MR slot:
  everything so far is proven only on synthetic meshes; (4) try `python -m app` on the Mac and
  take the repo to work per WORK_SETUP.md.
- **2026-09-04 07:55 — ROUND 3 (GUI) STARTED.** Ladder G1→G2→G3→HANDOFFv3 (MISSION §6.3, §12
  rewritten): G1 `pipeline/engine.py` API extraction, G2 PySide6 app (`python -m app`, pyvistaqt
  3D viewport, offscreen smoke + screenshots), G3 polish with **Fable visual review** — the
  driver stops at "awaiting visual review"; reviewer inspects `out/gui/*.png`, writes feedback
  into PROGRESS.md `## Notes from Brady` or creates `state/G3_APPROVED`. **No packaging/.exe —
  run-from-source on mac + Windows is the deliverable** (Brady can't run installers at work);
  `WORK_SETUP.md` is the work-machine setup doc (written for the AI assistant there) and must
  stay accurate. GUI deps installed in `.venv` (PySide6 6.11.2, pyvista 0.48.4, vtk 9.6.2,
  pyqtgraph, qtawesome). Driver gates live in `loop.py::evaluate_gui`; G fragment
  `driver/prompt_gui.md`. Lesson re-learned at launch: **re-tag `infra-frozen` BEFORE starting
  the loop** — a stale tag makes preflight revert the new setup as "agent tampering" (cost two
  false starts, fixed at `2ae0227`).
- **2026-08-31 10:27 — ROUND 2 COMPLETE.** All 13 milestones + MR(skipped) + HANDOFF green;
  final regression sweep passed on the last commit; driver exited DONE at iteration 77.
  Round 2 ran 2026-08-30 04:29 → 08-31 10:27 (~30 h wall incl. Brady's token-limit pause),
  52 iterations, ≈$330 (project total $435). The engine now does: per-window ruled loft (M6),
  multi-loop matching with chain birth/death (M7), real feature-aware `--adaptive` stations
  (M8 — 10 iterations, the hardest fight), marching-cubes input repair with skew p95≈0.96
  (M9), `--axis auto`/inches/off-origin/1/40-scale frames (M10), multi-body STEP (M11),
  near-burnout dome breakthrough (M12), and the 5.3 M-triangle dirty capstone in 280 s (M13).
  `HANDOFF.md` (394 lines) has the full results table, SpaceClaim checklist, real-STL runbook,
  and three recorded engine gaps for Round 3 (e.g. `paths_used` never reports `loft`).
  Winning STEPs: `logs/M{1..13}-final-step.step`. The Opus harness review (iter 39) fixed a
  real gaming hole (station_bands endorsed cosine end-clustering) and left 9 documented
  non-blocking scorer weaknesses in PROGRESS.md `## Open review findings`.
  **Brady: `git push` (~85 commits pending; the driver never pushes).**
- **2026-08-29 20:41 — Round 1 done.** 25 iterations, ≈$103 API-equivalent, one day. All
  five milestones pass on commit `1b4ff91` (verified together by the driver's regression sweep):
  M1 annular cylinder, M2 ellipsoidal domes, M3 6-point star bore, M4 finocyl with a topology
  event at z=6000, M5 domes + fins with two events. Volume errors ≤ 0.02 %, deviation max
  ≤ 0.44 mm (gate 0.6), gmsh min SICN ≥ 0.187 (gate 0.1), 1–2 s per rebuild. `HANDOFF.md`
  has the results table (§2), the SpaceClaim checklist (§4), how to run a real burnback STL
  incl. choosing `--chord-tol` (§5), and honest limitations (§6). Winning STEPs:
  `logs/M{1..5}-final-step.step`.
- **Next (Brady):** (1) `git push`. (2) SpaceClaim checklist on the 13 STEPs (HANDOFF §4) —
  the Parasolid import is the one test the OCCT harness cannot see. (3) When work authorises
  it, drop a real burnback STL (+ optional `<name>.json` with units/axis/known volume) into
  `real_inputs/` and run MR per HANDOFF §5 — everything so far is proven only on synthesised
  meshes. (4) Decide Round 3 (PySide6 GUI, MISSION §12): extend MISSION with G1–G3, driver-level
  gates, re-tag `infra-frozen`, `./loop.sh`; also decide the Python 3.12 migration for wheels.
- **Round 1 gaps now closed in Round 2:** loft path, multi-loop stations, non-axisymmetric
  handling scope unchanged (outer must still be axisymmetric — a real-STL risk), `--adaptive`
  is real (M8/M10/M12/M13 gates force it), scale/units/axis handled.
- **Known CLI quirk:** the `claude` CLI froze mid-iteration 3× (silent, 0 sockets, after a
  completed step) — the driver's timeout catches it; killing just the `claude -p` pid banks the
  committed work early. Not a driver bug.
- Driver hardening done today (all on `infra-frozen`): save-then-stop (`--stop`/`--kill`/Ctrl-C),
  stream-json heartbeats + `--status` + `state/STATUS.md`, memory-guarded scoring (24 GB),
  per-mode timeouts 90/120/180 min, budgets $10/$15/$20, crash guard + auto-restart in
  `loop.sh`, review-pass stall reset, `PROGRESS_MIN_DELTA`, `--keep` artifacts, and the
  regression gate (a milestone pass requires all earlier milestones to still pass; otherwise
  demote — it caught M5's fix breaking M4). Window budget default $120; use
  `LOOP_WINDOW_BUDGET_USD` to raise it for a session. Costs: Sonnet iterations $2–9 (10–50
  min), Opus review/escalated $2–4.
- Incidents worth remembering: the M0 review pass's deviation metric OOM'd the machine (64 GB
  RAM + 64 GB swap) — fixed by the agent with a radius-bounded KD-tree query (75 s selftest);
  a `ROOT / None` crash on the first milestone pass silently stopped the loop for 4 h before the
  crash guard existed.

## What this is
An autonomous, self-correcting coding loop that builds `rebuild.py`: an STL (solid-rocket-motor
burnback surface mesh) → true BRep solid (STEP) converter, validated against synthetic ground
truth by a frozen scoring harness. Read `MISSION.md` for the full spec and `README.md` for how
the loop works and how to run it.

## If you are the looping agent
The driver puts everything you need in the prompt header, `PROMPT.md`, and `MISSION.md`.
Rules of engagement are MISSION.md §2 — frozen paths, one change per iteration, commit, and
update `PROGRESS.md`. Use `.venv/bin/python`. No network, no `git push`, nothing outside this repo.

## If you are Brady's assistant in an interactive session
Do not edit `harness/` after it is frozen (tag `harness-frozen`) without also re-tagging —
the driver restores it from the tag before every scoring run. Infra files are restored from
`infra-frozen`; to change `loop.py`/`PROMPT.md`/`MISSION.md`, edit, commit, then re-point
the `infra-frozen` tag (`-f`). The loop's state is `state/loop_state.json` (gitignored);
`state/current.json` says what the driver is doing right now; `state/STATUS.md` is the dashboard.
Stop a running loop with `python3 loop.py --stop` / `--kill`, never by killing processes by hand.
The project's Bash guard hook (`driver/guard_bash.py`) also applies to you here: any Bash command
whose text mentions a `~/.claude`/`~/.ssh`/`~/.config` path, or `git tag`/`git remote`, is
blocked — use the Read/Write tools for those files and a script file for the re-tag.

## Key paths
- `MISSION.md` — spec, milestones, gates, harness contract, failure modes
- `loop.py` — driver (config block at top; env overrides `LOOP_<NAME>`)
- `driver/prompt_*.md` — mode fragments; `driver/guard_bash.py` — PreToolUse hook
- `docs/research/*.md` — verified API research (trimesh, OCP/build123d, gmsh, claude CLI)
- `harness/` — scorer + generators (agent-built at M0, then frozen)
- `pipeline/` — the product (agent-owned)

## Access notes
No secrets involved. Runs on Brady's Claude subscription via the `claude` CLI (OAuth login);
the driver strips `ANTHROPIC_API_KEY` from the agent env unless `LOOP_USE_API_KEY=1`.
