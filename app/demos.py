"""The thirteen guided demos: what they are, how their mesh gets made, the picker that lists
them, and the glue that hands one to `app.tour.TourController`.

The scripts themselves (the bubble text) live in `app/demo_scripts.py` -- this module is the
catalog and the machinery. `args` here is not decoration: every entry reproduces the milestone's
own official command from `HANDOFF.md` §3.2, the settings it was actually validated at, and
`tests/gui/test_demos.py` pins them literally so a demo can never quietly teach a run nobody ever
scored.

The repository ships the GENERATORS, not the meshes, so starting a demo may have to build its STL
first (`harness.generators.make`, which no-ops fast against its own spec-hash cache when the truth
is already on disk)."""
import importlib.util
import os
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from app import demo_scripts
from app.theme import ACCENT, BG_PANEL, BG_RAISED, TEXT_SECONDARY, WARNING
from app.worker import run_in_thread

TRUTH_DIR = os.path.join("harness", "truth")


@dataclass(frozen=True)
class Demo:
    """One catalog entry. `args` mirrors the milestone's official `rebuild.py` command
    (HANDOFF §3.2) as the five settings the GUI exposes for it."""

    milestone: str
    name: str
    blurb: str
    args: dict
    heavy: bool = False
    needs_skimage: bool = False
    steps: tuple = field(default=())


def demo_stl_path(milestone: str) -> str:
    """Where this demo's input mesh lives, RELATIVE to the working directory -- the same
    convention `app/smoke.py` already uses, because the app is documented as run from the
    repository root (WORK_SETUP.md §6)."""
    return os.path.join(TRUTH_DIR, f"{milestone}.stl")


def _args(axis, units, sections, chord_tol, adaptive) -> dict:
    return {"axis": axis, "units": units, "sections": sections, "chord_tol": chord_tol,
            "adaptive": adaptive}


# Ordered M1..M13. Adding a fourteenth demo is: one row here, one `DEMO_M*_STEPS` list in
# app/demo_scripts.py. Nothing else.
DEMOS = {
    "M1": Demo(
        "M1", "Simple Tube Grain",
        "An annular tube with flat ends — the whole workflow at its simplest.",
        _args("z", "mm", 40, 0.5, False), steps=demo_scripts.DEMO_M1_STEPS),
    "M2": Demo(
        "M2", "Domed Capsule, Straight Bore",
        "2:1 ellipsoidal domes both ends, straight bore through — domes and the station table.",
        _args("z", "mm", 40, 0.5, False), steps=demo_scripts.DEMO_M2_STEPS),
    "M3": Demo(
        "M3", "Six-Point Star Bore",
        "A filleted six-point star bore, read with Section view.",
        _args("z", "mm", 60, 0.5, False), steps=demo_scripts.DEMO_M3_STEPS),
    "M4": Demo(
        "M4", "Finocyl — Bore-to-Fin Transition",
        "Eight fin slots aft of a flat wall: a real topology event, and A/B against the input.",
        _args("z", "mm", 80, 0.5, False), steps=demo_scripts.DEMO_M4_STEPS),
    "M5": Demo(
        "M5", "Domed Finocyl, Adaptive Stations",
        "Domes plus fins — where adaptive station placement earns its keep.",
        _args("z", "mm", 40, 0.5, True), steps=demo_scripts.DEMO_M5_STEPS),
    "M6": Demo(
        "M6", "Tapered Star (Lofted Profiles)",
        "A star bore that grows 1.5x along the axis — the lofted, not revolved, path.",
        _args("z", "mm", 60, 0.5, False), steps=demo_scripts.DEMO_M6_STEPS),
    "M7": Demo(
        "M7", "Central Bore + Six Satellite Perforations",
        "Six perforations that dead-end at a flat wall — many loops per station, and chains "
        "that die.",
        _args("z", "mm", 60, 0.5, False), steps=demo_scripts.DEMO_M7_STEPS),
    "M8": Demo(
        "M8", "Mid-Burn Slotted Grain",
        "Eight obround slots with filleted end edges — adaptive stations and the deviation "
        "heatmap.",
        _args("z", "mm", 80, 0.5, True), steps=demo_scripts.DEMO_M8_STEPS),
    "M9": Demo(
        "M9", 'Noisy Scan Input — Reading a "Failed" Check',
        "A marching-cubes scan of M8 that ends on a FAILED check, on purpose. The one to do "
        "second.",
        _args("z", "mm", 80, 5.0, True), heavy=True, needs_skimage=True,
        steps=demo_scripts.DEMO_M9_STEPS),
    "M10": Demo(
        "M10", "Tiny, Tilted, and in Inches — Frame Auto-Detection",
        "The same grain at 1/40 scale, axis along +X, written in inches — axis and unit "
        "normalisation.",
        _args("auto", "in", 80, 0.0125, True), steps=demo_scripts.DEMO_M10_STEPS),
    "M11": Demo(
        "M11", "Three-Segment BATES (Multi-Solid Output)",
        "One STL that is really three separate grain segments — and becomes three solids.",
        _args("z", "mm", 40, 0.5, False), steps=demo_scripts.DEMO_M11_STEPS),
    "M12": Demo(
        "M12", "Near-Burnout — Slots Breaking Through the Dome",
        "Thin webs and slots that open into the aft dome — the hardest topology the engine "
        "handles.",
        _args("z", "mm", 120, 0.5, True), steps=demo_scripts.DEMO_M12_STEPS),
    "M13": Demo(
        "M13", "Dirty Real-World Capstone (5M Triangles, Inches)",
        "Five million unwelded, noisy, flipped-facet triangles with junk islands, in inches. "
        "Start it before lunch.",
        _args("auto", "in", 120, 8.0, True), heavy=True, needs_skimage=True,
        steps=demo_scripts.DEMO_M13_STEPS),
}

# What the heavy-milestone confirmation says, per §C.1: all three numbers plainly, no euphemism.
HEAVY_WARNING = {
    "M9": ("M9's input mesh has to be generated first: about 44 MB of STL, roughly a minute of "
           "work. It is written to harness/truth/ and reused by every later run.\n\n"
           "Generate it now?"),
    "M13": ("M13's input mesh has to be generated first: about 253 MB of STL, several minutes to "
            "generate — and the Analyze step alone can then run about 19 minutes.\n\n"
            "It is written to harness/truth/ and reused by every later run. Start it now?"),
}
# Short form for the picker's right-hand badge.
HEAVY_BADGE = {
    "M9": "generates ~44 MB, ~1 min",
    "M13": "generates ~253 MB, several min",
}


class DemoMeshWorker(QObject):
    """Builds a demo's truth mesh off the UI thread.

    `generators.make` checks its own spec-hash cache first, so this is near-instant for a
    milestone already generated -- which is why it runs unconditionally rather than being skipped
    when the STL exists: a truth file left over from an older spec would otherwise be handed to
    the demo as if it matched.

    Concurrency note, documented rather than coded around: `harness/score.py` renames
    `harness/truth/` to `.truth_hidden_*` for the duration of a scoring run, so generating a demo
    mesh while the autonomous loop is scoring would race it. That is a dev-machine-only overlap,
    not an end-user scenario, and is out of scope here."""

    finished = Signal(str)   # path to the demo's input STL
    failed = Signal(str)     # message

    def __init__(self, milestone: str):
        super().__init__()
        self.milestone = milestone

    def run(self):
        try:
            from harness import generators
            generators.make(self.milestone)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        path = demo_stl_path(self.milestone)
        if not os.path.exists(path):
            self.failed.emit(f"the generator ran but {path} is not there")
            return
        self.finished.emit(path)


def skimage_available() -> bool:
    """M9 and M13's inputs come out of `skimage.measure.marching_cubes`; the rest are analytic."""
    return importlib.util.find_spec("skimage") is not None


SKIMAGE_MESSAGE = (
    "This demo's input mesh is generated by marching cubes, which needs scikit-image.\n\n"
    "Install it once, from the repository folder:\n\n"
    "    .venv/bin/pip install scikit-image\n\n"
    "then start the demo again. The other eleven demos do not need it.")


class _DemoRow(QWidget):
    """One picker row: milestone + name in bold, blurb beneath it, and -- for the two slow ones
    -- a right-aligned cost badge, so the warning is visible BEFORE the row is chosen rather than
    only in the confirmation that follows it."""

    MARGIN_H = 8
    MARGIN_V = 6
    SPACING = 10

    def __init__(self, demo: Demo, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(self.MARGIN_H, self.MARGIN_V, self.MARGIN_H, self.MARGIN_V)
        layout.setSpacing(self.SPACING)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self.title = QLabel(f"{demo.milestone} — {demo.name}")
        # Wrapped, not elided: M13's name plus its badge is wider than the dialog, and a fixed
        # width without wrapping simply cut the title off mid-word ("...(5M Triangles, I").
        self.title.setWordWrap(True)
        title_font = self.title.font()
        title_font.setBold(True)
        self.title.setFont(title_font)
        self.blurb = QLabel(demo.blurb)
        self.blurb.setWordWrap(True)
        self.blurb.setStyleSheet(f"color: {TEXT_SECONDARY};")
        text.addWidget(self.title)
        text.addWidget(self.blurb)
        layout.addLayout(text, 1)

        self.badge = None
        badge_text = HEAVY_BADGE.get(demo.milestone) if demo.heavy else None
        if badge_text:
            self.badge = QLabel(badge_text)
            self.badge.setStyleSheet(f"color: {WARNING};")
            self.badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(self.badge, 0)

    def fit_to_width(self, width: int) -> int:
        """Lay the row out at `width` and return the height it then needs.

        A word-wrapped QLabel reports its UNWRAPPED width as its size hint, so neither the row's
        own `sizeHint()` nor `QListWidget`'s default item hint knows how tall a wrapped blurb
        really is -- which is what left every blurb clipped to one truncated line in the first
        screenshot pass. The height has to come from `heightForWidth` at the width the label will
        actually be given. Same lesson, same fix, as `CoachmarkBubble._refit`."""
        self.setFixedWidth(width)
        text_width = width - 2 * self.MARGIN_H
        if self.badge is not None:
            text_width -= self.badge.sizeHint().width() + self.SPACING
        text_width = max(120, text_width)
        self.blurb.setFixedWidth(text_width)
        self.blurb.setFixedHeight(max(1, self.blurb.heightForWidth(text_width)))
        self.title.setFixedWidth(text_width)
        self.title.setFixedHeight(max(1, self.title.heightForWidth(text_width)))
        return (self.title.height() + self.blurb.height() + 2 * self.MARGIN_V + 4)


class DemoPickerDialog(QDialog):
    """The list of thirteen. Non-modal and parented to the main window like `HelpDialog`, and it
    closes itself the moment a demo starts -- the tour needs the window it is pointing at."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.setWindowTitle("Guided Demos")
        self.resize(560, 620)

        heading = QLabel(
            "Each demo runs one of the thirteen validated test motors, for real, at the settings "
            "it was validated at. Nothing is simulated.")
        heading.setWordWrap(True)
        heading.setStyleSheet(f"color: {TEXT_SECONDARY};")

        self.list = QListWidget()
        # NOT alternating row colors: the palette's alternate-base is a near-white band the dark
        # theme never restyles, so every second row rendered as white with a dark bold title on
        # it -- unreadable, and caught only in the screenshot pass (2026-09-08). The rows are
        # already separated by their own two-line shape.
        self.list.setAlternatingRowColors(False)
        # A row is an item WIDGET, whose labels carry their own fixed colors -- so the default
        # bright-blue selection highlight paints underneath text that stays dark grey, and the
        # selected row becomes the least readable one in the list (screenshot pass, 2026-09-08).
        # A raised panel tint plus an accent bar on the left reads as "selected" without fighting
        # the text it sits behind.
        self.list.setStyleSheet(
            f"QListWidget::item:selected {{ background: {BG_RAISED}; "
            f"border-left: 3px solid {ACCENT}; }}"
            f"QListWidget::item:hover {{ background: {BG_PANEL}; }}")
        for demo in DEMOS.values():
            item = QListWidgetItem(self.list)
            item.setData(Qt.ItemDataRole.UserRole, demo.milestone)
            row = _DemoRow(demo)
            self.list.setItemWidget(item, row)
        self.list.itemDoubleClicked.connect(self._start_selected)
        if self.list.count():
            self.list.setCurrentRow(0)

        self.start_btn = QPushButton("Start demo")
        self.start_btn.setObjectName("primary")
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self._start_selected)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        buttons.addWidget(self.start_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(heading)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)

    def selected_milestone(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def relayout_rows(self):
        """Re-wrap every row at the list's current width. Qt will not recompute an item widget's
        height on its own -- the item's size hint is authoritative -- so this has to run on show
        and on every resize, or a blurb silently clips."""
        width = self.list.viewport().width()
        if width <= 1:
            width = max(320, self.width() - 40)
        for i in range(self.list.count()):
            item = self.list.item(i)
            row = self.list.itemWidget(item)
            if row is None:
                continue
            item.setSizeHint(QSize(width, row.fit_to_width(width)))

    def showEvent(self, event):
        super().showEvent(event)
        self.relayout_rows()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout_rows()

    def _start_selected(self, *_args):
        milestone = self.selected_milestone()
        if milestone:
            self._window.start_demo(milestone)


def confirm_and_start(window, milestone: str) -> bool:
    """The pre-flight every demo start goes through: is a run already in flight, is scikit-image
    there, and does the user accept the generation cost. Returns False when the demo must not
    start; the caller has already been told why."""
    demo = DEMOS[milestone]
    if window.cancel_btn.isEnabled():
        QMessageBox.warning(
            window, "Guided demos",
            "A run is in flight. Wait for it to finish (or press Cancel) before starting a demo.")
        return False
    if demo.needs_skimage and not skimage_available():
        QMessageBox.warning(window, f"{milestone} — scikit-image needed", SKIMAGE_MESSAGE)
        return False
    if demo.heavy and not os.path.exists(demo_stl_path(milestone)):
        answer = QMessageBox.question(
            window, f"{milestone} — this one is slow",
            HEAVY_WARNING.get(milestone, "This demo's mesh takes a while to generate. Continue?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
    return True


def begin_mesh_generation(window, milestone: str, on_ready, on_error):
    """Kick the mesh build and return the worker. `on_ready(stl_path)` runs when it is there.

    `on_ready` / `on_error` MUST be bound methods of `window` (or of some other QObject living on
    the GUI thread), never lambdas or `functools.partial`. Qt picks a connection type from the
    RECEIVER's thread affinity, and a plain callable has none -- so it is connected directly and
    runs inside the worker thread that emitted the signal. Here that meant `TourController` and
    its bubble being constructed on the worker thread ("Cannot create children for a parent that
    is in a different thread", "Cannot filter events for objects in a different thread"), after
    which the tour's own signal connections silently never fired. Caught by the `--smoke-tour`
    screenshot pass, 2026-09-08; invisible from reading the code.

    Worker AND thread are appended to `window._workers` / `window._threads` -- see the comment on
    `MainWindow.__init__`'s `_workers`: a worker with no persistent Python reference can be
    garbage-collected between `thread.start()` and the event-loop tick that actually calls
    `run()`, which is a real hang, already found and fixed once."""
    worker = DemoMeshWorker(milestone)
    worker.finished.connect(on_ready)
    worker.failed.connect(on_error)
    thread = run_in_thread(worker)
    window._workers.append(worker)
    window._threads.append(thread)
    thread.start()
    return worker
