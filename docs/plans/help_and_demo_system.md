# Plan: in-app Help/Manual, guided milestone demos, and no-install launchers

_Implementation plan, 2026-09-08. Written from a full read of the current `app/` + `pipeline/engine.py`
+ MISSION.md/HANDOFF.md/WORK_SETUP.md. Treat file/line references as accurate as of commit `e70bb1a`
(plus the uncommitted `pipeline/engine.py` working-tree change) — re-verify line numbers before
editing, the anchors quoted alongside them are the stable thing to search for._

**Audience: the implementation agent.** You have no memory of the conversation that produced this.
Every product decision has already been made below — do not re-open them without flagging to Brady.
Brady's verbatim ask, condensed: (1) a help section / quick-start guide / manual **baked into the
program**, covering all configurations, view options, and steps; (2) the 13 milestone STLs as
click-to-start **guided demos with descriptive burnback-type names**, walked through step by step
with **speech-bubble popups anchored to the actual controls with an arrow** (explicitly NOT text in
an existing panel), including at least one demo (M9) that **starts in a known-failure configuration
and teaches the user through the failure**; (3) a **no-install, no-admin, double-click launcher**
(explicitly NOT a PyInstaller `.exe`/installer — Brady cannot run installers at work; run-from-source
is the settled deliverable, see `WORK_SETUP.md` and MISSION §12's "no packaging" decision).

**Hard rules inherited from this project:**
- `harness/` is FROZEN (tag `harness-frozen`; the driver restores it before scoring). **Read-only
  imports of `harness.generators` are fine and precedented** (`app/smoke.py` line ~57 already does
  `from harness import generators; generators.make("M2")`). Never edit anything under `harness/`.
- Do not break the existing `python -m app --smoke <outdir>` contract (`app/smoke.py::run_smoke`)
  — `loop.py::_gui_smoke_ok` and `tests/gui/test_smoke.py` gate on it. Add new smoke entry points
  instead of changing that one.
- Test conventions: `tests/gui/` uses pytest-qt with `QT_QPA_PLATFORM=offscreen`, a `window`
  fixture building `MainWindow(offscreen=True)` (`tests/gui/test_main_window.py`), heavy use of
  `monkeypatch` for dialogs. `tests/api/` tests `pipeline.engine` directly. Full suite currently
  green (~151–166 tests depending on count method); it must stay green.
- Deliverable files Brady picks up in a GUI (the launcher artifacts) must be **deleted before
  being (re)written** — never overwritten in place (macOS APFS birthtime rule; also `zip` appends).
  `app/main_window.py::_export_viewport_image` (line ~317) shows the in-repo precedent
  (`os.remove(path)` before write).
- No emojis in UI text. Match the existing writing voice: plain, technical, direct.

---

## 0. Decisions made (summary — details in each section)

| Question | Decision |
|---|---|
| Where demo STLs live | **Generate on demand into `harness/truth/` via `harness.generators.make()`** — no copies, no symlinks, no new `demos/` data dir. `harness/truth/` is **gitignored** (see `.gitignore`: `harness/truth/`), so the STLs do not travel with the repo at all; "copy them into `demos/`" is not actually an option without committing up to 253 MB (M13) of binary STL. The generators ARE the portable artifact — `WORK_SETUP.md` §4 already documents exactly this pattern for the work machine. Coupling to the frozen harness is read-only and isolated behind one function (`app/demos.py::ensure_demo_mesh`); if `harness/` is ever reorganized, one function changes. |
| Coachmark widget architecture | **Two frameless top-level tool windows** (bubble + target halo), NOT a child-widget overlay with a spotlight cutout. Reason: the central widget is a native OpenGL `QtInteractor`; Qt child widgets stacked over native GL surfaces are unreliable cross-platform (the app already works around this class of problem — see `app/viewport.py`'s offscreen-composite dance). Top-level windows always stack above the GL surface. |
| Input constraint during a tour | **Soft constraint on "action" steps only**: an application-level event filter swallows mouse presses outside {target widget + its children, the bubble, the demo's own option widgets} while a step that waits on a real user action is active. Pure-explanation ("Next"-button) steps constrain nothing. Esc / "End tour" always works. Rationale: full lock-out fights Qt native menus/dialogs and frustrates users; zero constraint lets a stray click desync the script. Soft constraint + a per-step settings validation check (see §C.4) is the robust middle. |
| How a demo pre-fills settings | The picker fills **input/output paths only** and resets option widgets to defaults; the tour steps then *instruct the user* to set each option (teaching value). Before the "click Run" step advances, the controller **validates** the option widgets against the demo's canonical args and corrects the user in the bubble if they differ. |
| Manual format | Markdown files in `app/help_content/*.md`, rendered in a non-modal `HelpDialog` (TOC tree + `QTextBrowser.setMarkdown`). No web engine, no new dependencies, travels with the repo. |
| macOS launcher | **A minimal hand-written `.app` bundle** (generated, ~3 files) as the primary, with a plain `.command` file generated alongside as the documented fallback. See §D for the tradeoff. |
| Windows launcher | Generated `.bat` (console, debuggable) in `launchers/` + a Desktop `.lnk` created via a PowerShell one-liner pointing at `pythonw.exe -m app` (no console flash), Desktop path resolved by PowerShell (`[Environment]::GetFolderPath('Desktop')` — handles OneDrive-redirected Desktops on corporate machines). |
| Where generation logic lives | `scripts/create_desktop_shortcut.py` (standalone + importable) does the work; a `Help → Create Desktop Shortcut` menu action calls it in-process and reports the result. Both, not either. |
| Repo home for launcher templates | Templates are string constants inside `scripts/create_desktop_shortcut.py` (they are small); generated artifacts land in a new gitignored `launchers/` dir in the repo root plus the Desktop copy. |

New modules (all phases):

```
app/help_content/           Phase A — manual pages (markdown, data files)
    00_quick_start.md  01_workflow.md  02_input_options.md  03_viewport.md
    04_outline_details.md  05_stations.md  06_verification.md  07_dashboard.md
    08_shortcuts.md  09_troubleshooting.md  10_demos.md
app/help.py                 Phase A — HelpDialog (TOC + QTextBrowser), content loader
app/tour.py                 Phase B — CoachmarkBubble, TargetHalo, TourController, TourStep
app/demos.py                Phase C — DEMOS catalog, DemoPickerDialog, ensure_demo_mesh/worker
app/demo_scripts.py         Phase C — the 13 step lists (content-only module, heavy on text)
scripts/create_desktop_shortcut.py   Phase D — launcher generator (mac + Windows)
tests/gui/test_help.py  tests/gui/test_tour.py  tests/gui/test_demos.py
tests/test_shortcuts.py
```

Modified files: `app/main_window.py` (Help menu, three new signals, tour hooks),
`app/smoke.py` (new `run_tour_smoke`, existing `run_smoke` untouched), `app/__main__.py`
(`--smoke-tour` flag), `.gitignore` (`launchers/`), `WORK_SETUP.md` (§6 note pointing at the new
in-app shortcut generator), `HANDOFF.md` optional §8 note.

Phase order and independence: **A, B, D are mutually independent** and can be built/tested in any
order. **C depends on A (menu plumbing) and B (the coachmark engine).** Suggested order:
A → B → C → D (D can be interleaved anywhere).

---

## Phase A — In-app Help / Manual

### A.1 Content inventory (COMPLETE — write the manual from this, it was compiled from a full read of the code)

The manual must document every item below. File/anchor references are for the implementer's own
verification, not for the manual text.

**Menus & toolbar** (`app/main_window.py::_build_menu_and_toolbar`, line ~139):
- File: Open STL… (Ctrl+O), Export viewport image… (Ctrl+E, saves the current 3D view as PNG),
  Quit (Ctrl+Q).
- Run: Analyze (F5), Run (Ctrl+R), Cancel (Esc). Toolbar mirrors Open/Analyze/Run/Cancel with
  text-beside-icon buttons.
- View — six checkable **layer toggles**: Input mesh, Rebuilt solid, Station rings, Dome-cap fit,
  Topology events, Axis line (each syncs bidirectionally with the clickable viewport legend rows).
- View — comparison modes: "Swap input ↔ rebuilt" (B) — input near-opaque, solid hidden, for A/B;
  "Rebuilt solid: transparent" — ghost the solid to compare fin/wall geometry against the input;
  "Input mesh: solid color" — full-opacity input to compare its outer boundary (wins over
  swap/overlay opacity).
- View — camera: Fit view (F), Orthographic projection (O — parallel edges stay parallel, for
  silhouette/alignment checks).
- View — inspection: Section view (S — clip along the motor axis with the bottom slider overlay,
  see the bore directly instead of through transparency), Deviation heatmap (color the solid by
  measured distance to the input mesh — shows WHERE the reconstruction deviates, scale anchored to
  the verification tolerance).
- View → Render mode submenu: Shaded (Ctrl+1), Shaded + edges (Ctrl+2 — REAL geometric edges from
  a dihedral-angle threshold, not raw triangulation; a smooth revolved wall correctly shows none),
  Wireframe (Ctrl+3). W cycles (shortcut-only action, no menu entry).
- Help: currently only "About STL Rebuilder" (line ~264). Phase A/C/D add entries here.

**Input page** (Details dock, Outline ▸ Input; `_build_input_page`, line ~423):
- Source: Input STL (line edit + Browse; typing/pasting a path loads a preview on Enter/focus-out),
  Output STEP (default `out/rebuilt.step`).
- Geometry: Axis combo (auto / x / y / z — auto re-detects fresh on EVERY rebuild), Units combo
  (mm / in / m — STLs carry no units; output STEP is always mm).
- Fidelity: Sections spinbox (4–500, default 40 — cross-section stations along the axis; M11-style
  multi-body inputs get this count PER body), Chord tol (mm) spinbox + "auto from mesh" checkbox
  (checked by default; the auto value is a chordal-SAG estimate from Analyze — `2× p95` of
  per-edge sag — NOT the median edge length, which wildly overestimates on clean CAD tessellations;
  when checked, the spinbox is disabled and Run will silently run Analyze first if no analysis for
  this input exists yet), "adaptive stations" checkbox (clusters stations at detected features;
  tooltip already warns uniform is often MORE robust for sharp slot/fillet transitions — if a run
  crashes with adaptive on, turn it off before adding sections).
- Actions row: Analyze, Run (primary), Cancel buttons. Both Analyze and Run disable for the whole
  in-flight duration of any operation.
- Workflow semantics to document: Analyze = load/repair + frame/scale detection + quality metrics,
  no geometry built. Run = rebuild (chains Analyze first automatically when "auto from mesh" needs
  it). Every run writes `<output>.report.json` and `<output>.preview.stl` next to the STEP.

**Detected page** (Outline ▸ Detected; `_analysis_property_groups`, line ~956): Frame group
(Axis + confidence %, Origin X/Y, Units), Mesh group (Extent, Bodies, Triangles, Median edge
length, Watertight, Dropped islands, Bounds), Suggested run settings (Chord tol (auto)).

**Stations page** (Outline ▸ Stations; `_build_stations_page` + `app/widgets.py::StationTable`):
radius-profile chart (R_outer + R_bore vs z in report frame, red vertical lines at topology
events) above the per-station table (#, Z, Loops, R_outer, R_bore, Class = barrel/dome/transition,
amber rows = topology events). Selecting a row highlights that station's ring in the 3D view AND
on the chart — three views of one selection. R values come from an exact planar slice of the
rebuilt solid at that station.

**Output page** (Outline ▸ Output; `_build_output_page` + `_manifest_property_groups` +
`_verification_group`): error banner (on failure), then the manifest tree —
- **Verification group** (one row per engine check; ✓ green = pass, ✗ amber = fail-informational,
  – grey = n/a; **a failing check never blocks the run or changes the output**):
  - Watertight — input mesh watertightness (always true by this point; shown as confirmation).
  - Volume — input mesh vs STEP solid, tolerance 0.5 %.
  - Bounds X/Y/Z — per-axis input-vs-solid bounding ranges, tolerance
    `max(2×chord_tol, 0.1 % of axial extent)`.
  - Bodies — expected vs actual solid count in the STEP.
  - Deviation — approximate sampled surface deviation, pass gated on **p95** ≤ 2×chord_tol
    (a robust whole-surface statistic — document explicitly that this is the primary "is the
    reconstruction actually right" signal, per the M9 lesson).
  - Failed rows append a visible "WHAT TO DO:" hint computed by the engine (`_with_hint`,
    line ~993). Document the M9-class case verbatim-in-spirit: a Bounds fail on the motor axis
    with Deviation passing comfortably is usually the input mesh's own noise/coarseness at a
    single extreme tip point — not something `--chord-tol` or `--adaptive` will move
    (`pipeline/engine.py::_axial_bounds_hint`, line ~1686).
- Output group (STEP file, Preview STL), Result group (Bodies, Faces, Volume, Stations), Report
  group (Topology events, Paths used), Warnings group, and the "Reveal file" button.

**Dashboard dock** (`app/dashboard.py`): mission clock (T+ HH:MM:SS, LED style; starts on any
run, freezes on done/fail — an Analyze chaining into a Run is ONE mission), status strip
(STANDBY / RUNNING — STAGE / NOMINAL / FAULT — KIND; a user Cancel shows ABORTED, not a fault),
five gauge dials ANALYZE / LOAD / SCAN / SECTIONING / BUILD (needle + progress arc + LED %
readout + stage caption; document honestly that between sparse engine checkpoints the needle
"creeps" as an is-alive indicator, capped at 93 % — it is not claiming measured progress), the
log console below (resizable via the splitter). A refine retry pass re-activates the SECTIONING/
BUILD dials rather than resetting them — that is deliberate, not a bug.

**Viewport** (`app/viewport.py`): empty-state hint; top-left display-toggle overlay (render-mode
cycle, section view, deviation heatmap — synced with the View menu); top-right clickable legend
(one row per active layer; click toggles); bottom-left camera-orientation widget (click/drag
snaps to axis views) + the single "home" button beside it (reset to isometric); bottom-center
SECTION slider when section view is on. Colors worth a legend table in the manual: input mesh
lightsteelblue ghost, rebuilt solid blue #3f9fdc, station rings yellow (+ tick combs at the
silhouette for edge-on visibility), dome-cap fit mint (rings + longitudinal meridians — marks the
deliberately station-free end bands rebuilt from the dome fit; NOT unverified geometry), topology
events red, axis line grey, highlight ring bright yellow.

**Status bar**: status text, telemetry line (axis · units · triangles · stations), progress bar
(visible only during a run), busy spinner, version.

**Keyboard shortcuts (complete)**: Ctrl+O, Ctrl+E, Ctrl+Q, F5, Ctrl+R, Esc, B, F, O, S, W,
Ctrl+1/2/3.

**Troubleshooting content** (source: `WORK_SETUP.md` §8 + the engine's own hint taxonomy in
HANDOFF §5.4/§5.5): exit-code taxonomy (input unusable / topology / geometry validity), the
adaptive-crash advice, wrong-axis/wrong-units symptoms, blank-3D-view `QT_OPENGL=software`, and
the "failing verification is informational" framing.

### A.2 Files

**`app/help_content/*.md`** — one file per TOC entry, filenames as listed in §0. Plain Markdown,
no HTML. First line is an `# H1` used as the page title. Keep each page skimmable: a one-paragraph
intro, then tables/definition lists mirroring the inventory above. `00_quick_start.md` is a
10-step "open → analyze → run → read the verification" walkthrough of M2 (mention Help ▸ Guided
Demos as the interactive version once Phase C exists — write the sentence now, it degrades
gracefully). `10_demos.md` lists the 13 demos with one line each (write in Phase C, stub in A).

**`app/help.py`** — new module:

```python
HELP_DIR = os.path.join(os.path.dirname(__file__), "help_content")

def load_pages() -> list[tuple[str, str, str]]:
    # [(page_id, title, markdown_body)] sorted by filename; title = first "# " line,
    # stripped from the body. Raises nothing; a malformed file contributes ("<file>", raw).

class HelpDialog(QDialog):
    # Non-modal (show(), not exec()), 900x640 default, WindowStaysOnTopHint NOT set.
    # Layout: QSplitter horizontal — left QTreeWidget TOC (flat list, one item per page,
    # min width 200), right QTextBrowser.
    # QTextBrowser: setOpenExternalLinks(False); anchorClicked -> if url path matches another
    # page's filename or page_id, navigate there and sync the TOC selection; else ignore.
    # setMarkdown(body) per page. Search: a QLineEdit above the TOC filtering TOC items by
    # simple case-insensitive substring against title+body (hide non-matching items). That is
    # the whole search feature — no highlighting, no index.
    # Styling: the app QSS covers QTreeWidget/QTextEdit; QTextBrowser inherits QTextEdit rules
    # in app/theme.py already (selector says "QTreeWidget, QTextEdit, ..." — QTextBrowser
    # subclasses QTextEdit so it matches). Verify in the screenshot pass; if body text renders
    # grey-on-dark too dim, set a widget-level stylesheet color: TEXT_PRIMARY.
    def show_page(self, page_id: str): ...   # used by menu deep-links and tests
```

**`app/main_window.py` integration** — in `_build_menu_and_toolbar`, replace the current Help
menu block (search anchor: `help_menu = menubar.addMenu("&Help")`, line ~264) with:

```
Help
  Quick Start Guide        F1    -> self._show_help("00_quick_start")
  Manual                         -> self._show_help(None)   # last page or first
  ----
  Guided Demos...                -> Phase C (add the action in A, disabled with a
                                    tooltip "coming in this build" ONLY if C ships
                                    separately; otherwise add in C)
  ----
  Create Desktop Shortcut...     -> Phase D
  ----
  About STL Rebuilder            -> existing _show_about
```

`_show_help(page_id)` lazily creates a single `HelpDialog` instance (`self._help_dialog`),
`show()` + `raise_()` + `activateWindow()`, then `show_page(page_id)` if given. Keep the dialog
parented to the main window so it closes with the app.

### A.3 Verification (Phase A)

1. `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/gui -q` — all pre-existing tests
   still pass.
2. New `tests/gui/test_help.py`:
   - `load_pages()` returns ≥ 11 pages, every body non-empty, titles unique.
   - Dialog opens offscreen; iterating TOC items yields a non-empty
     `browser.toPlainText()` for each page.
   - **Completeness canaries**: assert the concatenated manual text contains a fixed list of
     strings that must never silently drop out of the docs: "Ctrl+R", "auto from mesh",
     "adaptive", "Section view", "Deviation", "Bounds", "watertight", "Swap input", "chord",
     "Render mode", "B", "F5". (Cheap, catches accidental page deletion.)
3. Screenshot pass (this project's convention — LOOK at the pixels): temporary snippet or the
   Phase C `--smoke-tour` (which grabs the help dialog too, §C.6); until then:
   `QT_QPA_PLATFORM=offscreen` + a 10-line script instantiating `HelpDialog`, `grab().save()`
   per page into `out/help_review/`, then actually read the PNGs for: dark-theme legibility,
   heading hierarchy, tables not overflowing.

---

## Phase B — Coachmark engine (generic, demo-content-agnostic)

All in **`app/tour.py`**. No imports from `app/demos.py` (dependency points the other way).

### B.1 `TourStep` (frozen dataclass)

```python
@dataclass(frozen=True)
class TourStep:
    target: str | None      # attribute path on MainWindow, resolved by _resolve_target():
                            # a plain dotted getattr chain, e.g. "run_btn", "adaptive_check",
                            # "sections_spin", "viewport", "manifest_tree", "outline".
                            # None => bubble centered over the viewport, no pointer, no halo.
    title: str              # bubble header, short ("Step 3 — Set the station count")
    body: str               # bubble text, plain string; \n\n = paragraphs. Word-wrapped.
    advance: str = "next"   # "next" | a key in TourController.SIGNAL_KEYS (see B.3)
    constrain: bool = False # True => soft input constraint while this step is active (B.4)
    expect: dict | None = None  # option-widget expectations checked before a "run"/"analyze"
                            # advance fires the action-blocked correction (B.5), e.g.
                            # {"sections_spin": 80, "adaptive_check": True,
                            #  "chord_tol_auto": False, "chord_tol_spin": 5.0,
                            #  "axis_combo": "z", "units_combo": "mm"}
    on_enter: str | None = None  # optional named side-effect run when the step is shown:
                            # "select_outline_output" | "select_outline_stations" |
                            # "select_outline_input" | "select_outline_detected" — the ONLY
                            # four supported values; implemented as a dict of lambdas in
                            # TourController. Do not grow this into a scripting language.
```

`_resolve_target(window, path)`: `functools.reduce(getattr, path.split("."), window)`; raises
`AttributeError` naturally — tests iterate every demo step and resolve every path against a real
offscreen `MainWindow` (§C.7), so a typo fails CI, not a user's tour.

### B.2 `CoachmarkBubble(QWidget)` — the speech bubble

- Top-level: `super().__init__(parent=window, flags=Qt.Tool | Qt.FramelessWindowHint)`,
  `setAttribute(Qt.WA_TranslucentBackground)`, `setAttribute(Qt.WA_ShowWithoutActivating)`.
  Parenting to the window keeps it above the app and closes it with the app; `Qt.Tool` keeps it
  out of the taskbar/Dock.
- Fixed content width 340 px; height from the wrapped text (`QLabel` with `setWordWrap(True)`
  inside a layout; call `adjustSize()` after setting text).
- **Painted body** (in `paintEvent`, same hand-drawn QPainter idiom as `app/dashboard.py`):
  a rounded rect (radius 6, fill `BG_PANEL`, 1 px border `TELEMETRY` — the "live data" color,
  deliberately not ACCENT per the theme's own token comment) inset by `POINTER = 14` px on every
  side, plus a filled triangle of height `POINTER`, base 22 px, on the side facing the target,
  positioned along that edge to aim at the target's center (clamped 20 px from the bubble's
  corners). Store `self._pointer_side` ("left"/"right"/"top"/"bottom"/None) and
  `self._pointer_along` (px along the edge); `set_pointer(side, along)` triggers `update()`.
- Content stack (normal child widgets over the painted body, margins = POINTER + 12):
  - Title row: `QLabel` bold + a right-aligned step counter `QLabel` ("3 / 11", `TEXT_SECONDARY`,
    mono family — reuse the `_mono_font` idiom from `app/widgets.py`).
  - Body `QLabel`, `TEXT_PRIMARY`, wrap on.
  - Button row: "Back" (flat, hidden on step 1), stretch, "End tour" (objectName `danger`
    styling is too loud — plain button, text only), "Next ▸" (objectName `primary`) — **"Next" is
    hidden when `advance != "next"`**; in its place a passive hint label in `TELEMETRY` italic:
    "waiting — perform the action above" (exact text supplied by the controller, e.g.
    "→ click Analyze to continue").
- Signals: `next_clicked`, `back_clicked`, `skip_clicked`.
- `place_near(target_global_rect: QRect, window_screen: QScreen)`: choose the first side in
  preference order **right, left, below, above** where the bubble fits inside
  `window_screen.availableGeometry()` with an 8 px gap; if none fits, center on the screen with
  no pointer. Set geometry AND pointer side/along in one shot. This method is pure geometry —
  unit-test it directly with synthetic rects (no showing needed).

### B.3 `TargetHalo(QWidget)` — the "arrow's landing zone"

A second frameless translucent top-level (same flags), `setAttribute(Qt.WA_TransparentForMouseEvents)`
so it never eats the click it is pointing at. Geometry = target's global rect grown by 6 px.
`paintEvent`: a 2 px rounded-rect outline in `TELEMETRY` with a slow opacity pulse (QTimer 50 ms,
sinusoidal alpha 120–255 — the `BusySpinner`/`GaugeDial` timer idiom). The bubble's triangle
points at this halo; together they read unambiguously as "this control, here."

### B.4 `TourController(QObject)`

Owns one tour run. Constructor: `TourController(window: MainWindow, steps: list[TourStep],
title: str)`.

State: `_i` (current index), `_bubble`, `_halo`, `_active` flag, `_connected` (the QMetaObject
connection for the current step's advance signal, if any).

```python
SIGNAL_KEYS = {
    # key           -> (how to obtain the signal from the window, one-line doc)
    "analyze_done":   lambda w: w.analyze_finished,
    "rebuild_done":   lambda w: w.rebuild_finished,
    "run_failed":     lambda w: w.run_failed,
    "verify_failed":  lambda w: w.rebuild_finished,   # advance on completion; the STEP FAILING
                                                      # is in the report, not a Qt signal — the
                                                      # M9 script branches on it via expect_fail
                                                      # (see below); keep this alias anyway for
                                                      # script readability.
    "file_loaded":    lambda w: w.input_loaded,
    "swap_toggled":   lambda w: w.swap_action.toggled,
    "section_toggled":lambda w: w.section_view_action.toggled,
    "deviation_toggled": lambda w: w.deviation_action.toggled,
}
```

**Three new signals on `MainWindow`** (Phase B's only main_window edit):
- `analyze_finished = Signal()` — emit at the top of `_on_analyzed` (anchor:
  `self._analysis = analysis`, line ~714), AFTER state is stored, and only when
  `_pending_rebuild` is False OR unconditionally — decide: **emit unconditionally**; the
  controller only listens when its current step asked for it, and a chained Analyze→Rebuild demo
  step should still advance on the analyze completion it asked for.
- `rebuild_finished = Signal(bool)` — emit at the END of `_on_rebuilt` (after
  `self.outline.setCurrentItem(self.node_output)`, line ~940) with
  `all(c.get("pass") is not False for c in _iter_verification_checks(report))` — add a tiny
  module-level helper `_verification_all_passed(report) -> bool` beside `_verification_group`
  that walks `report["verification"]` the same way `_verification_group` does (watertight,
  volume, bounds x/y/z, bodies, deviation) and returns False iff any `pass is False`.
- `run_failed = Signal(str, str)` — emit at the end of `_on_failed` (line ~942) with
  `(kind, message)`.

Lifecycle:
- `start()`: create bubble + halo, install `self` as `QApplication.instance().eventFilter`
  (`installEventFilter` on the app object) for the constraint + reposition logic, show step 0.
- `_show_step(i)`: disconnect any previous advance connection; resolve target (None → no halo,
  bubble centered over `window.viewport` global rect); run `on_enter` action if set; halo to
  target rect; `bubble.place_near(...)`; wire advance:
  - `"next"` → bubble.next_clicked → `_advance()`.
  - signal key → `SIGNAL_KEYS[key](window)` connected once to `_on_step_signal(*args)`; for
    `rebuild_finished` the bool arg is captured for the M9 branch (see §C.5).
  - Back button → `_show_step(i-1)` (Back never re-runs actions; document in the bubble that
    Back only re-reads earlier text — it does NOT undo a rebuild).
- `_advance()`: `expect` check first (B.5); then `i+1` or `_finish()`.
- `cancel()` / Esc (the event filter watches for `QKeyEvent` Esc while active) / skip_clicked →
  `_finish()`.
- `_finish()`: remove app event filter, close+delete bubble/halo, emit `finished = Signal()`.
- **Repositioning**: the app-level event filter watches for `QEvent.Move`/`QEvent.Resize` on the
  main window and `QEvent.Resize`/`QEvent.Move`/`QEvent.LayoutRequest` on the current target's
  window ancestry; on any, single-shot `QTimer.singleShot(0, self._reanchor)` (coalesces bursts)
  which recomputes halo+bubble geometry from the target's current `mapToGlobal`. Also re-anchor
  on `QEvent.Show`/`QEvent.Hide` of the target (a dock being closed mid-tour: if the target
  becomes invisible, fall back to centered-bubble mode with a one-line prefix "(<control> is in
  the <dock name> dock — reopen it via View)" — implement as: if `not target.isVisible()`, treat
  as target=None for geometry purposes; simple and honest).
- **The window being minimized/hidden**: bubble/halo are `Qt.Tool` children of the window —
  Qt hides them with the parent automatically on minimize; no code needed (verify manually).

**Soft input constraint** (only when `step.constrain`): in the app event filter, for
`QEvent.MouseButtonPress`/`MouseButtonDblClick` events whose receiver widget is NOT (a descendant
of the target, the bubble, the halo, a `QMenu`/`QMessageBox`/`QDialog`, or the window's menubar):
swallow (`return True`) and flash the halo (restart its pulse at full alpha). Menubar and dialogs
stay allowed so Cancel/Quit/native behavior is never trapped. Keyboard is never constrained.

### B.5 `expect` validation (the "wrong settings" corrector)

When `_advance()` runs on a step carrying `expect`, compare each key's widget value: combo
`currentText()`, spinbox `value()`, checkbox `isChecked()`, `QDoubleSpinBox` with
`abs(a-b) < 1e-9`. On mismatch: do NOT advance; call `bubble.show_correction(lines)` — the
bubble body is temporarily replaced by "Before continuing, fix these settings:" + one bullet per
mismatch ("Sections: 40 → set to 80") in `WARNING` color, with a "Re-check" button in place of
Next; Re-check re-runs the comparison.

Placement rule: **`expect` only ever appears on "next"-advanced steps** (enforce with an assert
in `TourController.__init__`). A signal-advanced "click Run" step cannot validate settings —
by the time its signal fires the engine has already run with whatever was set — so the demo
scripts put `expect` on the "review your settings" step immediately BEFORE the run step, and the
run step's bubble text additionally states the exact settings it expects.

### B.6 Phase B verification

- `tests/gui/test_tour.py` (offscreen, pytest-qt):
  - `place_near` side selection: target rect at screen right edge → bubble lands left; centered
    target → right; tiny screen → centered fallback. (Pure-geometry, call the method directly.)
  - Controller with 3 fake steps on a real `MainWindow`: starts, Next advances, Back returns,
    Esc finishes, `finished` emitted exactly once.
  - Signal advance: a step with `advance="analyze_done"`; emit `window.analyze_finished`
    manually; assert advanced.
  - `expect` mismatch: set `sections_spin` to 40, step expects 80 → not advanced, correction
    text present; set 80, Re-check → advanced.
  - Constraint filter: synthesize a `QMouseEvent` press on `window.run_btn` vs on
    `window.outline` during a `constrain=True` step targeting `run_btn`; assert the filter
    returns False/True respectively (call `controller.eventFilter(receiver, event)` directly —
    do not try to deliver real events offscreen).
  - App event filter is REMOVED after finish (`QApplication` event dispatch unaffected — assert
    via a flag or `finished` + a subsequent synthetic event returning False).
- Screenshot pass: `out/tour_review/` — grab the bubble widget itself (`bubble.grab().save()`)
  in all four pointer orientations + the correction state + the waiting state, and READ them:
  triangle actually touches the rounded rect edge, no clipped text, counter aligned. (Top-level
  translucent widgets DO render via `grab()` offscreen; the translucent background will show as
  black in the PNG where alpha=0 — acceptable for review, note it in the commit message.)
- **Real-screen check that CANNOT be done offscreen** (flag to Brady, do not claim verified):
  stacking above the native GL viewport, translucency, and the halo pulse need one manual run on
  the Mac (`python -m app`, start any demo). List this in the PR/commit notes explicitly.

---

## Phase C — The 13 guided demos

### C.1 Demo catalog (`app/demos.py::DEMOS`)

```python
@dataclass(frozen=True)
class Demo:
    milestone: str          # "M1".."M13"
    name: str               # descriptive burnback-type name (below)
    blurb: str              # one sentence for the picker row
    args: dict              # canonical run settings — MUST match HANDOFF.md §3.2 exactly
    heavy: bool = False     # True => generation confirmation dialog (M9, M13; M13 especially)
    needs_skimage: bool = False  # M9, M13 (marching-cubes generators import scikit-image)
    steps: tuple[TourStep, ...] = ()   # imported from app.demo_scripts
```

Names + args (args verified against HANDOFF.md §3.2 "Exact commands" — the implementation MUST
copy these literally; a `tests/gui/test_demos.py` assertion pins them):

| # | Name | args (axis, units, sections, chord_tol, adaptive) |
|---|---|---|
| M1 | Simple Tube Grain | z, mm, 40, 0.5, off |
| M2 | Domed Capsule, Straight Bore | z, mm, 40, 0.5, off |
| M3 | Six-Point Star Bore | z, mm, 60, 0.5, off |
| M4 | Finocyl — Bore-to-Fin Transition | z, mm, 80, 0.5, off |
| M5 | Domed Finocyl, Adaptive Stations | z, mm, 40, 0.5, ON |
| M6 | Tapered Star (Lofted Profiles) | z, mm, 60, 0.5, off |
| M7 | Central Bore + Six Satellite Perforations | z, mm, 60, 0.5, off |
| M8 | Mid-Burn Slotted Grain | z, mm, 80, 0.5, ON |
| M9 | Noisy Scan Input — Reading a "Failed" Check | z, mm, 80, 5.0, ON |
| M10 | Tiny, Tilted, and in Inches — Frame Auto-Detection | auto, in, 80, 0.0125, ON |
| M11 | Three-Segment BATES (Multi-Solid Output) | z, mm, 40, 0.5, off |
| M12 | Near-Burnout — Slots Breaking Through the Dome | z, mm, 120, 0.5, ON |
| M13 | Dirty Real-World Capstone (5M Triangles, Inches) | auto, in, 120, 8.0, ON |

`heavy`: M9 (44 MB STL, ~a minute to generate), M13 (**253 MB STL, several minutes to generate,
Analyze alone can run ~19 minutes** — the picker's confirm dialog for M13 must say all three
numbers plainly). `needs_skimage`: M9, M13 — check `importlib.util.find_spec("skimage")` before
generating; if missing, a message box quoting the exact pip command
(`.venv/bin/pip install scikit-image`) and abort.

### C.2 Mesh provisioning (`app/demos.py`)

```python
def demo_stl_path(milestone) -> str:   # os.path.join("harness", "truth", f"{milestone}.stl")
                                        # RELATIVE path, matching app/smoke.py's precedent —
                                        # the app is documented as run-from-repo-root
                                        # (WORK_SETUP §6).

class DemoMeshWorker(QObject):          # app/worker.py idiom exactly: finished/failed signals,
    finished = Signal(str)              # run() calls harness.generators.make(milestone)
    failed = Signal(str)                # (which no-ops fast when the cached truth exists —
                                        # generators.make checks a spec-hash cache itself),
                                        # then emits the stl path.
```

Run it via the existing `app/worker.py::run_in_thread`, and **append worker+thread to
`window._workers` / `window._threads`** — the GC hang documented at `main_window.py` line ~111
("a worker with no persistent Python reference can be garbage-collected...") applies to any new
worker; do not relearn it.

While generating: status label "Preparing demo geometry (Mx)…", `window.spinner.start()`,
picker's Start button disabled. Generation happens BEFORE the tour starts; the tour's first step
only shows once the STL exists and has been loaded into the input preview.

Concurrency note (document in a comment, no code): `harness/score.py` renames `harness/truth/`
to `.truth_hidden_*` during scoring runs; a demo generated while the autonomous loop is scoring
would race it. Not a real end-user scenario (the loop is the dev machine only); out of scope.

### C.3 `DemoPickerDialog(QDialog)` (`app/demos.py`)

- Opened from **Help → Guided Demos…** (menu wiring in `_build_menu_and_toolbar`, Help block).
- Modal-less like HelpDialog; 560×620. A `QListWidget` (or QTreeWidget, 13 top-level rows) —
  each row: "M4 — Finocyl — Bore-to-Fin Transition" bold, blurb second line `TEXT_SECONDARY`,
  and for heavy rows a right-aligned badge "generates ~253 MB, several min" in `WARNING`.
  Double-click or a "Start demo" primary button starts the selected demo.
- Start flow (`MainWindow.start_demo(milestone)` — put the method on MainWindow, the picker just
  calls it, so tests and `--smoke-tour` can drive demos without the dialog):
  1. If a run is in flight (`cancel_btn.isEnabled()`), refuse with a message box.
  2. Heavy confirm (M9/M13) if `not os.path.exists(demo_stl_path)`; skimage check (C.1).
  3. Close the picker. Kick `DemoMeshWorker`.
  4. On `finished(stl_path)`: `self._set_path_field(self.input_path_edit, stl_path)`;
     `self._load_input_preview(stl_path)`; output path →
     `out/demos/{milestone}_rebuilt.step` (set via `_set_path_field(self.output_path_edit, …)`;
     `out/` is gitignored); **reset option widgets to app defaults** (axis auto, units mm,
     sections 40, chord auto ON, adaptive off) so the tour's teaching steps start from a known
     state; then build `TourController(self, DEMOS[m].steps, DEMOS[m].name)`, keep it as
     `self._tour`, connect its `finished` to clearing `self._tour`, `start()`.
  5. On `failed(msg)`: log line level="error" + message box.

### C.4 The standard demo script shape (`app/demo_scripts.py`)

This module is text-heavy and boilerplate-light: a helper
`def _config_steps(axis, units, sections, chord_tol, adaptive) -> list[TourStep]` emits the
shared settings walkthrough (target each widget in turn: `axis_combo`, `units_combo`,
`sections_spin`, `chord_tol_auto`/`chord_tol_spin`, `adaptive_check`), ending with a
review step carrying `expect={...}` (per §B.5 `expect` lives only on next-steps). A 14th demo is
added by copying one `DEMO_M*_STEPS` list and a catalog row — that is the whole extension story;
say so in a module docstring addressed to future non-expert editors.

Canonical per-demo skeleton (~9–13 steps):
1. (target=None) Welcome: what this burnback geometry IS (one paragraph from the MISSION §6
   description — e.g. M7: "a central bore plus six satellite perforations that dead-end at a
   flat wall — watch for the topology event where those six chains die"). advance=next.
2. (target="input_path_edit") "The input STL is already loaded — this file was generated
   locally by the test-geometry generators; nothing shipped in the repo." next.
3–7. `_config_steps(...)` — each step names the exact value and WHY this milestone wants it
   (e.g. M8: "80 sections + adaptive: this grain's slots have filleted end edges the adaptive
   pass clusters stations around"). Last config step carries `expect`.
8. (target="analyze_btn", advance="analyze_done", constrain=True) "Click Analyze." Bubble's
   waiting hint: "→ click Analyze to continue". Next step's body interprets the Detected page
   (on_enter="select_outline_detected").
9. (target="run_btn", advance="rebuild_done", constrain=True) "Click Run." (For demos whose
   chord-tol step unchecked auto, Analyze already ran; Run goes straight to rebuild.)
10. (target="manifest_tree", on_enter="select_outline_output") Read the verification rows —
   per-demo text (M11: "Bodies: 3 expected vs 3 — one STL became three solids").
11. (target="viewport") Milestone-specific viewing instruction driving a REAL view feature —
    this is where the view-options teaching lives, varied per demo so the 13 demos collectively
    cover every View feature: M3 star bore → Section view (S) + slider; M4/M5 → Swap (B) and
    "Rebuilt solid: transparent"; M6 → orthographic + look down the axis to see the star grow;
    M8 → Deviation heatmap; M2 → dome-cap layer toggle + Stations table row selection
    (on_enter="select_outline_stations"); M12 → Section view at the breakthrough z; M7/M11 →
    station rings + topology-event rings; M10/M13 → telemetry line + Detected page (units/axis
    normalization); M1 → render modes (Ctrl+1/2/3) as the gentlest intro. advance=next or the
    matching toggle signal key (swap_toggled/section_toggled/deviation_toggled) with
    constrain=False.
12. (target=None) Wrap-up + "Try the next demo from Help → Guided Demos."

### C.5 The M9 known-failure demo (the one Brady called out — get this exactly right)

The real lesson (verified against `pipeline/engine.py::_axial_bounds_hint`'s docstring, its hint
strings, and CLAUDE.md's 2026-09-08 entry): at M9's own official, historically-validated settings
(`--axis z --sections 80 --adaptive --chord-tol 5`) the **Bounds Z check fails** — the solid
stops ~21 mm short of the input's aft-dome tip — and **no chord-tol/adaptive combination fixes
it** (3.5 crashed, 4.25 / 4.93-auto / adaptive-off all left the shortfall unchanged), because
M9's input is a genuinely noisy, coarse marching-cubes mesh (median edge 40 mm) whose true
surface position at the single most-extreme apex point carries real uncertainty at that scale.
Meanwhile **Deviation — the robust p95 whole-surface statistic — passes comfortably (2.29 mm
against a 10 mm gate)**, which is the actual evidence the reconstruction is right. The check is
informational; the engine's own hint now says exactly this. The demo must teach THE SAME lesson
in the same language — do not invent a different failure or imply a fix exists.

Script (M9-specific steps, replacing the generic 9–11):
- Welcome step frames it honestly: "This demo runs a milestone at its official settings and
  ends with a FAILED check on purpose. The point is learning to read a failure."
- Config steps set the official args, INCLUDING unchecking "auto from mesh" and typing 5.0 —
  with a step explaining why a scan-like mesh gets a coarse chord-tol (the mesh bends at every
  edge; its faithfulness is ~its voxel spacing, not sub-mm).
- Run step: `advance="rebuild_done"`. **Branching**: `TourController` stores the bool payload of
  `rebuild_finished` as `self._last_run_ok`. `TourStep` gets one more optional field:
  `branch: tuple[int, int] | None = None` — `(next_if_ok, next_if_failed_verification)` as
  ABSOLUTE step indices; when set, `_advance()` uses it instead of `i+1`. M9's run step branches
  to (a rarely-taken "huh, it passed on your machine — the input is noise-seeded, tolerances can
  land either side; here's what the failure normally looks like" step, then wrap-up) vs (the
  main teaching sequence). Every other demo leaves `branch=None`. This is the ONLY branching
  mechanism — do not generalize further.
- Teaching sequence (the failure path), each `advance="next"`:
  1. target="manifest_tree", on_enter="select_outline_output": "See Bounds Z: ✗ amber, not red.
     Amber means informational — the run finished and the STEP was written."
  2. target="manifest_tree": "Read the WHAT TO DO hint under Bounds Z. It says this is very
     likely the input mesh's own noise at a single extreme tip point — NOT something chord-tol
     or adaptive will move. That hint was earned: finer chord-tol (3.5) crashes this mesh,
     coarser (4.25) and the auto value (4.93) leave the shortfall identical."
  3. target="manifest_tree": "Now read Deviation: p95 ≈ 2.3 mm against a 10 mm gate — passing
     with 4× margin. Deviation is a robust statistic over the whole surface; Bounds Z is a
     single most-extreme point. When they disagree like this, trust Deviation."
  4. target="viewport" + deviation heatmap invitation (advance="deviation_toggled"): "Turn on
     the Deviation heatmap and look at the aft dome tip — the disagreement is a tiny local spot
     on a surface that is otherwise well under tolerance."
  5. Wrap-up: "Rule of thumb: a failed check + a passing Deviation + an honest hint = read
     before re-running. Not every ✗ is yours to fix."
- **Numbers in the bubble text**: phrase as "≈ 21 mm" / "≈ 2.3 mm" ("on this machine the exact
  values may differ slightly") — the mesh is seeded (seed 7 per MISSION) so they should
  reproduce, but do not promise exactness.

### C.6 Smoke/screenshot entry point

`app/smoke.py`: add `run_tour_smoke(outdir) -> int` (do NOT touch `run_smoke`):
offscreen `MainWindow`, then grab+save: `help_dialog.png` (HelpDialog on its quick-start page),
`demo_picker.png` (DemoPickerDialog), `bubble_right.png`/`bubble_waiting.png`/
`bubble_correction.png` (a CoachmarkBubble driven directly), and `m2_demo_mid.png` — start the
**M2** demo for real (M2 generates in seconds and is the existing smoke milestone), programmatically
walk the controller: call `controller._advance()` through the config steps after setting the
widgets, emit the analyze/rebuild flows via the same synchronous worker helpers `run_smoke`
already uses, and grab `window` + bubble at the read-the-verification step. Write
`tour_smoke.json` `{"ok": true, "screenshots": [...]}` mirroring the existing contract. Wire
`python -m app --smoke-tour <outdir>` in `app/__main__.py` beside `--smoke`.

### C.7 Phase C verification

- `tests/gui/test_demos.py`:
  - Exactly 13 demos, milestones M1..M13 unique, names non-empty and none equal to bare "Demo N".
  - **Args pinned literally**: a hardcoded expected dict-of-dicts copied from the table in C.1;
    assert equality. (Guards against drift from HANDOFF's official commands.)
  - Every step of every demo: `_resolve_target(window, step.target)` succeeds (offscreen
    MainWindow); every `advance` key ∈ SIGNAL_KEYS ∪ {"next"}; `expect` only on "next" steps;
    every `on_enter` ∈ the four allowed values; `branch` only on M9 and indices in range.
  - M9's script contains the strings "Deviation", "informational", and "chord-tol" (lesson
    canary); M9 `heavy` and `needs_skimage` True; M13 both True.
  - `start_demo` refuses while `cancel_btn` enabled.
- `run_tour_smoke` in CI-style: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m app --smoke-tour
  out/tour_smoke` exits 0; then READ the PNGs (this project's convention — the reviewer looks at
  pixels, comments in code are not evidence): bubble anchored sensibly, text unclipped, picker
  rows legible, dark theme consistent.
- Manual (flag as requiring Brady/a real screen): run the M9 demo end-to-end once on the Mac —
  generation, the real failure, the branch. ~2–4 min runtime for the rebuild at M9 scale.

---

## Phase D — No-install, no-admin, double-click launchers

### D.1 `scripts/create_desktop_shortcut.py`

Standalone (`.venv/bin/python scripts/create_desktop_shortcut.py [--desktop-dir DIR] [--repo DIR]`)
AND importable (`create_shortcuts(repo_root, desktop_dir=None) -> list[str]` returning created
paths). `--desktop-dir` exists for tests (write to tmp, never the real Desktop in CI). Repo root
default: `Path(__file__).resolve().parent.parent`. Venv python resolved as
`repo/.venv/bin/python` (mac) / `repo\.venv\Scripts\pythonw.exe` (win); **error out with a plain
message if missing** ("create the venv first — see WORK_SETUP.md §3") rather than falling back
to system python.

All writes follow the delete-first rule: `shutil.rmtree(app_bundle, ignore_errors=True)` /
`Path.unlink(missing_ok=True)` before each artifact is written.

**macOS — primary: a minimal hand-written `.app` bundle** at `~/Desktop/STL Rebuilder.app`:

```
STL Rebuilder.app/Contents/Info.plist      # CFBundleName "STL Rebuilder",
                                           # CFBundleExecutable "launch",
                                           # CFBundleIdentifier "local.stl-rebuilder.launcher",
                                           # CFBundlePackageType "APPL",
                                           # LSMinimumSystemVersion "11.0"
STL Rebuilder.app/Contents/MacOS/launch    # exec bit set (chmod 0o755):
    #!/bin/zsh
    cd "<ABS_REPO_PATH>" || exit 1
    exec "<ABS_REPO_PATH>/.venv/bin/python" -m app
```

Paths are absolute and baked in at generation time (that is fine — the generator is rerun if the
repo moves; say so in the completion dialog). No icon file: the generic app icon is acceptable;
a custom `.icns` is a nice-to-have explicitly cut (creating one without admin/tools means
scripting `iconutil`, and icon caching makes iteration miserable — not worth it now).

Why `.app` over bare `.command` as the primary: a `.command` double-click opens a Terminal
window that persists for the whole app session and stays behind after quit — exactly the
"opening terminal" feel Brady wants to avoid. The 3-file bundle costs ~40 lines of generator and
gives a real Dock presence with no window. **Both** are generated, though: the `.command`
fallback at `launchers/stl-rebuilder.command` (same two lines plus `#!/bin/zsh`) is the
debugging path — it shows the console output the .app swallows. Gatekeeper: both are unsigned;
first launch of the `.app` needs **right-click → Open → Open** once (a plain double-click shows
"cannot verify the developer"). The generator's completion message AND the manual's
troubleshooting page must state this one-time step. No admin rights are involved at any point.

**Windows** (generated but NOT executed on macOS):
- `launchers/stl-rebuilder.bat`: `@echo off`, `cd /d "<REPO>"`,
  `".venv\Scripts\python.exe" -m app`, `pause` on nonzero exit — the console-visible debug path.
- Desktop `.lnk` via PowerShell (no pywin32 dependency):
  ```
  powershell -NoProfile -Command "$d=[Environment]::GetFolderPath('Desktop');
    $s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\\STL Rebuilder.lnk\");
    $s.TargetPath='<REPO>\.venv\Scripts\pythonw.exe'; $s.Arguments='-m app';
    $s.WorkingDirectory='<REPO>'; $s.Save()"
  ```
  invoked with `subprocess.run([...])` only when `sys.platform == "win32"`. `pythonw.exe` = no
  console window (already the documented pattern in WORK_SETUP §6 — this automates it).
  `GetFolderPath('Desktop')` is the load-bearing choice: corporate machines commonly redirect
  Desktop into OneDrive, where `%USERPROFILE%\Desktop` is wrong.

**Repo hygiene**: add `launchers/` to `.gitignore` (generated artifacts; the generator is the
source of truth). Update `WORK_SETUP.md` §6's shortcut paragraph to point at
`Help → Create Desktop Shortcut` / the script.

### D.2 In-app menu action

`Help → Create Desktop Shortcut…` → `MainWindow._create_desktop_shortcut()`:
`from scripts.create_desktop_shortcut import create_shortcuts` (works because the app runs from
the repo root — same precondition as everything else; wrap the import in the method, not module
top-level, so `scripts/` never becomes an app-startup dependency), run it, then a
`QMessageBox.information` listing the created paths and, on macOS, the one-time
right-click → Open instruction. Errors → `QMessageBox.warning` with the exception text and a
pointer to the script for manual runs. No worker thread needed (sub-second file writes).

### D.3 Phase D verification

- `tests/test_shortcuts.py` (pure filesystem, no Qt): run `create_shortcuts(repo, desktop_dir=tmp)`
  with a fake repo tree (make `.venv/bin/python` exist as an empty file); assert on macOS-mode
  output: bundle structure exists, `launch` is executable (`os.access(X_OK)`), plist parses via
  `plistlib` and `CFBundleExecutable == "launch"`, script contains the absolute repo path;
  `.command` executable and contains `-m app`; `.bat` contains `python.exe` (console/debug
  path), and the `.lnk` PowerShell command string contains `pythonw.exe` — expose that command
  string as a module-level template constant so the test can assert on it without running
  PowerShell. Rerun-idempotence: call twice, second call succeeds (delete-first).
- Manual on the Mac (Brady or the implementer with a screen): double-click the `.app` once via
  right-click → Open; confirm the GUI launches with `harness`/`out` paths resolving (i.e. cwd is
  the repo).
- **Windows verification is impossible from this machine** — see Risks.

---

## Risks / effort flags (do not silently absorb these)

1. **Windows is designed-but-unverified.** The `.bat`/`.lnk`/pythonw path and the coachmark
   stacking over the GL viewport on Windows cannot be tested on this Mac. Ship them clearly
   labeled "untested — verify per WORK_SETUP on first work-machine run" in WORK_SETUP.md, and
   keep the PowerShell command in one template constant so a work-machine fix is a one-liner.
2. **Coachmark visuals need one real-screen pass.** Offscreen tests + `grab()` PNGs verify
   geometry and content, not translucency/stacking/pulse over the native GL surface (the
   camera-orientation widget already has exactly this caveat in `viewport.py` — same situation).
   Budget a Brady live-test round; Round-2-style feedback ("bubble covers the thing it points
   at" class of bugs) is likely and cheap to fix after one screenshot from him.
3. **M13 demo is a 20+ minute experience** (minutes of generation + ~19 min Analyze). The
   confirm dialog must set expectations; consider (already decided: yes) the M13 welcome step
   saying "start this one before lunch." Do not quietly drop M13 — Brady asked for all 13.
4. **Manual prose volume.** Phase A is mostly writing (~11 pages). The inventory in §A.1 is
   complete; the writing is bounded but is the single biggest time block in this plan.
5. **`rebuild_finished(bool)` verification-payload coupling**: `_verification_all_passed` must
   tolerate a missing/None `verification` block (treat as ok=True — absence of checks is not
   failure) or M-demos on odd machines will branch wrong.
6. **Text-content drift**: manual + demo scripts quote real behaviors (tolerances, hint wording).
   The canary-string tests catch deletions, not wording drift. Acceptable; noted.

## Things Brady should personally sign off on before/while implementation runs

- **The 13 demo names** (§C.1 table) — taste call; they aim for descriptive-of-burnback-type per
  his ask, but he may want e.g. real motor-design vocabulary tweaks.
- **The `.app`-with-generic-icon decision** — if he wants a real icon on the Desktop, that is a
  small follow-up (`iconutil` + an `.icns`), just not free.
- **Soft input constraint** (§0/B.4) — during action steps, clicks outside the highlighted
  control are swallowed (menus/dialogs exempt). If he'd rather the app stay fully usable
  mid-tour, flip `constrain=False` everywhere; the mechanism is per-step either way.
- **Bubble accent color** — spec says TELEMETRY cyan (the "live data" token) for border/halo;
  if he expected ACCENT blue, it's a two-constant change.
