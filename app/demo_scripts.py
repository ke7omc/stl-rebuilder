"""The words. Thirteen `TourStep` lists, one per guided demo.

**Adding a fourteenth demo is two edits and no new machinery**: copy the nearest
`DEMO_M*_STEPS` list below, rewrite its text, and add one row to `DEMOS` in `app/demos.py`.
Nothing here is generated, imported dynamically, or looked up by name -- if you can read a list
of sentences you can edit a demo.

Two conventions worth knowing before you write one:

* `_config_steps(...)` emits the shared settings walkthrough (axis, units, sections, chord
  tolerance, adaptive, then a review step). Its `why_*` arguments are the per-milestone
  sentences explaining why THIS grain wants THOSE numbers -- that is where a demo earns its
  keep, so write them as if to someone who has never seen the part.
* The review step is the one carrying `expect`, and it has to stay that way: a step that waits
  on a run's completion signal cannot validate the settings it ran with, because by then it has
  already run with them (`app/tour.py`, `TourController.__init__`).

Every number quoted in the prose below comes from `HANDOFF.md` §3.2 (the official commands),
`MISSION.md` §6.2 (what each grain actually is), or -- for M9's failure -- the engine's own hint
in `pipeline/engine.py::_axial_bounds_hint`. Do not invent new ones."""
from app.tour import TourStep

# ---------------------------------------------------------------------------
# Shared step shapes. Each returns ONE TourStep, so a demo below reads as a flat list of
# sentences rather than as a call to a factory that hides its own structure.
# ---------------------------------------------------------------------------


def _welcome(title: str, body: str) -> TourStep:
    """The opening step: what this burnback geometry IS, before any control is touched. No
    target, so the bubble sits centered over the viewport with nothing to point at yet."""
    return TourStep(target=None, title=title, body=body)


def _input_step(body: str) -> TourStep:
    return TourStep(
        target="input_path_edit", title="The input mesh is already loaded",
        body=body + "\n\nThis file was generated on this machine by the test-geometry "
                    "generators in harness/. The repository ships the generators, never the "
                    "meshes — which is why the first run of a demo can take a moment.")


def _config_steps(*, axis: str, units: str, sections: int, chord_tol: float, adaptive: bool,
                  why_frame: str, why_sections: str, why_chord: str,
                  why_adaptive: str) -> list:
    """The settings walkthrough every demo shares: one step per option widget, each naming the
    exact value AND why this particular grain wants it, then a review step that refuses to
    continue until the settings really are those (`expect`)."""
    on_off = "on" if adaptive else "off"
    chord_text = f"{chord_tol:g}"
    return [
        TourStep(
            target="axis_combo", title="Axis",
            body=f'Set Axis to "{axis}".\n\n{why_frame}'),
        TourStep(
            target="units_combo", title="Units",
            body=f'Set Units to "{units}".\n\nUnits describe the INPUT file. An STL carries no '
                 f'units of its own — it is just numbers — so this is the one setting the tool '
                 f'genuinely cannot guess. Everything downstream (tolerances, the report, the '
                 f'STEP) is in millimetres regardless.'),
        TourStep(
            target="sections_spin", title="Sections",
            body=f"Set Sections to {sections}.\n\nThis is how many cross-sections are cut along "
                 f"the axis and reconstructed as profile curves. More sections means more "
                 f"fidelity along the length and a slower run.\n\n{why_sections}"),
        TourStep(
            target="chord_tol_auto", title="Chord tolerance",
            body=f'Uncheck "auto from mesh", then type {chord_text} into the Chord tol box '
                 f'beside it.\n\nChord tolerance is how far a fitted curve is allowed to sit '
                 f'from the mesh it was fitted to. The auto value is a reasonable guess from '
                 f'the mesh\'s own chordal sag; {chord_text} mm is the value this milestone was '
                 f'actually validated at.\n\n{why_chord}'),
        TourStep(
            target="adaptive_check", title="Adaptive stations",
            body=f'Turn adaptive stations {on_off.upper()}.\n\nAdaptive placement clusters '
                 f'stations where the geometry changes — domes, fillets, slot ends — instead of '
                 f'spreading them evenly. It is not automatically better: uniform spacing is '
                 f'often MORE robust across sharp transitions.\n\n{why_adaptive}'),
        TourStep(
            target=None, title="Check the settings",
            body=f"Before running, the settings should read:\n\n"
                 f"    Axis {axis} · Units {units} · Sections {sections}\n"
                 f"    Chord tol {chord_text} mm (auto off) · Adaptive {on_off}\n\n"
                 f"Click Next. If anything is off, this step will say which and wait — it is "
                 f"the last point at which a wrong setting is cheap to fix.",
            expect={"axis_combo": axis, "units_combo": units, "sections_spin": sections,
                    "chord_tol_auto": False, "chord_tol_spin": chord_tol,
                    "adaptive_check": adaptive}),
    ]


def _analyze_step(body: str) -> TourStep:
    return TourStep(
        target="analyze_btn", title="Analyze", advance="analyze_done", constrain=True,
        body="Click Analyze.\n\nAnalyze reads the mesh and reports what it found — axis, extent, "
             "triangle count, watertightness, a suggested chord tolerance — without building "
             "anything. It is the cheap look before the expensive run.\n\n" + body)


def _detected_step(body: str) -> TourStep:
    return TourStep(
        target="page_detected", title="What Analyze found",
        on_enter="select_outline_detected", body=body)


def _run_step(settings_line: str, body: str = "") -> TourStep:
    tail = f"\n\n{body}" if body else ""
    return TourStep(
        target="run_btn", title="Run", advance="rebuild_done", constrain=True,
        body=f"Click Run.\n\nThis rebuilds the solid and writes a real STEP file to the Output "
             f"STEP path, at {settings_line}. Cancel stays live the whole time.{tail}")


def _verify_step(body: str) -> TourStep:
    return TourStep(
        target="manifest_tree", title="Read the verification",
        on_enter="select_outline_output",
        body="The Output page compares the solid that was just built against the mesh it came "
             "from.\n\n" + body)


def _wrap_step(name: str, body: str) -> TourStep:
    return TourStep(
        target=None, title=f"{name} — done",
        body=body + "\n\nThat is the whole demo. Try the next one from Help ▸ Guided Demos, or "
                    "press End tour and run your own mesh with what you just learned.")


# ---------------------------------------------------------------------------
# M1 — Simple Tube Grain
# ---------------------------------------------------------------------------
DEMO_M1_STEPS = [
    _welcome(
        "M1 — Simple Tube Grain",
        "This is the simplest solid the tool handles: an annular tube 10 metres long, 1000 mm "
        "outer radius, 300 mm inner, with flat ends. No domes, no fins, no star — one cylinder "
        "with one hole down the middle.\n\n"
        "It is here so you can see the whole workflow once with nothing to distract you. Every "
        "later demo is this same loop with harder geometry in it."),
    _input_step("harness/truth/M1.stl is the burnback surface of that tube: a triangle mesh of "
                "the OUTSIDE of the propellant, including the bore wall."),
    *_config_steps(
        axis="z", units="mm", sections=40, chord_tol=0.5, adaptive=False,
        why_frame="M1 was built lying along Z, so you can name the axis outright instead of "
                  'letting the tool detect it. "auto" would find the same answer here; naming '
                  "it is simply the cheaper, more certain path when you already know.",
        why_sections="40 is plenty for a shape whose cross-section never changes. Every section "
                     "of M1 is the same annulus, so extra sections would cost time and buy "
                     "nothing — this run should take about two seconds.",
        why_chord="0.5 mm is the tolerance the whole synthetic milestone family was tessellated "
                  "and scored at. On a 1000 mm radius that is a five-thousandth of the part.",
        why_adaptive="Off. Adaptive placement clusters stations at features, and M1 has no "
                     "features to cluster at — a straight tube is uniform by construction."),
    _analyze_step("Watch the ANALYZE dial on the dashboard move while it works."),
    _detected_step(
        "Read three rows here.\n\n"
        "Axis: Z, with a confidence percentage — that is how strongly the mesh's own inertia "
        "says it is a body of revolution about that direction.\n\n"
        "Watertight: yes. A mesh with holes cannot enclose a volume, and the engine refuses to "
        "rebuild one.\n\n"
        "Suggested chord tol: the tool's own estimate from the mesh's chordal sag. Compare it to "
        "the 0.5 you typed."),
    _run_step("axis Z, 40 sections, 0.5 mm"),
    _verify_step(
        "Every row should be a green check. Volume compares the solid's enclosed volume against "
        "the input mesh's — on M1 this lands within a hundredth of a percent, because a "
        "revolved tube is exactly representable as BRep geometry rather than approximated.\n\n"
        "Deviation is the one to learn: the p95 distance from the input mesh's points to the "
        "solid's surface, against a tolerance derived from the chord tolerance."),
    TourStep(
        target="viewport", title="The three render modes",
        body="Press Ctrl+1, Ctrl+2, Ctrl+3 to cycle the viewport between solid, edges and "
             "points (W cycles them too).\n\n"
             "Solid is the shaded surface. Edges overlays the solid's real geometric feature "
             "edges — not the triangulation — so on M1 you should see exactly the circles where "
             "the ends meet the walls, and nothing else. That sparseness IS the result: a "
             "handful of analytic faces, not a mesh."),
    _wrap_step(
        "M1",
        "You have run the tool end to end: load, analyze, set fidelity, rebuild, verify, look. "
        "M2 adds domes to this same tube."),
]

# ---------------------------------------------------------------------------
# M2 — Domed Capsule, Straight Bore
# ---------------------------------------------------------------------------
DEMO_M2_STEPS = [
    _welcome(
        "M2 — Domed Capsule, Straight Bore",
        "M1's tube with 2:1 ellipsoidal domes closing both ends, and a straight bore running "
        "all the way through.\n\n"
        "The domes are the point. A dome is where the surface curves fastest and where a "
        "station-by-station reconstruction is most likely to cut corners, so this is the "
        "milestone that pins down how the tool handles ends — including the band right at each "
        "apex, where it deliberately places no stations at all."),
    _input_step("harness/truth/M2.stl is the domed capsule's burnback surface — about 7 MB of "
                "triangles, most of them spent on the two domes."),
    *_config_steps(
        axis="z", units="mm", sections=40, chord_tol=0.5, adaptive=False,
        why_frame="Built along Z, like the rest of the synthetic family.",
        why_sections="40, with the dome gate demanding at least 8 of them inside each dome. "
                     "Notice that this is achieved WITHOUT adaptive placement — the engine's "
                     "uniform placement already respects dome extent.",
        why_chord="0.5 mm again. On the domes this matters more than it did on M1: chord "
                  "tolerance is the allowed gap between a fitted curve and the mesh, and a "
                  "curved surface is where that gap wants to open up.",
        why_adaptive="Off. M2 passes its dome gates on uniform stations, which is worth seeing "
                     "before M5 shows you a case that does not."),
    _analyze_step("This mesh is bigger than M1's, so the dial has more to do."),
    _detected_step(
        "Axial extent is the number to look at here: the full length from dome tip to dome tip, "
        "in millimetres. Bounds Z on the Output page will later check the rebuilt solid against "
        "exactly this figure, and the extremes it compares are the two apexes."),
    _run_step("axis Z, 40 sections, 0.5 mm"),
    _verify_step(
        "Watertight, Volume, Bodies, Bounds and Deviation should all be checks.\n\n"
        "Volume is the interesting one on a domed part: it is a whole-body number, so a dome "
        "fitted slightly short shows up here as well as in Bounds Z. Both passing means the "
        "ends really are where the input says they are."),
    TourStep(
        target="page_stations.table", title="The station table",
        on_enter="select_outline_stations",
        body="Every row is one cross-section the engine actually cut, with its axial position "
             "and the radii of the loops found there. Click a row: the matching ring highlights "
             "in the viewport and the radius-profile chart marks the same z.\n\n"
             "Scroll to the top and bottom. The stations stop a little short of each tip — that "
             "band is rebuilt from a dedicated dome fit instead, because a circle fitted to a "
             "near-apex slice is systematically too small. The viewport draws it as the "
             "dome-cap layer, in a different style from the station rings."),
    TourStep(
        target="viewport", title="Look at the dome caps",
        body="In the View menu, toggle the dome-cap layer off and on while watching an end of "
             "the part.\n\n"
             "Those extra end-band slices are cut from the solid that was actually built, not "
             "from the fit that produced it — so what you are seeing is evidence, not a "
             "restatement of intent."),
    _wrap_step(
        "M2",
        "Domes, the station table, and the dome-cap band that fills the gap the stations "
        "deliberately leave. M3 changes the bore from a circle to a star."),
]

# ---------------------------------------------------------------------------
# M3 — Six-Point Star Bore
# ---------------------------------------------------------------------------
DEMO_M3_STEPS = [
    _welcome(
        "M3 — Six-Point Star Bore",
        "A plain outer cylinder, but the bore is a six-point star: valleys at 250 mm radius, "
        "tips at 450 mm, with 40 mm fillets in the valleys and 30 mm at the tips, extruded the "
        "full length.\n\n"
        "This is the first non-circular loop. Up to now every cross-section was a pair of "
        "circles the engine could fit analytically; now the inner loop is a filleted polygon "
        "that has to be reconstructed as a real curve, and the fillets are where the worst "
        "deviation is expected to live."),
    _input_step("harness/truth/M3.stl — the star is uniform along the whole length, so every "
                "cross-section is identical."),
    *_config_steps(
        axis="z", units="mm", sections=60, chord_tol=0.5, adaptive=False,
        why_frame="Along Z again.",
        why_sections="60. More than M1/M2 not because the shape changes along the axis — it "
                     "does not — but because the star's twelve fillets make each individual "
                     "section harder, and the milestone's face-count gate (under 100 faces) "
                     "only holds if the sections agree well enough to be lofted into shared "
                     "faces.",
        why_chord="0.5 mm. Expect the largest deviation at the fillets: a 30 mm fillet is the "
                  "tightest curvature anywhere in the part, so it is where a chordal fit has "
                  "the least room.",
        why_adaptive="Off. There is nothing along the axis to adapt to — the star is extruded, "
                     "so uniform spacing is exactly right."),
    _analyze_step(""),
    _detected_step(
        "Nothing here announces the star — Analyze reports the frame and the mesh's health, not "
        "the bore's shape. The star shows up in the Stations table (multiple radii per station) "
        "and, best of all, in the viewport once the solid exists."),
    _run_step("axis Z, 60 sections, 0.5 mm"),
    _verify_step(
        "Volume within a tenth of a percent, and Deviation's worst point should be in the "
        "fillets. That is the expected place for it — a check passing for the right reason is "
        "worth more than a check passing."),
    TourStep(
        target="viewport", title="Cut it open with Section view",
        advance="section_toggled",
        body="Press S (or View ▸ Section view) to slice the solid with a plane and see inside "
             "it.\n\n"
             "The star bore is invisible from outside — the outer surface is a plain cylinder. "
             "Section view is the only way to look at it, and on this part the slice looks the "
             "same wherever you put it, which is itself the confirmation that the extrusion was "
             "reconstructed as an extrusion."),
    TourStep(
        target="viewport", title="Sweep the section slider",
        body="Drag the section slider along the axis.\n\n"
             "Six points, twelve fillets, unchanged from end to end. Keep this picture: in M6 "
             "the same star grows as you sweep, and that difference is the whole of M6."),
    _wrap_step(
        "M3",
        "A non-circular bore, and the view tool that makes internal geometry visible at all. "
        "M4 introduces a bore that CHANGES along the axis."),
]

# ---------------------------------------------------------------------------
# M4 — Finocyl, bore-to-fin transition
# ---------------------------------------------------------------------------
DEMO_M4_STEPS = [
    _welcome(
        "M4 — Finocyl: the Bore-to-Fin Transition",
        "A circular 300 mm bore runs the fore half. Aft of z = 6000 mm, eight rectangular fin "
        "slots open off it — 80 mm wide, reaching from 300 mm out to 700 mm radius, with 40 mm "
        "tip radii — and the wall where they begin is flat.\n\n"
        "That flat wall is a TOPOLOGY EVENT: at one exact axial position the cross-section stops "
        "being one circle and becomes a circle with eight fins attached. The engine has to "
        "detect it, place stations tightly either side of it, and still produce exactly one "
        "solid."),
    _input_step("harness/truth/M4.stl — the fins are cut into the aft half only."),
    *_config_steps(
        axis="z", units="mm", sections=80, chord_tol=0.5, adaptive=False,
        why_frame="Along Z.",
        why_sections="80 — double M1's. The transition at z = 6000 is a discontinuity, and the "
                     "only way a station-based reconstruction resolves a discontinuity is by "
                     "having stations close enough either side of it to bracket it cleanly.",
        why_chord="0.5 mm. The fin tip radii are 40 mm, comparable to M3's fillets, so the same "
                  "tolerance holds.",
        why_adaptive="Off — and this is worth noticing. M4 hits its topology-event gate on "
                     "UNIFORM stations at 80 sections. Uniform placement across a sharp "
                     "transition is frequently more robust than adaptive; adaptive is a tool "
                     "for curvature, not for discontinuities."),
    _analyze_step(""),
    _detected_step(
        "Analyze does not find the event — it looks at the mesh, not at sections. The topology "
        "event is a product of the rebuild, and it will appear in the Stations table and as its "
        "own ring colour in the viewport once Run has finished."),
    _run_step("axis Z, 80 sections, 0.5 mm"),
    _verify_step(
        "Bodies: 1. That is the row to check on M4 — eight fins branching off a bore is exactly "
        "the shape that a naive reconstruction splits into nine disconnected pieces. One body "
        "means the chains were matched across the transition instead of being abandoned at it."),
    TourStep(
        target="viewport", title="A/B against the input mesh",
        advance="swap_toggled",
        body="Press B (View ▸ Swap input ↔ rebuilt) to hide the rebuilt solid and show the "
             "input mesh near-opaque. Press it again to swap back.\n\n"
             "Swap back and forth a few times with the fin region in view. This is the most "
             "honest comparison the tool offers: the same camera, the same frame, the mesh you "
             "gave it against the solid it made."),
    TourStep(
        target="viewport", title="Ghost the solid over the mesh",
        body="Now turn on View ▸ Rebuilt solid: transparent, with the input mesh visible "
             "underneath.\n\n"
             "Where the solid is right, the mesh disappears inside it. Look along the flat wall "
             "at z = 6000: the topology-event ring is drawn in its own colour, and it should sit "
             "exactly on the step in the input mesh."),
    _wrap_step(
        "M4",
        "A topology event, one body across it, and the two ways of comparing a result against "
        "its input. M5 adds domes on top of this and finally makes adaptive placement pay."),
]

# ---------------------------------------------------------------------------
# M5 — Domed finocyl, adaptive stations
# ---------------------------------------------------------------------------
DEMO_M5_STEPS = [
    _welcome(
        "M5 — Domed Finocyl, Adaptive Stations",
        "M2's domes and M4's fins in one part: an ellipsoidally domed capsule with a bore that "
        "grows eight fins in the aft half.\n\n"
        "This is the first demo where adaptive station placement is not optional. M5's gate "
        "demands at least 8 stations inside each dome AND tight stations around the fin "
        "transition, using no more than HALF the stations a uniform run would need for the same "
        "accuracy. Forty adaptive stations do what eighty-odd uniform ones would."),
    _input_step("harness/truth/M5.stl — domes at both ends, fins aft."),
    *_config_steps(
        axis="z", units="mm", sections=40, chord_tol=0.5, adaptive=True,
        why_frame="Along Z.",
        why_sections="Only 40 — fewer than M4's 80, on strictly harder geometry. That is the "
                     "point of this demo: the budget is small on purpose, so where the stations "
                     "go has to be earned rather than sprayed.",
        why_chord="0.5 mm, unchanged across the family.",
        why_adaptive="ON, and this is the demo where it matters. Adaptive placement reads the "
                     "geometry first and spends its 40 stations where curvature and topology "
                     "actually change — clustered into both domes and packed around the fin "
                     "transition — instead of laying them out evenly and hoping."),
    _analyze_step(""),
    _detected_step(
        "Note the suggested chord tolerance and the axial extent. With adaptive on, the "
        "SECTIONING dial's progress will look less even than on earlier demos: the engine is "
        "doing a feature-detection pass before it places anything."),
    _run_step("axis Z, 40 adaptive sections, 0.5 mm"),
    _verify_step(
        "All checks pass on 40 stations. Compare that to M4, which needed 80 uniform stations "
        "for simpler geometry — same tolerance, harder part, half the sections."),
    TourStep(
        target="page_stations.table", title="Where the stations went",
        on_enter="select_outline_stations",
        body="Scroll the station table and watch the axial spacing in the first column.\n\n"
             "It is not uniform. The gaps shrink inside each dome and around the fin "
             "transition, and stretch through the plain cylindrical middle where nothing "
             "changes. That distribution is what adaptive placement bought, and it is visible "
             "as data rather than as a claim."),
    TourStep(
        target="viewport", title="And where they went in 3D",
        advance="swap_toggled",
        body="The station rings in the viewport show the same thing spatially: dense at the "
             "ends and at the transition, sparse in the middle.\n\n"
             "Press B to swap to the input mesh and back, and check the domes especially — "
             "clustered stations are only worth anything if the dome they cluster into actually "
             "ends up in the right place."),
    _wrap_step(
        "M5",
        "Adaptive placement, seen both as a spacing pattern and as a result. M6 changes what a "
        "cross-section IS along the length."),
]

# ---------------------------------------------------------------------------
# M6 — Tapered star (loft)
# ---------------------------------------------------------------------------
DEMO_M6_STEPS = [
    _welcome(
        "M6 — Tapered Star (Lofted Profiles)",
        "M3's six-point star, but growing: 250/450 mm valley/tip radii at one end, scaled 1.5x "
        "to 375/675 mm at the other, ruled linearly in between.\n\n"
        "Nothing here can be revolved and nothing can be extruded. Every cross-section is a "
        "different star, so the surface between two stations has to be LOFTED — and the "
        "milestone's face-count gate (200 faces) only passes if consecutive windows are merged "
        "into shared faces instead of each becoming its own patch."),
    _input_step("harness/truth/M6.stl — the taper is subtle to the eye and obvious in a "
                "section."),
    *_config_steps(
        axis="z", units="mm", sections=60, chord_tol=0.5, adaptive=False,
        why_frame="Along Z.",
        why_sections="60. The profile changes continuously along the axis, so sections here buy "
                     "real fidelity — unlike M3, where every section was identical. The loft "
                     "between neighbours is only as good as how close together they are.",
        why_chord="0.5 mm. The fillets grow with the star (30/40 mm at the small end, 45/60 at "
                  "the large one), so the tightest curvature is at the SMALL end — that is "
                  "where the deviation argmax should be.",
        why_adaptive="Off. The taper is linear: the geometry changes at a constant rate along "
                     "the whole length, so there is no feature for adaptive placement to "
                     "cluster at. Uniform spacing is genuinely the right answer here."),
    _analyze_step(""),
    _detected_step(
        "As with M3, the interesting structure is internal and will not appear until the solid "
        "exists. Check watertightness and the axial extent, then run."),
    _run_step("axis Z, 60 sections, 0.5 mm"),
    _verify_step(
        "Volume under 0.2 % — and this one is a genuinely independent check, because the truth "
        "volume of a ruled frustum has a closed form (L/3 · (A₀ + √(A₀A₁) + A₁)) that has "
        "nothing to do with how the solid was built.\n\n"
        "Look at Faces in the manifest too. A lofted star that came out as a few hundred faces "
        "was merged properly; thousands would mean every window became its own patch."),
    TourStep(
        target="viewport", title="Look straight down the axis",
        body="Switch on View ▸ Orthographic projection, then use the orientation widget (or Fit "
             "view) to look end-on down the motor axis.\n\n"
             "In perspective, a tapering bore and a straight bore look nearly identical — "
             "perspective makes the far end smaller anyway. Orthographic removes that, so the "
             "small star sits INSIDE the large one as two concentric outlines. That nesting is "
             "the taper, seen directly."),
    TourStep(
        target="viewport", title="Confirm it in section",
        advance="section_toggled",
        body="Press S and sweep the slider from one end to the other.\n\n"
             "The star grows as you go. Set this against M3, where the identical sweep showed "
             "no change at all — same viewing tool, and the difference between an extrusion and "
             "a loft written in it."),
    _wrap_step(
        "M6",
        "Lofted profiles, and two view settings that make an axial change visible. M7 puts "
        "several separate holes in one section."),
]

# ---------------------------------------------------------------------------
# M7 — Central bore + satellites
# ---------------------------------------------------------------------------
DEMO_M7_STEPS = [
    _welcome(
        "M7 — Central Bore + Six Satellite Perforations",
        "A flat-ended cylinder with a 300 mm central bore all the way through, plus six 100 mm "
        "perforations on a 600 mm circle every 60° — running from one end to z = 7000 mm, where "
        "they stop at a flat wall.\n\n"
        "Watch for what happens at that wall. Up to z = 7000 each cross-section has seven "
        "interior loops; past it, one. Six chains are BORN at the start face and DIE at 7000, "
        "and matching loops from one station to the next — deciding which hole is which — is the "
        "capability this milestone forces."),
    _input_step("harness/truth/M7.stl — seven holes in the fore section, one in the aft."),
    *_config_steps(
        axis="z", units="mm", sections=60, chord_tol=0.5, adaptive=False,
        why_frame="Along Z.",
        why_sections="60, uniform. The chain deaths all happen at one axial position, so what "
                     "matters is having stations bracketing z = 7000 closely — which 60 evenly "
                     "spaced stations over 10 000 mm do, at about 170 mm apart.",
        why_chord="0.5 mm. Every loop in this part is a circle, so the chordal fit has an easy "
                  "job — expect deviation well inside tolerance.",
        why_adaptive="Off. As with M4, the feature here is a discontinuity rather than "
                     "curvature, and uniform spacing handles it cleanly."),
    _analyze_step(""),
    _detected_step("Frame and health as usual. The loop structure appears after the rebuild."),
    _run_step("axis Z, 60 sections, 0.5 mm"),
    _verify_step(
        "Bodies: 1 — six perforations and a bore, all one solid.\n\n"
        "Volume under 0.1 %, checked against a closed form: π(1000² − 300²)·L minus six "
        "cylinders of radius 100 and length 7000. There is no way to get that number right by "
        "accident."),
    TourStep(
        target="page_stations.table", title="Seven loops, then one",
        on_enter="select_outline_stations",
        body="Look at the radii column as you scroll down the table.\n\n"
             "Stations before z = 7000 list several loops; stations after it list one. The row "
             "where that changes is the topology event, and the engine found it by matching "
             "loops between neighbouring stations, not by being told where to look."),
    TourStep(
        target="viewport", title="The event ring",
        body="In the viewport, the station rings are drawn along the axis and the "
             "topology-event ring is drawn in its own colour.\n\n"
             "Find the event ring and check it sits at the flat wall in the input mesh, not "
             "somewhere near it. Toggle the station-ring and event layers from the View menu (or "
             "by clicking their legend rows) to see each on its own."),
    _wrap_step(
        "M7",
        "Multiple loops per station, chains being born and dying, and the event ring that marks "
        "it. M8 moves to a real mid-burn grain."),
]

# ---------------------------------------------------------------------------
# M8 — Mid-burn slotted grain
# ---------------------------------------------------------------------------
DEMO_M8_STEPS = [
    _welcome(
        "M8 — Mid-Burn Slotted Grain",
        "A domed capsule with a 450 mm bore through it and eight obround slots — 190 mm "
        "half-width, reaching to 850 mm radius, between z = 5850 and z = 9650, with their end "
        "edges filleted at 150 mm radius.\n\n"
        "This is a burnback surface partway through a burn: it is M5's cavity grown outward by "
        "a 150 mm web. Those filleted slot ends are the hardest thing in it — a fillet is a "
        "curved surface running around a corner, and it is exactly where a station-based "
        "reconstruction wants to cut across."),
    _input_step("harness/truth/M8.stl — domes, bore, and eight filleted slots aft of the "
                "middle."),
    *_config_steps(
        axis="z", units="mm", sections=80, chord_tol=0.5, adaptive=True,
        why_frame="Along Z.",
        why_sections="80, the milestone's cap. Two topology events (slot start at 5850, slot "
                     "end at 9650) plus two domes need stations in four separate places, and "
                     "the gate demands at least 10 in the fore wall band and 10 in the aft.",
        why_chord="0.5 mm. The 150 mm end fillets are gentle by this family's standards, so the "
                  "deviation should be comfortable — which is what makes M8 the right place to "
                  "learn to READ the deviation heatmap, before M9 shows you one that argues "
                  "with a bounds check.",
        why_adaptive="ON. This grain has curvature features — the domes and the filleted slot "
                     "ends — that adaptive placement is built for, and the station-band gates "
                     "are written assuming it. If you ever hit a crash on a slotted part with "
                     "adaptive on, though, try turning it OFF before adding sections: on sharp "
                     "slot transitions uniform placement is measurably more robust."),
    _analyze_step(""),
    _detected_step(
        "Triangle count and watertightness first. M8's mesh is analytic and clean — remember "
        "that, because M9 is the SAME grain arriving as a noisy scan, and the contrast between "
        "these two Detected pages is the whole of that demo."),
    _run_step("axis Z, 80 adaptive sections, 0.5 mm"),
    _verify_step(
        "All green. Note Deviation's p95 number and the tolerance beside it, and note how much "
        "margin there is.\n\n"
        "Deviation is a robust statistic across the entire surface — it is the check that "
        "actually says whether the reconstruction is faithful. Bounds, by contrast, compares "
        "single most-extreme points. Remember which is which."),
    TourStep(
        target="viewport", title="The deviation heatmap",
        advance="deviation_toggled",
        body="Turn on View ▸ Deviation heatmap.\n\n"
             "The solid is recoloured by the true point-to-surface distance between the input "
             "mesh and the rebuilt solid, scaled against the deviation tolerance. Cool colours "
             "are well inside it; warm colours are approaching it.\n\n"
             "On M8 this should be cool nearly everywhere, with whatever warmth there is "
             "gathered on the slot end fillets — the tightest curvature in the part. A heatmap "
             "that agrees with where you EXPECT the error is a heatmap you can trust later."),
    TourStep(
        target="viewport", title="Get in close",
        body="Zoom into one slot end and orbit it with the heatmap still on.\n\n"
             "You are looking for a small, local, gently-graded warm patch. What you do not "
             "want is a broad warm band across a flat region — that would mean a systematic "
             "offset rather than a curvature-fitting cost."),
    _wrap_step(
        "M8",
        "A realistic mid-burn grain, adaptive stations doing their job, and the deviation "
        "heatmap read on a run where you know what the answer should look like. M9 hands you "
        "the same grain as a noisy scan — and a failed check."),
]

# ---------------------------------------------------------------------------
# M9 — the known-failure demo. See §C.5 of docs/plans/help_and_demo_system.md and, for the
# lesson itself, pipeline/engine.py::_axial_bounds_hint (its docstring is the canonical account
# of the 2026-09-08 incident this demo teaches).
#
# The list is assembled from four blocks so the branch indices stay derived rather than
# hand-counted: the Run step branches to _M9_PASSED (the rare "it passed on your machine" path)
# or to _M9_FAILED (the teaching sequence), and both converge on the wrap-up.
# ---------------------------------------------------------------------------
_M9_HEAD = [
    _welcome(
        'M9 — Noisy Scan Input: Reading a "Failed" Check',
        "This demo runs a milestone at its official settings and ends with a FAILED check, on "
        "purpose. The point is learning to read a failure.\n\n"
        "M9 is M8's grain — same solid, exactly — arriving the way real data does: as a "
        "marching-cubes surface off an anisotropic 10 × 10 × 40 mm grid, about 550 000 sliver "
        "triangles with a 4:1 aspect ratio and half a millimetre of Gaussian noise on every "
        "vertex.\n\n"
        "It is the one demo worth doing even if you skip the rest."),
    _input_step(
        "harness/truth/M9.stl is that scan — around 44 MB. Its median edge length is about "
        "40 mm: the grid's coarsest spacing. Hold on to that number; it is the explanation for "
        "everything that happens at the end of this demo."),
    *_config_steps(
        axis="z", units="mm", sections=80, chord_tol=5.0, adaptive=True,
        why_frame="Along Z, same frame as M8.",
        why_sections="80, exactly as M8. The geometry is identical — only the mesh describing it "
                     "has changed — so the station budget has no reason to change either.",
        why_chord="5 mm, ten times M8's 0.5. That is not a compromise, it is the honest number. "
                  "Chord tolerance asks how close a fitted curve should sit to the mesh, and "
                  "this mesh bends at every single edge. Its faithfulness to the true surface "
                  "is set by its voxel spacing, not by sub-millimetre precision, so demanding "
                  "0.5 mm here would mean fitting the noise. A finer value does not help: 3.5 "
                  "crashes this mesh outright.",
        why_adaptive="ON, as M8. Same geometry, same features to cluster at."),
    _analyze_step(
        "Compare this Detected page with M8's when it appears — same part, and a very different "
        "mesh."),
    _detected_step(
        "Triangle count in the hundreds of thousands, and a suggested chord tolerance far "
        "coarser than any earlier demo — the tool is reading the mesh's own sag and telling you "
        "the same thing the 5 mm setting says.\n\n"
        "Watertight is still yes. Noisy does not mean broken; M9's mesh is a legitimate closed "
        "surface, just a rough one."),
    _run_step(
        "axis Z, 80 adaptive sections, 5 mm",
        "This is the biggest run so far — a couple of minutes is normal. It will finish "
        "successfully and write a STEP file. What comes next is about what the verification "
        "then says."),
]

_M9_PASSED = [
    TourStep(
        target="manifest_tree", title="Every check passed on this machine",
        on_enter="select_outline_output",
        body="Unusually, every check passed here — including Bounds Z, which this demo expects "
             "to fail.\n\n"
             "That is possible: M9's mesh is noise-seeded, and a single most-extreme point can "
             "land either side of a tolerance. The lesson is unchanged and worth reading anyway, "
             "because you WILL meet it on real scan data.\n\n"
             "Normally Bounds Z reports the solid stopping about 21 mm short of the input's aft "
             "dome tip, while Deviation passes comfortably at about 2.3 mm against a 10 mm "
             "gate. The hint on that row explains that this is the input mesh's own noise at a "
             "single extreme tip point — not something chord-tol or adaptive will move. Finer "
             "chord-tol (3.5) crashes this mesh; coarser (4.25) and the auto-suggested value "
             "(4.93) leave the shortfall identical.\n\n"
             "When Bounds and Deviation disagree like that, trust Deviation: it is a robust "
             "statistic over the whole surface, and Bounds is one point."),
]

_M9_FAILED = [
    TourStep(
        target="manifest_tree", title="Bounds Z failed — and that is the demo",
        on_enter="select_outline_output",
        body="Find the Bounds Z row. It is a cross, and it is AMBER, not red.\n\n"
             "Amber means informational. The run finished, the STEP was written, and this check "
             "does not change that — a failing verification row never changes the run's exit "
             "code. What it does is tell you something worth knowing about the result.\n\n"
             "The number beside it: the solid stops roughly 21 mm short of the input's aft dome "
             "tip. On this machine the exact value may differ slightly — the mesh is seeded, so "
             "it should be close."),
    TourStep(
        target="manifest_tree", title='Read the "WHAT TO DO" hint',
        body="Under the Bounds Z row is the engine's own hint. It says this is very likely the "
             "input mesh's own noise and coarseness right at the tip — a single extreme point, "
             "not a systematic fit problem — and specifically NOT something chord-tol or "
             "adaptive will move.\n\n"
             "That hint was earned rather than guessed. Every alternative was tried on this "
             "exact mesh: a finer chord tolerance (3.5) crashes the run outright; a coarser one "
             "(4.25) and the tool's own auto-suggested value (4.93) both leave the shortfall "
             "identical; adaptive on and off makes no difference either.\n\n"
             "The cause is the mesh. Median edge length 40 mm — the true surface position at the "
             "very apex carries real uncertainty at that scale, no matter how finely the SOLID "
             "is tessellated."),
    TourStep(
        target="manifest_tree", title="Now read Deviation",
        body="Deviation: p95 of about 2.3 mm against a 10 mm gate. Passing with roughly four "
             "times the margin.\n\n"
             "These two rows are measuring different things. Deviation is a robust statistic "
             "over the WHOLE surface — how far the input mesh sits from the rebuilt solid, "
             "everywhere. Bounds Z is the single most-extreme point along the axis, and one "
             "point is exactly what noise moves.\n\n"
             "When they disagree like this, trust Deviation. It is the evidence that the "
             "reconstruction is right; Bounds Z is the evidence that one tip is noisy."),
    TourStep(
        target="viewport", title="See the disagreement",
        advance="deviation_toggled",
        body="Turn on View ▸ Deviation heatmap and look at the aft dome tip.\n\n"
             "What you should see is a surface that is cool nearly everywhere — well under "
             "tolerance — with the disagreement confined to a tiny local spot at the very "
             "apex. That picture is the two checks reconciled: a good surface with one noisy "
             "extreme point on it."),
    TourStep(
        target=None, title="The rule of thumb",
        body="A failed check, plus a passing Deviation, plus an honest hint, means READ before "
             "you re-run.\n\n"
             "Not every cross is yours to fix. The expensive failure mode on real scan data is "
             "not a wrong answer — it is an afternoon spent grinding the chord tolerance up and "
             "down against a number that was never going to move, on a result that was correct "
             "the first time.\n\n"
             "The counterpart matters too: if Deviation had ALSO failed, or if the hint had "
             "pointed at the axis or units instead, that would be a real problem with a real "
             "knob. The tool tries to tell you which of the two you are looking at."),
]

_M9_TAIL = [
    _wrap_step(
        "M9",
        "You have seen a run finish, write a valid STEP, fail a check, and be right anyway — "
        "and you have seen how the tool tells you so. The written version of this same lesson "
        "is on the Verification page of the manual."),
]

DEMO_M9_STEPS = [
    *_M9_HEAD[:-1],
    # The Run step, rebuilt with its branch: passed -> the rare acknowledgement, failed -> the
    # teaching sequence. Absolute indices, validated by TourController and pinned in
    # tests/gui/test_demos.py.
    TourStep(
        target=_M9_HEAD[-1].target, title=_M9_HEAD[-1].title, body=_M9_HEAD[-1].body,
        advance=_M9_HEAD[-1].advance, constrain=_M9_HEAD[-1].constrain,
        branch=(len(_M9_HEAD), len(_M9_HEAD) + len(_M9_PASSED))),
    *_M9_PASSED,
    *_M9_FAILED,
    *_M9_TAIL,
]
# The "it passed" step skips the teaching sequence and lands on the wrap-up either way -- the
# same destination for both halves of the branch, because there is nothing left to decide.
_M9_TAIL_INDEX = len(_M9_HEAD) + len(_M9_PASSED) + len(_M9_FAILED)
DEMO_M9_STEPS[len(_M9_HEAD)] = TourStep(
    target=_M9_PASSED[0].target, title=_M9_PASSED[0].title, body=_M9_PASSED[0].body,
    on_enter=_M9_PASSED[0].on_enter, branch=(_M9_TAIL_INDEX, _M9_TAIL_INDEX))

# ---------------------------------------------------------------------------
# M10 — frame normalisation
# ---------------------------------------------------------------------------
DEMO_M10_STEPS = [
    _welcome(
        "M10 — Tiny, Tilted, and in Inches",
        "M8's grain at 1/40 scale (250 mm long, 25 mm outer radius), rotated so the motor axis "
        "runs along +X instead of +Z, moved well off the origin, and written to STL in "
        "INCHES.\n\n"
        "Nothing about the shape is new. Everything about the frame is wrong in the three ways "
        "real exported data is usually wrong, and this demo is about the tool putting it right: "
        "detecting the axis, applying the unit scale, and exporting the STEP back in the "
        "input's own frame rather than in a convenient internal one."),
    _input_step("harness/truth/M10.stl — the same slotted grain, forty times smaller, lying "
                "along X, with its numbers meaning inches."),
    *_config_steps(
        axis="auto", units="in", sections=80, chord_tol=0.0125, adaptive=True,
        why_frame='"auto", not "x". The tool finds the motor axis from the mesh\'s own inertia '
                  "and reports how confident it is — and it will find +X here. This is the "
                  "setting to use on data whose orientation you do not already know, which is "
                  "most real data.",
        why_sections="80, exactly as M8. Scale does not change how many cross-sections a shape "
                     "needs; the shape is identical.",
        why_chord="0.0125 mm — M8's 0.5 divided by 40, because the part is 1/40 the size. "
                  "Tolerances are absolute lengths, so a scaled part needs a scaled tolerance. "
                  "Leaving 0.5 here would be asking for a fit 2 % of the part's radius loose.",
        why_adaptive="ON, as M8."),
    _analyze_step(
        "Watch the axis result particularly. This is the first demo where you have not told the "
        "tool which way the part points."),
    _detected_step(
        "This page is the demo.\n\n"
        "Axis: X, with its confidence. It found that from the mesh alone.\n\n"
        "Axial extent: about 250 mm — a millimetre figure derived from an inch file, because "
        'the "in" setting scaled it on the way in. If this reads about 9.8 instead, the units '
        "setting is wrong, and that single mistake would quietly corrupt every tolerance and "
        "every check downstream."),
    _run_step("axis auto, inches, 80 adaptive sections, 0.0125 mm"),
    _verify_step(
        "Bounds X, Y and Z all pass — which means the STEP came back out in the input's own "
        "frame, off-origin and along +X, not rotated into some internal convenience frame. A "
        "part that imports into CAD in the wrong place is as useless as one with the wrong "
        "shape.\n\n"
        "The STEP itself is in millimetres. Units are an input-side setting; the output is "
        "always mm."),
    TourStep(
        target="telemetry_label", title="The telemetry line",
        body="The status bar's telemetry line carries the normalised frame — the detected axis, "
             "the extent, the station count — in a form you can read at a glance without "
             "leaving the viewport.\n\n"
             "On a real file this is the fastest sanity check the tool offers: if the axis or "
             "the extent is not what you expected, stop and fix the frame before spending time "
             "on tolerances."),
    TourStep(
        target="viewport", title="And it is where it should be",
        advance="swap_toggled",
        body="Press B to swap between the rebuilt solid and the input mesh.\n\n"
             "They occupy the same space. The axis line and station rings are drawn along the "
             "DETECTED axis, so if the detection had been wrong the rings would fan out "
             "sideways across the part instead of stacking neatly along it — a failure mode "
             "that is unmistakable once you have seen the correct version."),
    _wrap_step(
        "M10",
        "Axis auto-detection, unit normalisation, and an output that lands back in the input's "
        "frame. M11 changes how many solids come out."),
]

# ---------------------------------------------------------------------------
# M11 — multi-solid output
# ---------------------------------------------------------------------------
DEMO_M11_STEPS = [
    _welcome(
        "M11 — Three-Segment BATES (Multi-Solid Output)",
        "A segmented BATES grain: three separate annular segments with flat ends, with gaps "
        "between them. Segment A and C have 300 mm bores; the middle one has a 450 mm bore — a "
        "dual-grain configuration.\n\n"
        "One STL file, three disconnected shells, three solids out. Every earlier demo produced "
        "exactly one body and treated more than one as an error; this one expects three, and "
        "checks each segment's volume individually."),
    _input_step("harness/truth/M11.stl — three segments in one file, with empty space between "
                "them."),
    *_config_steps(
        axis="z", units="mm", sections=40, chord_tol=0.5, adaptive=False,
        why_frame="Along Z.",
        why_sections="40 — and this is a detail worth knowing: it means 40 stations PER BODY, "
                     "so the report will say 120. Section counts are per solid, not shared out "
                     "across whatever the file happens to contain.",
        why_chord="0.5 mm. Every loop is a circle; this is geometrically the easiest part in "
                  "the whole set, with a face-count budget of just 24.",
        why_adaptive="Off. Three straight tubes have nothing to cluster at."),
    _analyze_step(""),
    _detected_step(
        "The axial extent spans all three segments plus the gaps between them — Analyze looks "
        "at the mesh as a whole and does not yet separate it into bodies. That separation "
        "happens during the rebuild."),
    _run_step("axis Z, 40 sections per body, 0.5 mm"),
    _verify_step(
        "Bodies: 3 expected, 3 found. That is the row this whole demo exists for — one STL "
        "became three solids, each closed, each checked.\n\n"
        "Volume is checked per solid as well as in total (centroid-matched, so segment A is "
        "compared with segment A), which is what stops three bodies of the wrong sizes from "
        "averaging out to a right-looking total.\n\n"
        "In SpaceClaim this arrives as three separate bodies in the tree — worth knowing before "
        "you import it and wonder why there is not one."),
    TourStep(
        target="page_stations.table", title="120 stations, not 40",
        on_enter="select_outline_stations",
        body="Scroll the table. The stations run in three blocks with gaps between them, 40 to "
             "a segment.\n\n"
             "Look at the radii as you cross from the first block to the second: the bore jumps "
             "from 300 to 450 mm. Different segments genuinely being different is the "
             "dual-grain design, not a reconstruction artefact."),
    TourStep(
        target="viewport", title="Three stacks of rings",
        body="The station rings in the viewport show the same structure spatially: three "
             "distinct stacks with empty axis between them.\n\n"
             "Toggle the station-ring layer off and on from the View menu (or its legend row) "
             "to see the bare solids, then bring it back — the rings are drawn from the built "
             "solid's own cross-sections, so their radii change where the bore does."),
    _wrap_step(
        "M11",
        "Multi-body output, per-solid verification, and per-body station counts. M12 goes back "
        "to one body and makes its topology as hard as it gets."),
]

# ---------------------------------------------------------------------------
# M12 — near-burnout
# ---------------------------------------------------------------------------
DEMO_M12_STEPS = [
    _welcome(
        "M12 — Near-Burnout: Slots Breaking Through the Dome",
        "The same domed capsule, near the end of its burn: a 550 mm bore, eight obround slots "
        "reaching to 950 mm radius with only a 50 mm web left outside them, and 250 mm end "
        "fillets.\n\n"
        "Aft of about z = 9656 the slots BREAK THROUGH the dome. Stations there do not have one "
        "outer loop with holes in it — they have eight disjoint outer polygons, because the "
        "part has locally come apart into eight separate arcs of material. The bore exits "
        "through the domes too.\n\n"
        "This is the hardest topology the engine handles, and it is a shape a real motor "
        "genuinely passes through."),
    _input_step("harness/truth/M12.stl — thin webs, and slots opening into the aft dome."),
    *_config_steps(
        axis="z", units="mm", sections=120, chord_tol=0.5, adaptive=True,
        why_frame="Along Z.",
        why_sections="120, the most of any demo. Three topology events (5750, 9656, 9750) and "
                     "the breakthrough band between 9600 and 9750 must hold at least 10 "
                     "stations of its own — the geometry changes character three times along "
                     "one part.",
        why_chord="0.5 mm, with an added constraint: no edge in the result may be shorter than "
                  "0.1 mm. A 50 mm web between a slot and the outer surface is where slivers "
                  "get created, and a sliver edge is what makes a downstream mesher fail.",
        why_adaptive="ON. Without stations clustered around the breakthrough, the transition "
                     "from one outer loop to eight falls between two stations and the "
                     "reconstruction has to guess what happened in between."),
    _analyze_step("This is a bigger mesh — give it a moment."),
    _detected_step(
        "Frame and health. The interesting structure — three topology events — is a rebuild "
        "product, so run it and read them on the way out."),
    _run_step("axis Z, 120 adaptive sections, 0.5 mm"),
    _verify_step(
        "Bodies: 1. Eight arcs of material at the breakthrough, and still one connected solid, "
        "because they rejoin further forward.\n\n"
        "Volume under 0.3 %. The tolerance is looser than M1's 0.05 % on purpose: there is far "
        "less material left, so the same absolute error is a larger fraction of it."),
    TourStep(
        target="viewport", title="Section through the breakthrough",
        advance="section_toggled",
        body="Press S for Section view and move the slider toward the aft end, past about "
             "z = 9656.\n\n"
             "Forward of that, the section is a ring with slots cut into it. Aft of it, the "
             "ring is gone — eight separate islands of material with nothing joining them in "
             "this plane. Sweep back and forth across the transition a few times."),
    TourStep(
        target="page_stations.table", title="Three events in the table",
        on_enter="select_outline_stations",
        body="The topology events are marked in the station table at about z = 5750 (slots "
             "begin), 9656 (breakthrough) and 9750 (slots end).\n\n"
             "Click the rows around 9656 and watch the loop count in the radii column change. "
             "The engine detected all three from the sections themselves — the milestone gate "
             "allows at most five, so spurious events count against it as much as missed ones "
             "do."),
    _wrap_step(
        "M12",
        "Multi-outer-loop stations, three topology events, and thin-web geometry that stayed "
        "one solid. M13 puts everything together on a deliberately filthy input."),
]

# ---------------------------------------------------------------------------
# M13 — capstone
# ---------------------------------------------------------------------------
DEMO_M13_STEPS = [
    _welcome(
        "M13 — Dirty Real-World Capstone",
        "M12's near-burnout geometry, delivered the way real scan data actually arrives:\n\n"
        "  • about 5 million triangles from an 8 mm marching-cubes grid\n"
        "  • 0.8 mm of noise on every vertex\n"
        "  • UNWELDED — every facet has its own copies of its corners, jittered apart\n"
        "  • 2 % of facets wound backwards\n"
        "  • three junk islands of debris floating inside the bore\n"
        "  • rotated onto +X, moved off the origin, and written in inches\n\n"
        "It is NOT watertight as delivered. The tool has to repair it before it can rebuild it, "
        "and it has to drop the islands rather than treat them as bodies. This is the capstone: "
        "everything the previous twelve demos taught, at once, on data that fights back."),
    _input_step(
        "harness/truth/M13.stl is around 253 MB. Analyze alone can take about 19 minutes on "
        "this file, and most of that is welding the unwelded vertices back together — the run "
        "is not stuck."),
    *_config_steps(
        axis="auto", units="in", sections=120, chord_tol=8.0, adaptive=True,
        why_frame='"auto", as M10 — and here you genuinely do not know: the part is rotated onto '
                  "+X and moved a long way off the origin.",
        why_sections="120, as M12. Same geometry, same three topology events, same breakthrough "
                     "band to resolve.",
        why_chord="8 mm — the marching-cubes grid spacing itself. As with M9, asking for finer "
                  "than the data's own resolution means fitting noise. This is the single most "
                  "important number to get right on a real scan, and the rule is: start from "
                  "the mesh's own scale, not from what you wish it were.",
        why_adaptive="ON, as M12."),
    _analyze_step(
        "This is the long one. The ANALYZE dial creeps rather than steps, because the repair "
        "pass reports progress sparsely — that is expected, not a hang. Go and get a coffee."),
    _detected_step(
        "Read all four rows carefully; this page is the repair report.\n\n"
        "Axis: X, found on a noisy mesh. Axial extent: in millimetres, from an inch file.\n\n"
        "Watertight: the mesh as delivered was not, and this tells you what state it is in "
        "after repair. Triangle count in the millions.\n\n"
        "The three junk islands are dropped rather than rebuilt — that is why the run produces "
        "one solid and not four."),
    _run_step("axis auto, inches, 120 adaptive sections, 8 mm",
              "The rebuild itself is quicker than the Analyze that preceded it."),
    _verify_step(
        "Bodies: 1. The islands were discarded, and the real geometry stayed one solid through "
        "the breakthrough.\n\n"
        "Volume under 0.5 %, Deviation p95 well inside a tolerance derived from the 8 mm grid. "
        "This is a genuine end-to-end result on genuinely bad data — and it is the closest "
        "thing in this set to what your own files will look like."),
    TourStep(
        target="telemetry_label", title="Read the telemetry line",
        body="The status bar carries the detected axis, the normalised extent and the station "
             "count.\n\n"
             "On a file this size those three numbers are your first checkpoint. If the axis or "
             "the extent is wrong, nothing downstream can be right, and you would rather learn "
             "that from one line than from a heatmap twenty minutes later."),
    TourStep(
        target="viewport", title="Five million triangles, judged",
        advance="deviation_toggled",
        body="Turn on the deviation heatmap and orbit the part.\n\n"
             "Warmth here means the fitted surface sits toward the edge of tolerance, which on "
             "8 mm noisy data is expected in the tight places — slot ends, the breakthrough, "
             "the dome tips. What you are checking is that it is LOCAL. Broad warm regions "
             "across smooth areas would mean a frame or units problem, and the fix for that is "
             "never the chord tolerance.\n\n"
             "Swap to the input mesh with B if you want the raw scan back for comparison."),
    _wrap_step(
        "M13",
        "Repair, frame detection, unit normalisation, hard topology, island rejection and "
        "verification, on five million dirty triangles.\n\n"
        "That is all thirteen. The runbook for taking your OWN burnback STL through the same "
        "path — starting with how to choose a chord tolerance — is in HANDOFF.md §5, and the "
        "Troubleshooting page of this manual covers what to do when a run does not finish."),
]
