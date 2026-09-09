"""Main application window. MISSION §12/G2 layout: left Outline dock (Input -> Detected ->
Stations -> Output), Details dock for the selected node, central 3D viewport, bottom Log dock,
status bar with progress + cancel."""
import datetime
import html
import math
import os
import textwrap

import numpy as np
import pyqtgraph as pg
import qtawesome as qta
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QSpinBox, QStackedWidget, QTextEdit, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app import __version__, manifest as manifest_mod
from app.dashboard import Dashboard
from app.help import HelpDialog
from app.theme import (
    ACCENT, BG_DARKEST, ERROR, MONO_FAMILY, SUCCESS, TELEMETRY, TEXT_DISABLED, TEXT_SECONDARY,
    WARNING,
)
from app.viewport import EVENT_RING_COLOR, STATION_HIGHLIGHT_COLOR, STATION_RING_COLOR, Viewport
from app.widgets import BusySpinner, PropertyTree, StationTable, axis_label, fmt_bounds, fmt_num
from app.worker import AnalyzeWorker, PreviewWorker, RebuildWorker, run_in_thread
from pipeline.engine import RebuildOptions

NODE_INPUT, NODE_DETECTED, NODE_STATIONS, NODE_OUTPUT = "Input", "Detected", "Stations", "Output"

APP_TITLE = "STL Rebuilder"


def _elide_path(path: str, keep: int = 38) -> str:
    """Middle-elide a long path for a single-line display (Output-page STEP/Preview rows and log
    lines overflowed their column with no wrap or elide -- 2026-09-07 design review). The full
    path is always still available in the tooltip/log; this only shortens the visible text."""
    if not path or len(path) <= keep:
        return path or "-"
    head = keep // 2 - 2
    tail = keep - head - 1
    return f"{path[:head]}…{path[-tail:]}"


class _RadiusProfileChart(pg.PlotWidget):
    """Bore/outer-radius-vs-axial-position profile (2026-09-07 design review, V8) -- the actual
    grain-geometry picture a propulsion engineer reads, using the same report-frame z the
    Stations table already shows (so a table row and a chart position use the same numbers,
    with no separate axial-origin conversion for the user to reason about). `pyqtgraph` was
    already an installed dependency, unused anywhere in the app until this."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(BG_DARKEST)
        self.showGrid(x=True, y=True, alpha=0.15)
        self.setLabel("bottom", "z (mm, report frame)")
        self.setLabel("left", "R (mm)")
        for axis in ("bottom", "left"):
            self.getAxis(axis).setTextPen(TEXT_SECONDARY)
            self.getAxis(axis).setPen(TEXT_SECONDARY)
        self.setMaximumHeight(160)
        self.addLegend(offset=(8, 8), labelTextColor=TEXT_SECONDARY)
        self._outer_curve = self.plot([], [], pen=pg.mkPen(STATION_RING_COLOR, width=1.6), name="R_outer")
        self._bore_curve = self.plot([], [], pen=pg.mkPen(TELEMETRY, width=1.6), name="R_bore")
        self._highlight = pg.InfiniteLine(
            angle=90, pen=pg.mkPen(STATION_HIGHLIGHT_COLOR, width=1.2, style=Qt.PenStyle.DashLine))
        self._highlight.hide()
        self.addItem(self._highlight)
        self._event_lines = []

    def set_rows(self, rows):
        for line in self._event_lines:
            self.removeItem(line)
        self._event_lines = []
        zs, outers, bores = [], [], []
        for _idx, z, _n_loops, r_outer, r_bore, _cls, is_event in rows:
            zs.append(z)
            outers.append(r_outer if r_outer is not None else float("nan"))
            bores.append(r_bore if r_bore is not None else float("nan"))
            if is_event:
                line = pg.InfiniteLine(pos=z, angle=90, pen=pg.mkPen(EVENT_RING_COLOR, width=1.0))
                self.addItem(line)
                self._event_lines.append(line)
        self._outer_curve.setData(zs, outers)
        self._bore_curve.setData(zs, bores)

    def set_highlight(self, z):
        if z is None:
            self._highlight.hide()
        else:
            self._highlight.setPos(z)
            self._highlight.show()


class MainWindow(QMainWindow):
    # Run-lifecycle signals. Nothing in the window itself listens to these -- they exist so a
    # guided tour (`app/tour.py`) can wait on what the app actually DID rather than on a button
    # click, which is the difference between "you clicked Run" and "the run finished". Emitted
    # unconditionally; a listener only connects for the step that asked for one.
    analyze_finished = Signal()
    rebuild_finished = Signal(bool)   # True iff no verification check failed
    run_failed = Signal(str, str)     # kind, message
    input_loaded = Signal(str)        # path whose preview mesh just reached the viewport

    def __init__(self, offscreen: bool = False):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(qta.icon("ph.cube-bold", color=ACCENT))
        self.resize(1400, 900)

        self._analysis = None
        self._analyzed_input_path = None
        self._pending_rebuild = False
        self._result = None
        self._last_stage = None
        self._threads = []  # keep QThread refs alive until done
        # Keep every worker QObject referenced too, for its whole time in flight -- not just the
        # QThread it runs on. A worker with no persistent Python reference (and no Qt parent)
        # can be garbage-collected the moment the method that created it returns, since
        # `thread.start()` only SCHEDULES `worker.run()` for a later event-loop tick rather than
        # running it immediately -- found 2026-09-06 tracking down a real hang in Analyze/preview
        # loading under pytest-qt (confirmed with a minimal repro: identical code keeping an
        # extra local reference alive in the caller's own scope did not hang; PySide6/shiboken's
        # implicit "a live signal connection keeps the sender alive" behavior that let this go
        # unnoticed in interactive use is not something to rely on). `_start_rebuild` already
        # dodged this by storing `self._active_worker` (needed anyway, for Cancel) -- this list
        # is the same fix applied everywhere a worker+thread pair is created.
        self._workers = []
        self._help_dialog = None
        self._demo_picker = None
        self._demo_milestone = None
        self._tour = None   # the running TourController, if a guided demo is in progress

        self.viewport = Viewport(self, offscreen=offscreen)
        self.setCentralWidget(self.viewport)

        self._build_menu_and_toolbar()
        self._build_outline_dock()
        self._build_details_dock()
        self._build_log_dock()
        self._build_status_bar()

        self.outline.currentItemChanged.connect(self._on_outline_selection)
        self.outline.setCurrentItem(self.node_input)
        self.resizeDocks([self.outline_dock, self.details_dock], [220, 480], Qt.Orientation.Horizontal)

    # ---- chrome ---------------------------------------------------------
    def _build_menu_and_toolbar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        open_action = QAction(qta.icon("ph.folder-open-bold", color=TEXT_SECONDARY), "&Open STL...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._browse_input)
        self.export_image_action = QAction(
            qta.icon("ph.camera-bold", color=TEXT_SECONDARY), "&Export viewport image...", self)
        self.export_image_action.setShortcut("Ctrl+E")
        self.export_image_action.setToolTip("Save the current 3D view as a PNG (Ctrl+E)")
        self.export_image_action.triggered.connect(self._export_viewport_image)
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(open_action)
        file_menu.addAction(self.export_image_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        run_menu = menubar.addMenu("&Run")

        view_menu = menubar.addMenu("&View")
        self._layer_actions = {}
        for key, label in (("input", "Input mesh"), ("solid", "Rebuilt solid"),
                           ("stations", "Station rings"), ("domecap", "Dome-cap fit"),
                           ("events", "Topology events"), ("axis", "Axis line")):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(True)
            act.toggled.connect(lambda on, k=key: self.viewport.set_layer_visible(k, on))
            view_menu.addAction(act)
            self._layer_actions[key] = act
        # Clicking a legend row (app/viewport.py) also toggles its layer -- keep the menu's
        # checkmark in sync without re-triggering `toggled`'s own call back into
        # `set_layer_visible` (which would be a harmless but pointless second call).
        self.viewport.layer_toggled.connect(self._on_viewport_layer_toggled)
        view_menu.addSeparator()
        self.swap_action = QAction("Swap input ↔ rebuilt", self)
        self.swap_action.setCheckable(True)
        self.swap_action.setShortcut("B")
        self.swap_action.setToolTip("A/B compare: show the input mesh near-opaque, hide the rebuilt solid (B)")
        self.swap_action.toggled.connect(self.viewport.set_swap)
        view_menu.addAction(self.swap_action)
        self.solid_transparent_action = QAction("Rebuilt solid: transparent", self)
        self.solid_transparent_action.setCheckable(True)
        self.solid_transparent_action.setToolTip(
            "Ghost the rebuilt solid to compare its fin/wall geometry against the input mesh")
        self.solid_transparent_action.toggled.connect(self.viewport.set_solid_transparent)
        view_menu.addAction(self.solid_transparent_action)
        self.input_opaque_action = QAction("Input mesh: solid color", self)
        self.input_opaque_action.setCheckable(True)
        self.input_opaque_action.setToolTip(
            "Show the input mesh at full opacity to compare its outer boundary against the "
            "rebuilt solid")
        self.input_opaque_action.toggled.connect(self.viewport.set_input_opaque)
        view_menu.addAction(self.input_opaque_action)
        self.input_edges_action = QAction("Input mesh: show triangulation", self)
        self.input_edges_action.setCheckable(True)
        self.input_edges_action.setToolTip(
            "Draw the input STL's real facet edges -- the triangles ARE the data here (mesh "
            "density, faceting quality), unlike the rebuilt solid's display tessellation. "
            "Best combined with 'Input mesh: solid color'; on the translucent ghost the edges "
            "are faint by nature (they share the mesh's own opacity)")
        self.input_edges_action.toggled.connect(self.viewport.set_input_edges)
        view_menu.addAction(self.input_edges_action)
        # The legend rows' ghost buttons (app/viewport.py, 2026-09-08) can also flip the two
        # opacity modes -- keep these menu checkmarks synced to the Viewport, which is the
        # single source of truth (same pattern as section/deviation below).
        self.viewport.solid_transparent_toggled.connect(
            lambda on: self._sync_action_checked(self.solid_transparent_action, on))
        self.viewport.input_opaque_toggled.connect(
            lambda on: self._sync_action_checked(self.input_opaque_action, on))
        self.viewport.input_edges_toggled.connect(
            lambda on: self._sync_action_checked(self.input_edges_action, on))

        view_menu.addSeparator()
        self.fit_view_action = QAction("Fit view", self)
        self.fit_view_action.setShortcut("F")
        self.fit_view_action.setToolTip("Re-frame the camera on the current scene (F)")
        self.fit_view_action.triggered.connect(self.viewport.fit_view)
        view_menu.addAction(self.fit_view_action)
        self.ortho_action = QAction("Orthographic projection", self)
        self.ortho_action.setCheckable(True)
        self.ortho_action.setShortcut("O")
        self.ortho_action.setToolTip(
            "Parallel projection for silhouette/alignment checks -- parallel edges stay "
            "parallel regardless of depth (O)")
        self.ortho_action.toggled.connect(self.viewport.set_orthographic)
        view_menu.addAction(self.ortho_action)

        view_menu.addSeparator()
        self.section_view_action = QAction("Section view", self)
        self.section_view_action.setCheckable(True)
        self.section_view_action.setShortcut("S")
        self.section_view_action.setToolTip(
            "Clip the model along its motor axis with a draggable slider, to see the bore "
            "interior directly instead of through transparency (S)")
        self.section_view_action.toggled.connect(self.viewport.set_section_active)
        view_menu.addAction(self.section_view_action)
        self.deviation_action = QAction("Deviation heatmap", self)
        self.deviation_action.setCheckable(True)
        self.deviation_action.setToolTip(
            "Color the rebuilt solid by its measured distance to the input mesh, instead of a "
            "single p95/max scalar -- shows WHERE the reconstruction deviates")
        self.deviation_action.toggled.connect(self.viewport.set_deviation_mode)
        view_menu.addAction(self.deviation_action)
        # Both also drivable from the in-viewport display-toggle overlay (2026-09-07,
        # Fusion-360/Onshape-style canvas controls) -- Viewport is the single source of truth
        # for this state either way, so keep these QActions in sync with IT rather than the
        # other way around (same pattern as the legend's `layer_toggled` sync below).
        self.viewport.section_view_toggled.connect(
            lambda on: self._sync_action_checked(self.section_view_action, on))
        self.viewport.deviation_mode_toggled.connect(
            lambda on: self._sync_action_checked(self.deviation_action, on))

        view_menu.addSeparator()
        render_menu = view_menu.addMenu("Render mode")
        self._render_mode_actions = {}
        render_mode_tooltips = {
            # Edges only exist where the geometry genuinely has one -- a smooth revolved wall
            # (most of a burnback solid, viewed from the side) has none, so this mode can look
            # identical to Shaded from a typical view. That's correct, not a bug: check a bore,
            # rim, or slot transition instead (2026-09-07 implementation review).
            "edges": "Real geometric edges only, at rims/bores/slots -- a smooth wall has none",
        }
        for mode, label, shortcut in (
                ("shaded", "Shaded", "Ctrl+1"), ("edges", "Shaded + edges", "Ctrl+2"),
                ("wireframe", "Wireframe", "Ctrl+3")):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(mode == "shaded")
            act.setShortcut(shortcut)
            if mode in render_mode_tooltips:
                act.setToolTip(render_mode_tooltips[mode])
            act.triggered.connect(lambda checked, m=mode: checked and self.viewport.set_render_mode(m))
            render_menu.addAction(act)
            self._render_mode_actions[mode] = act
        self.viewport.render_mode_changed.connect(self._on_viewport_render_mode_changed)
        self.render_mode_cycle_action = QAction("Cycle render mode", self)
        self.render_mode_cycle_action.setShortcut("W")
        self.render_mode_cycle_action.triggered.connect(self.viewport.cycle_render_mode)
        self.addAction(self.render_mode_cycle_action)  # shortcut-only, not shown in any menu

        help_menu = menubar.addMenu("&Help")
        quick_start_action = QAction("&Quick Start Guide", self)
        quick_start_action.setShortcut("F1")
        quick_start_action.triggered.connect(lambda: self._show_help("00_quick_start"))
        help_menu.addAction(quick_start_action)
        manual_action = QAction("&Manual", self)
        manual_action.triggered.connect(lambda: self._show_help(None))
        help_menu.addAction(manual_action)
        help_menu.addSeparator()
        self.demos_action = QAction("&Guided Demos...", self)
        self.demos_action.setToolTip(
            "Walk one of the thirteen validated test motors through the real application")
        self.demos_action.triggered.connect(self._show_demos)
        help_menu.addAction(self.demos_action)
        help_menu.addSeparator()
        self.create_shortcut_action = QAction("Create &Desktop Shortcut...", self)
        self.create_shortcut_action.setToolTip(
            "Write a double-click launcher for this application to the Desktop")
        self.create_shortcut_action.triggered.connect(self._create_desktop_shortcut)
        help_menu.addAction(self.create_shortcut_action)
        help_menu.addSeparator()
        about_action = QAction(f"&About {APP_TITLE}", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        toolbar = QToolBar("Main", self)
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        # Text beside every icon (2026-09-07 design review, U1): an icon-only toolbar with four
        # cryptic glyphs and no labels tested as unclear on its own.
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.analyze_action = QAction(qta.icon("ph.magnifying-glass-bold", color=TEXT_SECONDARY), "Analyze", self)
        self.analyze_action.setShortcut("F5")
        self.analyze_action.setToolTip("Detect axis/frame/scale from the input mesh (F5)")
        self.analyze_action.triggered.connect(self.run_analyze)
        self.run_action = QAction(qta.icon("ph.play-bold", color=ACCENT), "Run", self)
        self.run_action.setShortcut("Ctrl+R")
        self.run_action.setToolTip("Analyze (if needed) then rebuild the solid (Ctrl+R)")
        self.run_action.triggered.connect(self.run_rebuild)
        self.cancel_action = QAction(qta.icon("ph.stop-bold", color=ERROR), "Cancel", self)
        self.cancel_action.setEnabled(False)
        self.cancel_action.setShortcut("Esc")
        self.cancel_action.setToolTip("Cancel the in-flight run (Esc)")
        self.cancel_action.triggered.connect(self.cancel_rebuild)
        for act in (self.analyze_action, self.run_action, self.cancel_action):
            run_menu.addAction(act)
        toolbar.addAction(open_action)
        toolbar.addSeparator()
        toolbar.addAction(self.analyze_action)
        toolbar.addAction(self.run_action)
        toolbar.addAction(self.cancel_action)
        self.addToolBar(toolbar)

    @staticmethod
    def _sync_action_checked(action: QAction, on: bool):
        """Update a checkable QAction's state to match the Viewport signal that just fired,
        without re-triggering `toggled` back into the Viewport setter it came from (same
        blockSignals pattern as `_on_viewport_layer_toggled`)."""
        if action.isChecked() != on:
            action.blockSignals(True)
            action.setChecked(on)
            action.blockSignals(False)

    def _on_viewport_render_mode_changed(self, mode: str):
        for m, act in self._render_mode_actions.items():
            self._sync_action_checked(act, m == mode)

    def _export_viewport_image(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export viewport image", "viewport.png", "PNG images (*.png)")
        if not path:
            return
        if os.path.exists(path):
            os.remove(path)  # never write over an existing file in place -- APFS birthtime rule
        try:
            self.viewport.screenshot(path)
        except Exception as exc:
            self.log_line(f"export viewport image failed: {exc}", level="error")
            return
        self.status_label.setText(f"Saved {os.path.basename(path)}")
        self.log_line(f"✓ Viewport image saved — {_elide_path(path)}")

    def _show_help(self, page_id: str = None):
        """Open (or re-raise) the single manual window, optionally deep-linked to one page.

        One lazily-created instance kept for the window's lifetime, rather than a fresh dialog
        per invocation: re-opening Help should return the reader to where they were, and a
        second dialog stacked on the first is the usual way that gets lost."""
        if self._help_dialog is None:
            self._help_dialog = HelpDialog(self)
        self._help_dialog.show()
        self._help_dialog.raise_()
        self._help_dialog.activateWindow()
        if page_id:
            self._help_dialog.show_page(page_id)

    def _show_demos(self):
        """Open (or re-raise) the guided-demo picker. Same single-instance idiom as the manual."""
        from app.demos import DemoPickerDialog
        if self._demo_picker is None:
            self._demo_picker = DemoPickerDialog(self)
        self._demo_picker.show()
        self._demo_picker.raise_()
        self._demo_picker.activateWindow()

    def start_demo(self, milestone: str):
        """Generate the demo's mesh if needed, load it, reset the options to app defaults, and
        hand over to a `TourController`.

        Lives on the window rather than in the picker so `--smoke-tour` and the tests can drive a
        demo without a dialog, and so the pre-flight (a run already in flight, a missing
        scikit-image, the heavy-generation confirmation) has the window it needs to warn on."""
        from app import demos
        if milestone not in demos.DEMOS:
            return False
        if self._tour is not None:
            self._tour.cancel()
        if not demos.confirm_and_start(self, milestone):
            return False
        if self._demo_picker is not None:
            self._demo_picker.close()
        demo = demos.DEMOS[milestone]
        self.status_label.setText(f"Preparing demo geometry ({milestone})...")
        self.log_line(f"demo: preparing {milestone} — {demo.name}")
        self.spinner.start()
        # Which demo is being prepared is state on the window rather than a bound argument,
        # because the slots below have to be BOUND METHODS of this QObject -- see
        # `demos.begin_mesh_generation`'s docstring: a lambda has no thread affinity, so Qt runs
        # it directly in the worker thread and the tour ends up built on the wrong thread.
        self._demo_milestone = milestone
        demos.begin_mesh_generation(
            self, milestone, self._on_demo_mesh_ready, self._on_demo_mesh_failed)
        return True

    def _on_demo_mesh_ready(self, stl_path: str):
        from app import demos
        from app.tour import TourController
        self.spinner.stop()
        self.status_label.setText("Ready")
        milestone = self._demo_milestone
        demo = demos.DEMOS[milestone]
        self._set_path_field(self.input_path_edit, stl_path)
        self._load_input_preview(stl_path)
        os.makedirs("out/demos", exist_ok=True)
        self._set_path_field(self.output_path_edit,
                             os.path.join("out", "demos", f"{milestone}_rebuilt.step"))
        # Back to the app's own defaults, NOT to the demo's answers: the walkthrough teaches by
        # having the reader set each one, which only works from a known starting state (and would
        # be a lie if the tour asked for a value that was already there).
        self.axis_combo.setCurrentText("auto")
        self.units_combo.setCurrentText("mm")
        self.sections_spin.setValue(40)
        self.chord_tol_auto.setChecked(True)
        self.chord_tol_spin.setValue(0.5)
        self.roundness_tol_auto.setChecked(True)
        self.roundness_tol_spin.setValue(0.0)
        self.adaptive_check.setChecked(False)
        self.log_line(f"demo: {milestone} — {demo.name} ({len(demo.steps)} steps)")
        self._tour = TourController(self, demo.steps, demo.name)
        self._tour.finished.connect(self._on_tour_finished)
        self._tour.start()

    def _on_tour_finished(self):
        self._tour = None

    def _on_demo_mesh_failed(self, message: str):
        milestone = self._demo_milestone
        self.spinner.stop()
        self.status_label.setText("Ready")
        self.log_line(f"demo: could not prepare {milestone} — {message}", level="error")
        QMessageBox.critical(
            self, f"{milestone} — could not prepare the demo",
            f"The test geometry for {milestone} could not be generated:\n\n{message}\n\n"
            "The generators live in harness/ and write to harness/truth/ — check that the "
            "folder is writable and that the app was started from the repository root.")

    def _create_desktop_shortcut(self):
        """Write the double-click launchers, then say exactly what was created and where.

        The import is inside the method, not at module top level, so that `scripts/` never
        becomes a startup dependency of the application -- the GUI must still start on a machine
        where that directory was not copied across. No worker thread: this is three small file
        writes plus a sub-second icon render."""
        def failed(exc):
            self.log_line(f"desktop shortcut: {exc}", level="error")
            QMessageBox.warning(
                self, "Create Desktop Shortcut",
                f"The launcher could not be created:\n\n{exc}\n\n"
                "You can also run it by hand from the repository folder:\n"
                "    .venv/bin/python scripts/create_desktop_shortcut.py")

        try:
            # Deferring the import keeps a missing scripts/ from breaking startup -- but it
            # would then fail on the click instead, so ImportError is handled here too.
            from scripts.create_desktop_shortcut import (
                ShortcutError, completion_message, create_shortcuts, default_repo_root)
        except ImportError as exc:
            failed(exc)
            return
        try:
            created = create_shortcuts(default_repo_root())
        except (ShortcutError, OSError) as exc:
            failed(exc)
            return
        for path in created:
            self.log_line(f"desktop shortcut: wrote {path}")
        QMessageBox.information(self, "Create Desktop Shortcut", completion_message(created))

    def _show_about(self):
        QMessageBox.about(
            self, f"About {APP_TITLE}",
            f"<b>{APP_TITLE}</b> v{__version__}<br><br>"
            "Burnback-surface STL &rarr; BRep STEP converter, for solid-rocket-motor geometry "
            "reconstruction and SpaceClaim/CAD import.<br><br>"
            "Engine: OCCT (via OCP/build123d) &middot; meshing/repair: trimesh &middot; "
            "quality: gmsh.<br>"
            "See MISSION.md for the full geometry pipeline spec.")

    # ---- docks ---------------------------------------------------------
    def _build_outline_dock(self):
        dock = QDockWidget("Outline", self)
        dock.setObjectName("outline_dock")
        self.outline_dock = dock
        self.outline = QTreeWidget()
        self.outline.setMinimumWidth(180)
        self.outline.setHeaderHidden(True)
        self.node_input = QTreeWidgetItem([NODE_INPUT])
        self.node_detected = QTreeWidgetItem([NODE_DETECTED])
        self.node_stations = QTreeWidgetItem([NODE_STATIONS])
        self.node_output = QTreeWidgetItem([NODE_OUTPUT])
        self.node_input.setIcon(0, qta.icon("ph.download-simple-bold", color=TEXT_SECONDARY))
        self.node_detected.setIcon(0, qta.icon("ph.crosshair-bold", color=TEXT_SECONDARY))
        self.node_stations.setIcon(0, qta.icon("ph.stack-bold", color=TEXT_SECONDARY))
        self.node_output.setIcon(0, qta.icon("ph.cube-bold", color=TEXT_SECONDARY))
        self.outline.addTopLevelItems(
            [self.node_input, self.node_detected, self.node_stations, self.node_output])
        dock.setWidget(self.outline)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _build_details_dock(self):
        dock = QDockWidget("Details", self)
        dock.setObjectName("details_dock")
        self.details_dock = dock
        self.details_stack = QStackedWidget()
        self.details_stack.setMinimumWidth(420)
        self.page_input = self._build_input_page()
        self.page_detected = self._build_property_page()
        self.page_stations = self._build_stations_page()
        self.page_output = self._build_output_page()
        for page in (self.page_input, self.page_detected, self.page_stations, self.page_output):
            self.details_stack.addWidget(page)
        dock.setWidget(self.details_stack)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _build_property_page(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        tree = PropertyTree()
        layout.addWidget(tree)
        w.tree = tree
        return w

    def _build_stations_page(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        chart = _RadiusProfileChart()
        layout.addWidget(chart)
        table = StationTable()
        # Selecting a row highlights that station's ring in the 3D view and on the profile chart
        # -- the table, the viewport, and the chart are three views of the same station data,
        # not three disconnected displays (2026-09-07 design review, V1).
        table.currentItemChanged.connect(self._on_station_row_selected)
        layout.addWidget(table)
        w.table = table
        w.chart = chart
        return w

    def _on_station_row_selected(self, current, _previous):
        if current is None:
            self.viewport.highlight_station(None)
            self.page_stations.chart.set_highlight(None)
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        z_report, r_outer = data
        # StationTable stores the REPORT-frame z (what `_station_rows` was given); the viewport's
        # rings were drawn in the input-frame axial projection (`z + axial_origin_z`, same
        # conversion `_station_rows` itself applies before slicing -- see `_on_rebuilt`).
        z_proj = z_report + getattr(self, "_last_axial_origin_z", 0.0)
        label = f"z={fmt_num(z_report, 1)}" + (f"  R={fmt_num(r_outer, 1)}" if r_outer is not None else "")
        self.viewport.highlight_station(z_proj, label)
        self.page_stations.chart.set_highlight(z_report)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-weight: 600; font-size: 11px; "
                           "letter-spacing: 1px; padding-top: 6px;")
        return lbl

    def _build_input_page(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(2)
        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        outer.addLayout(form)

        form.addRow(self._section_label("Source"))

        self.input_path_edit = QLineEdit()
        self.input_path_edit.setMinimumWidth(220)
        self.input_path_edit.editingFinished.connect(self._on_input_path_edited)
        browse_row = QWidget()
        row_layout = QHBoxLayout(browse_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.input_path_edit)
        browse_btn = QPushButton(qta.icon("ph.folder-open-bold", color=TEXT_SECONDARY), "Browse...")
        browse_btn.clicked.connect(self._browse_input)
        row_layout.addWidget(browse_btn)
        form.addRow("Input STL", browse_row)

        self.output_path_edit = QLineEdit("out/rebuilt.step")
        self.output_path_edit.setMinimumWidth(220)
        out_row = QWidget()
        out_layout = QHBoxLayout(out_row)
        out_layout.setContentsMargins(0, 0, 0, 0)
        out_layout.addWidget(self.output_path_edit)
        out_browse_btn = QPushButton(qta.icon("ph.folder-open-bold", color=TEXT_SECONDARY), "Browse...")
        out_browse_btn.clicked.connect(self._browse_output)
        out_layout.addWidget(out_browse_btn)
        form.addRow("Output STEP", out_row)

        form.addRow(self._section_label("Geometry"))

        self.axis_combo = QComboBox()
        self.axis_combo.addItems(["auto", "x", "y", "z"])
        self.axis_combo.setMaximumWidth(120)
        form.addRow("Axis", self.axis_combo)

        self.units_combo = QComboBox()
        self.units_combo.addItems(["mm", "in", "m"])
        self.units_combo.setMaximumWidth(120)
        self.units_combo.currentTextChanged.connect(self._on_units_changed)
        form.addRow("Units", self.units_combo)

        form.addRow(self._section_label("Fidelity"))

        self.sections_spin = QSpinBox()
        self.sections_spin.setRange(4, 500)
        self.sections_spin.setValue(40)
        self.sections_spin.setMinimumWidth(90)
        form.addRow("Sections", self.sections_spin)

        self.chord_tol_auto = QCheckBox("auto from mesh")
        self.chord_tol_auto.setToolTip(
            "Estimate the chord tolerance from the mesh's own chordal sag (2x the p95 of "
            "per-edge sag = extent x dihedral angle / 8), not the median edge length — a clean "
            "CAD tessellation has edges far longer than its actual deviation from the true "
            "surface.")
        self.chord_tol_auto.setChecked(True)
        self.chord_tol_spin = QDoubleSpinBox()
        self.chord_tol_spin.setRange(0.01, 100.0)
        # Four decimals, not QDoubleSpinBox's default two: a small part needs a small tolerance,
        # and at two decimals the box physically cannot hold one. M10's own official value is
        # 0.0125 mm (M8's 0.5 at 1/40 scale, HANDOFF §3.2) -- it rounded to 0.01 and the setting
        # simply could not be entered, found while writing the M10 guided demo against those
        # exact commands. Anything Brady scans at inch scale has the same problem.
        self.chord_tol_spin.setDecimals(4)
        self.chord_tol_spin.setSingleStep(0.05)
        self.chord_tol_spin.setValue(0.5)
        self.chord_tol_spin.setEnabled(False)
        self.chord_tol_spin.setMinimumWidth(80)
        self.chord_tol_auto.toggled.connect(lambda on: self.chord_tol_spin.setEnabled(not on))
        chord_row = QWidget()
        chord_layout = QHBoxLayout(chord_row)
        chord_layout.setContentsMargins(0, 0, 0, 0)
        chord_layout.addWidget(self.chord_tol_spin)
        chord_layout.addWidget(self.chord_tol_auto)
        form.addRow("Chord tol (mm)", chord_row)

        # Decoupled roundness-tolerance floor (2026-09-09): mirrors the chord-tol auto/spin
        # pattern exactly. A real CAD-exported/scanned part can be finely tessellated (tiny
        # chordal sag) yet genuinely not very round -- the classification gates need this
        # SEPARATE, independently-measured floor so --chord-tol can stay at the mesh's honest
        # faceting precision instead of being dragged coarse to satisfy roundness alone (the
        # trap that used to leave no --chord-tol value clearing both the roundness gates and the
        # boolean/ShapeFix construction precisions at once).
        self.roundness_tol_auto = QCheckBox("auto from mesh")
        self.roundness_tol_auto.setToolTip(
            "Measure the out-of-roundness noise floor from the mesh itself (12 probe "
            "cross-sections, worst-case + 20% headroom, capped at 2% of the fitted radius). "
            "0 disables the floor entirely -- classification gates then derive purely from "
            "--chord-tol, as before this existed.")
        self.roundness_tol_auto.setChecked(True)
        self.roundness_tol_spin = QDoubleSpinBox()
        self.roundness_tol_spin.setRange(0.0, 1000.0)
        self.roundness_tol_spin.setDecimals(4)
        self.roundness_tol_spin.setSingleStep(0.05)
        self.roundness_tol_spin.setValue(0.0)
        self.roundness_tol_spin.setEnabled(False)
        self.roundness_tol_spin.setMinimumWidth(80)
        self.roundness_tol_auto.toggled.connect(
            lambda on: self.roundness_tol_spin.setEnabled(not on))
        roundness_row = QWidget()
        roundness_layout = QHBoxLayout(roundness_row)
        roundness_layout.setContentsMargins(0, 0, 0, 0)
        roundness_layout.addWidget(self.roundness_tol_spin)
        roundness_layout.addWidget(self.roundness_tol_auto)
        form.addRow("Roundness tol (mm)", roundness_row)

        self.adaptive_check = QCheckBox("adaptive stations")
        self.adaptive_check.setToolTip(
            "Cluster stations at detected features (domes, fillets). Uniform spacing is often "
            "MORE robust for sharp slot/fillet transitions -- if a run crashes with adaptive on, "
            "try turning it off before adding sections.")
        form.addRow("", self.adaptive_check)

        outer.addStretch(1)

        form2 = QFormLayout()
        form2.addRow(self._section_label("Actions"))
        outer.addLayout(form2)

        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)
        self.analyze_btn = QPushButton(qta.icon("ph.magnifying-glass-bold", color=TEXT_SECONDARY), "Analyze")
        self.analyze_btn.clicked.connect(self.run_analyze)
        self.run_btn = QPushButton(qta.icon("ph.play-bold", color="#ffffff"), "Run")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run_rebuild)
        self.cancel_btn = QPushButton(qta.icon("ph.stop-bold", color=TEXT_DISABLED), "Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_rebuild)
        for btn in (self.analyze_btn, self.run_btn, self.cancel_btn):
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn_layout.addWidget(btn)
        outer.addWidget(btn_row)

        return w

    def _set_path_field(self, edit: QLineEdit, path: str):
        edit.setText(path)
        edit.setCursorPosition(0)  # show the start of a long path, not a truncated tail
        edit.setToolTip(path)

    def _build_output_page(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        self.error_banner = QLabel()
        self.error_banner.setObjectName("errorBanner")
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        layout.addWidget(self.error_banner)
        self.manifest_tree = PropertyTree()
        layout.addWidget(self.manifest_tree)
        reveal_btn = QPushButton(qta.icon("ph.export-bold", color=TEXT_SECONDARY), "Reveal file")
        reveal_btn.clicked.connect(self._reveal_output)
        layout.addWidget(reveal_btn)
        return w

    def _build_log_dock(self):
        # Dock's internal objectName is unchanged (tests key off it) -- only its title and
        # contents changed, from a plain log to the instrument dashboard (Brady, 2026-09-05):
        # gauge dials for the run's phases plus the log (moved here, same widget/object name so
        # `log_line()` needs no changes). A verification readout used to live here too but
        # duplicated the Details dock's Output page -- dropped 2026-09-06, Brady's call.
        dock = QDockWidget("Dashboard", self)
        dock.setObjectName("log_dock")
        self.log = QTextEdit()
        self.log.setObjectName("logConsole")
        self.log.setReadOnly(True)
        self.dashboard = Dashboard(self.log)
        dock.setWidget(self.dashboard)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def _build_status_bar(self):
        bar = self.statusBar()
        self.status_label = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setMaximumWidth(240)
        self.progress_bar.hide()  # G3 review #2 item 3: only visible during an active run
        self.spinner = BusySpinner()
        # Quiet telemetry line (2026-09-07 design review, U5): axis/units/triangle count once
        # Analyze has run, station count appended once a Rebuild completes -- gives the chrome a
        # persistent read of what's actually loaded, not just transient status text.
        self.telemetry_label = QLabel("")
        mono = QFont(self.telemetry_label.font())
        mono.setFamilies([f.strip(' "') for f in MONO_FAMILY.split(",")])
        self.telemetry_label.setFont(mono)
        self.telemetry_label.setStyleSheet(f"color: {TEXT_DISABLED};")
        version_label = QLabel(f"v{__version__}")
        version_label.setFont(mono)
        version_label.setStyleSheet(f"color: {TEXT_DISABLED};")
        bar.addWidget(self.status_label, 1)
        bar.addPermanentWidget(self.telemetry_label)
        bar.addPermanentWidget(self.progress_bar)
        bar.addPermanentWidget(self.spinner)
        bar.addPermanentWidget(version_label)

    # ---- view menu / legend sync ------------------------------------------
    def _on_viewport_layer_toggled(self, key: str, on: bool):
        act = self._layer_actions.get(key)
        if act is not None and act.isChecked() != on:
            act.blockSignals(True)
            act.setChecked(on)
            act.blockSignals(False)

    # ---- outline selection ----------------------------------------------
    def _on_outline_selection(self, current, _previous):
        if current is None:
            return
        # Keyed by item IDENTITY, not `.text(0)` -- the Stations/Output nodes grow a suffix
        # once a run completes ("Stations (40)", "Output ✓", 2026-09-07 design review, U7),
        # which a text-keyed lookup would silently stop matching.
        page = {
            self.node_input: self.page_input,
            self.node_detected: self.page_detected,
            self.node_stations: self.page_stations,
            self.node_output: self.page_output,
        }[current]
        self.details_stack.setCurrentWidget(page)

    # ---- logging ----------------------------------------------------------
    LOG_COLORS = {"info": TEXT_SECONDARY, "warn": WARNING, "error": ERROR}

    def log_line(self, text: str, level: str = "info"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        color = self.LOG_COLORS.get(level, TEXT_SECONDARY)
        self.log.append(
            f'<span style="color:{TEXT_DISABLED}">{ts}</span> '
            f'<span style="color:{color}">{html.escape(text)}</span>')

    # ---- file pickers -------------------------------------------------
    def _browse_input(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select input STL", "", "STL files (*.stl)")
        if path:
            self._set_path_field(self.input_path_edit, path)
            self._load_input_preview(path)

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Select output STEP", "", "STEP files (*.step)")
        if path:
            self._set_path_field(self.output_path_edit, path)

    def _on_input_path_edited(self):
        # Covers typing/pasting a path directly rather than using Browse... -- fires on Enter or
        # focus-loss (QLineEdit.editingFinished), not per keystroke.
        path = self.input_path_edit.text().strip()
        if path and os.path.exists(path):
            self._load_input_preview(path)

    def _update_window_title(self, path: str = None):
        self.setWindowTitle(f"{os.path.basename(path)} — {APP_TITLE}" if path else APP_TITLE)

    def _on_units_changed(self, _units: str):
        """The input mesh preview is displayed at 1mm-per-file-unit (`PreviewWorker`/
        `AnalyzeWorker`/`RebuildWorker` all scale by `pipeline.io.parse_units`) -- so an already-
        loaded inches file rendered a real 25.4x too small next to the (always-mm) rebuilt solid
        until the units were told to it. Reloading here, rather than rescaling the cached mesh in
        place, keeps this one code path (`_load_input_preview`) as the only place that decides
        what a freshly-chosen unit means for the display, instead of a second scaling formula
        someone could get out of sync with the first. Stale Detected/Stations/Output state is
        correctly cleared too: whatever was computed under the old units no longer applies."""
        path = self.input_path_edit.text().strip()
        if path and os.path.exists(path):
            self._load_input_preview(path)

    def _load_input_preview(self, path: str):
        """Show the input mesh in the viewport immediately on file selection, before Analyze or
        Run ever runs -- previously nothing appeared until Analyze completed, leaving no visual
        confirmation that a file was actually loaded (Brady, 2026-09-06)."""
        self.viewport.reset_scene()
        self._analysis = None
        self._analyzed_input_path = None
        self._update_window_title(path)
        self.telemetry_label.setText("")
        self.node_stations.setText(0, NODE_STATIONS)
        self.node_output.setText(0, NODE_OUTPUT)
        self.page_stations.table.set_rows([])
        self.page_stations.chart.set_rows([])
        self.status_label.setText("Loading preview...")
        worker = PreviewWorker(path, self.units_combo.currentText())
        worker.finished.connect(self._on_input_preview_loaded)
        worker.failed.connect(self._on_input_preview_failed)
        thread = run_in_thread(worker)
        self._workers.append(worker)
        self._threads.append(thread)
        thread.start()

    def _on_input_preview_loaded(self, mesh):
        self.status_label.setText("Ready")
        self.viewport.show_input_mesh(mesh)
        path = self.input_path_edit.text().strip()
        self.log_line(f"loaded preview: {path}")
        self.input_loaded.emit(path)

    def _on_input_preview_failed(self, message):
        self.status_label.setText("Ready")
        self.log_line(f"viewport: could not load input mesh preview: {message}", level="warn")

    def _reveal_output(self):
        if self._result is None:
            return
        folder = os.path.dirname(os.path.abspath(self._result.output_path)) or "."
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ---- actions --------------------------------------------------------
    def run_analyze(self):
        input_path = self.input_path_edit.text().strip()
        if not input_path:
            QMessageBox.warning(self, "Analyze", "Choose an input STL first.")
            return
        axis = self.axis_combo.currentText()
        units = self.units_combo.currentText()
        self.status_label.setText("Analyzing...")
        self.spinner.start()
        self._set_running(True)
        self.dashboard.reset()
        self.dashboard.mission_start()
        self.log_line(f"analyze: {input_path} (axis={axis}, units={units})")
        worker = AnalyzeWorker(input_path, axis, units)
        worker.progress.connect(self._on_analyze_progress)
        worker.finished.connect(self._on_analyzed)
        worker.failed.connect(self._on_failed)
        thread = run_in_thread(worker)
        self._workers.append(worker)
        self._threads.append(thread)
        thread.start()

    def _on_analyze_progress(self, stage, frac, message):
        self._last_stage = stage  # shared with _on_progress -- _on_failed reads this either way
        self.dashboard.on_stage(stage, frac, message)

    def _on_analyzed(self, analysis, mesh):
        self._analysis = analysis
        self._analyzed_input_path = self.input_path_edit.text().strip()
        self.analyze_finished.emit()
        self.spinner.stop()
        self.dashboard.mark_analyze_done()
        self.status_label.setText("Analyzed")
        # Spell out each check's pass/fail plainly (Brady, 2026-09-06: "it needs to state that
        # it is watertight when it passes that check, etc") instead of dumping raw field values
        # -- "watertight=True" and a bare axis vector don't read as a checklist at a glance.
        watertight_ok = analysis.is_watertight
        single_body = analysis.body_count == 1
        checks = ["✓ watertight" if watertight_ok else "✗ NOT watertight (repair may be needed)",
                  "✓ single body" if single_body else f"⚠ {analysis.body_count} bodies detected"]
        if analysis.n_dropped_islands:
            checks.append(f"⚠ {analysis.n_dropped_islands} noise island(s) dropped")
        level = "info" if (watertight_ok and single_body) else "warn"
        self.log_line(
            f"✓ Analyze done — {', '.join(checks)}, "
            f"axis {axis_label(analysis.frame_axis)} "
            f"(confidence {analysis.axis_confidence * 100:.0f}%), "
            f"extent {fmt_num(analysis.axial_extent_mm)} mm, {analysis.triangle_count:,} triangles",
            level=level)
        self.telemetry_label.setText(
            f"{axis_label(analysis.frame_axis)} · {analysis.units} · "
            f"{analysis.triangle_count:,} tris")
        self.page_detected.tree.set_groups(_analysis_property_groups(analysis))
        if self.chord_tol_auto.isChecked():
            self.chord_tol_spin.setValue(max(analysis.suggested_chord_tol_mm, 0.01))
        if self.roundness_tol_auto.isChecked():
            self.roundness_tol_spin.setValue(max(analysis.suggested_roundness_tol_mm, 0.0))
        if mesh is not None:
            self.viewport.show_input_mesh(mesh)
        else:
            self.log_line("viewport: could not load input mesh preview", level="warn")
        self.outline.setCurrentItem(self.node_detected)
        if getattr(self, "_pending_rebuild", False):
            self._pending_rebuild = False
            self._start_rebuild()  # re-enables the buttons itself once the rebuild finishes
        else:
            self.dashboard.mission_done()
            self._set_running(False)

    def run_rebuild(self):
        input_path = self.input_path_edit.text().strip()
        output_path = self.output_path_edit.text().strip()
        if not input_path or not output_path:
            QMessageBox.warning(self, "Run", "Input STL and output STEP path are required.")
            return
        # "auto from mesh" promises a chord-tol sized to THIS input, but without an Analysis to
        # read it from, the code used to silently fall back to the raw 0.5mm spinbox default --
        # often far too fine for a real motor's scale, which can fail geometry construction
        # outright with no indication chord-tol was ever the issue (Brady, 2026-09-06: M8 at the
        # default 0.5mm -- instead of the ~0.94mm Analyze would have suggested -- failed
        # BRepCheck_Analyzer validity). Run Analyze first (silently chaining into the rebuild
        # once it completes) whenever there's no analysis yet, or it's for a different input.
        if self.chord_tol_auto.isChecked() and (
                self._analysis is None or getattr(self, "_analyzed_input_path", None) != input_path):
            self.log_line("auto chord-tol needs Analyze first — running it now")
            self._pending_rebuild = True
            self.run_analyze()
            return
        self._start_rebuild()

    def _start_rebuild(self):
        input_path = self.input_path_edit.text().strip()
        output_path = self.output_path_edit.text().strip()
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        chord_tol = (self._analysis.suggested_chord_tol_mm if (self.chord_tol_auto.isChecked() and self._analysis)
                     else self.chord_tol_spin.value())
        # "auto" means let the ENGINE measure the floor itself from this exact run's chord_tol
        # (None), not the GUI's own Analyze-time probe value -- same reasoning RebuildOptions'
        # own docstring gives for roundness_tol=None.
        roundness_tol = None if self.roundness_tol_auto.isChecked() else self.roundness_tol_spin.value()
        report_path = os.path.splitext(output_path)[0] + ".report.json"
        stl_preview_path = os.path.splitext(output_path)[0] + ".preview.stl"
        opts = RebuildOptions(
            input_stl=input_path, output=output_path, axis=self.axis_combo.currentText(),
            units=self.units_combo.currentText(), sections=self.sections_spin.value(),
            adaptive=self.adaptive_check.isChecked(), chord_tol=chord_tol,
            roundness_tol=roundness_tol, report=report_path, stl=stl_preview_path,
        )
        self.status_label.setText("Running...")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.spinner.start()
        self._set_running(True)
        self._last_stage = None
        self.dashboard.reset_rebuild_dials()
        self.dashboard.mission_start()
        rt_label = "auto" if opts.roundness_tol is None else f"{opts.roundness_tol:.3f}"
        self.log_line(f"rebuild: {input_path} -> {output_path} (sections={opts.sections}, "
                      f"chord_tol={opts.chord_tol:.3f}, roundness_tol={rt_label}, "
                      f"adaptive={opts.adaptive})")
        worker = RebuildWorker(opts)
        self._active_worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_rebuilt)
        worker.failed.connect(self._on_failed)
        thread = run_in_thread(worker)
        self._threads.append(thread)
        thread.start()

    def cancel_rebuild(self):
        if getattr(self, "_active_worker", None) is not None:
            self._active_worker.cancel()
            self.status_label.setText("Cancelling...")
            self.log_line("cancel requested", level="warn")

    def _set_running(self, running: bool):
        # Also disables Analyze, not just Run/Cancel -- Brady, 2026-09-06: clicking Analyze then
        # Run before Analyze finished (M13's Analyze alone can run ~19 minutes) let a SECOND,
        # redundant AnalyzeWorker start via run_rebuild()'s own "needs Analyze first" chaining,
        # since nothing blocked either button while the first Analyze was still in flight --
        # confirmed by the log showing two identical "Analyze done" lines seconds apart, but
        # only one actual rebuild (whichever Analyze completion's _pending_rebuild chain won the
        # race). Both buttons now go through this one guard for the whole Analyze-and/or-Rebuild
        # operation, however it started.
        self.run_btn.setEnabled(not running)
        self.run_action.setEnabled(not running)
        self.analyze_btn.setEnabled(not running)
        self.analyze_action.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.cancel_action.setEnabled(running)
        self.cancel_btn.setIcon(qta.icon("ph.stop-bold", color=ERROR if running else TEXT_DISABLED))

    def _on_progress(self, stage, frac, message):
        self._last_stage = stage
        self.progress_bar.setValue(int(max(0.0, min(1.0, frac)) * 100))
        self.status_label.setText(f"{stage}: {message}")
        self.log_line(f"[{stage}] {frac:.2f} {message}")
        self.dashboard.on_stage(stage, frac, message)

    def _on_rebuilt(self, result, solid_mesh, input_mesh):
        self._result = result
        self._set_running(False)
        self.progress_bar.hide()
        self.spinner.stop()
        self.error_banner.hide()
        self.status_label.setText("Done")
        self.log_line(f"✓ Rebuild done — {_elide_path(result.output_path)}")
        self.dashboard.on_done()
        man = manifest_mod.build(result, self._analysis)
        self.manifest_tree.set_groups(_manifest_property_groups(man))
        report = result.report or {}
        stations_z = report.get("stations_z_mm", [])
        events_z = set(round(z, 6) for z in report.get("topology_events_z_mm", []))
        # This rebuild's OWN axis, NOT the cached Analysis's -- they can legitimately differ.
        # `--axis auto` re-detects fresh on every rebuild (not reused from Analyze), and its
        # secondary origin-refinement step (`_axis_origin_refine`) depends on --chord-tol, so a
        # rebuild run with a different chord-tol than whatever Analyze last probed with can end
        # up with a measurably different axis. Drawing the overlays with the STALE Analyze axis
        # while the solid itself is built (and positioned) in the CURRENT rebuild's own axis
        # produces exactly the "station rings look huge/spiraled, way outside the small correct
        # solid" bug Brady hit 2026-09-06: rings placed along a slightly wrong axis fan out
        # sideways more and more with each station's z, since each one is offset by
        # `wrong_axis * z` instead of the true axis.
        axis_unit = (report.get("frame", {}).get("axis")
                    or (self._analysis.frame_axis if self._analysis else (0, 0, 1)))
        axis_point = None
        sections = None
        try:
            # Run without a prior Analyze: the input mesh was never loaded into the scene,
            # which left the Input-mesh layer (and B-swap) empty (Brady, 2026-09-04)
            if not self.viewport.has_input_mesh and input_mesh is not None:
                self.viewport.show_input_mesh(input_mesh)
            if solid_mesh is not None:
                self.viewport.show_solid_mesh(solid_mesh)
                # Radius/centering/axial-offset computed axis-generically (2026-09-07 fix): the
                # old `max(abs(x), abs(y))` bounds-box radius and `normal * z` ring center both
                # silently assumed the motor axis is Z and passes through the world origin --
                # true for every synthetic milestone, false for Brady's 2026-09-06 real motor
                # (axis == X, axis offset ~58mm off-origin), which inflated the ring radius 12x
                # and shifted every ring/axis-line off both center and its true axial position.
                radial_mm, axis_point, proj_min, proj_max = _axis_frame_metrics(solid_mesh, axis_unit)
                axial_origin_z = float(report.get("axial_origin_z") or 0.0)
                self._last_axial_origin_z = axial_origin_z
                stations_proj = [z + axial_origin_z for z in stations_z]
                events_proj = sorted(z + axial_origin_z for z in events_z)
                # Slice once, feed both the 3D ring layer AND the Stations table below -- each
                # ring sized/shaped by its OWN station's cross-section, not one global radius
                # (Brady's 2026-09-08 M9 report: rings at the full-body silhouette made zoomed-in
                # feature regions look station-free, and edge-on views near-blank).
                sections = _station_cross_sections(solid_mesh, stations_proj, axis_unit, axis_point)
                self.viewport.show_station_planes(
                    stations_proj, radial_mm, axis_unit, axis_point,
                    radii_mm=[radii[0] if radii else None for _sec, radii in sections],
                    sections=[sec for sec, _radii in sections])
                event_sections = _station_cross_sections(solid_mesh, events_proj, axis_unit, axis_point)
                self.viewport.show_topology_events(
                    events_proj, radial_mm, axis_unit, axis_point,
                    radii_mm=[radii[0] if radii else None for _sec, radii in event_sections])
                # End-band "dome cap" layer (Brady's 2026-09-08 M8 question: "why do the rings
                # stop partway up each dome?"): the engine deliberately places NO stations within
                # `station_eps` of either tip -- a per-station circle fit is systematically
                # biased low there, so that band is rebuilt from a vertex-refined dome fit
                # instead (pipeline/engine.py, `_dome_model`). Correct, but it left the viewport
                # dark exactly where the surface curves most, reading as "unverified." These
                # extra slices come from the ALREADY-BUILT solid (not a re-derivation of the fit
                # that could drift from what was actually built), tip-clustered out to the
                # solid's true axial extremes (proj_min/proj_max -- the extent Bounds Z checks),
                # and drawn by `show_dome_caps` in a deliberately non-station style.
                cap_ends = []
                for tip_z, cap_z in zip(
                        (proj_min, proj_max),
                        _dome_cap_samples(stations_proj, proj_min, proj_max)):
                    if not cap_z:
                        continue
                    cap_secs = _station_cross_sections(solid_mesh, cap_z, axis_unit, axis_point)
                    cap_ends.append((tip_z, [(z, sec, radii[0] if radii else None)
                                             for z, (sec, radii) in zip(cap_z, cap_secs)]))
                self.viewport.show_dome_caps(cap_ends, axis_unit, axis_point)
                self.viewport.show_axis_line(radial_mm, proj_max - proj_min, axis_unit,
                                             origin_z_mm=0.5 * (proj_min + proj_max),
                                             axis_point=axis_point)
                # Cache the axis frame for the Section-view clip plane (Phase 3, V2) and the
                # deviation heatmap's color-scale reference (V3) -- both act on this same solid.
                self.viewport.set_axis_frame(axis_unit, axis_point, proj_min, proj_max)
                dev_tol = (report.get("verification") or {}).get("deviation", {}).get("tol_mm")
                self.viewport.set_deviation_tolerance(dev_tol)
        except Exception as exc:
            self.log_line(f"viewport: could not load solid preview: {exc}", level="warn")
        rows = _station_rows(report, stations_z, events_z, solid_mesh, axis_unit, axis_point,
                             sections=sections)
        self.page_stations.table.set_rows(rows)
        self.page_stations.chart.set_rows(rows)
        n_stations = len(stations_z)
        self.node_stations.setText(0, f"{NODE_STATIONS} ({n_stations})")
        ok = bool(report.get("verification"))
        self.node_output.setText(0, f"{NODE_OUTPUT} ✓" if ok else NODE_OUTPUT)
        current_telemetry = self.telemetry_label.text()
        base = current_telemetry.split(" · ", 2)
        base = " · ".join(base[:2]) if len(base) >= 2 else current_telemetry
        if base:
            self.telemetry_label.setText(f"{base} · {n_stations} stations")
        self.outline.setCurrentItem(self.node_output)
        self.rebuild_finished.emit(_verification_all_passed(report))

    def _on_failed(self, kind, message):
        self._pending_rebuild = False  # an Analyze that failed must not later auto-trigger a run
        self._set_running(False)
        self.progress_bar.hide()
        self.spinner.stop()
        self.status_label.setText(f"Failed: {kind}")
        self.log_line(f"FAILED [{kind}] {message}", level="error")
        self.dashboard.on_failed(getattr(self, "_last_stage", None), kind)
        self.error_banner.setText(f"{kind}: {message}")
        self.error_banner.show()
        self.manifest_tree.set_groups([("Error", [("Kind", kind, None), ("Message", message, message)])])
        self.outline.setCurrentItem(self.node_output)
        self.run_failed.emit(kind, message)


def _analysis_property_groups(analysis) -> list:
    return [
        ("Frame", [
            ("Axis", f"{axis_label(analysis.frame_axis)} (confidence {analysis.axis_confidence * 100:.0f}%)",
             analysis.frame_axis),
            ("Origin (X, Y)", f"{fmt_num(analysis.origin_xy_mm[0])}, {fmt_num(analysis.origin_xy_mm[1])} mm",
             analysis.origin_xy_mm),
            ("Units", analysis.units, None),
        ]),
        ("Mesh", [
            ("Extent", f"{fmt_num(analysis.axial_extent_mm)} mm", analysis.axial_extent_mm),
            ("Bodies", str(analysis.body_count), None),
            ("Triangles", f"{analysis.triangle_count:,}", None),
            ("Median edge length", f"{fmt_num(analysis.median_edge_length_mm, 3)} mm",
             analysis.median_edge_length_mm),
            ("Watertight", "Yes" if analysis.is_watertight else "No", None),
            ("Dropped islands", str(analysis.n_dropped_islands), None),
            ("Bounds", fmt_bounds(analysis.bounds_mm), str(analysis.bounds_mm)),
        ]),
        ("Suggested run settings", [
            ("Chord tol (auto)", f"{fmt_num(analysis.suggested_chord_tol_mm, 3)} mm",
             analysis.suggested_chord_tol_mm),
            ("Roundness noise (auto)", f"{fmt_num(analysis.suggested_roundness_tol_mm, 3)} mm",
             analysis.suggested_roundness_tol_mm),
        ]),
    ]


def _verification_glyph(passed):
    """(glyph, color) for a verification verdict: green check for pass, amber cross for fail,
    grey dash for null/not-available. Failed checks are informational only — amber, not ERROR
    red, and nothing is blocked."""
    if passed is True:
        return "✓", SUCCESS
    if passed is False:
        return "✗", WARNING
    return "–", TEXT_DISABLED


def _with_hint(check: dict, text: str) -> str:
    """Hard-wrap a Verification row's status text into explicit `\\n`-separated lines (always --
    a PASSING row's status line can be exactly as long as a failing one, e.g. a full bounds
    comparison), then append a failed check's engine-computed fix suggestion right into the
    visible text (not just the tooltip) -- a failure with no visible next step was exactly
    Brady's complaint 2026-09-05: the hint was being computed but only reachable by hovering the
    raw dict.

    `PropertyTree` doesn't reflow text to the column's actual pixel width -- see its own
    `setWordWrap` comment for why that reflow is deliberately off; every long value must arrive
    here pre-wrapped into explicit lines, PASSING rows included, or it silently overflows the
    column with no wrap and no elide (found 2026-09-05 verifying this same fix: wrapping only
    the failing branch here fixed the hint but left passing Bounds rows overflowing, since they
    never went through `textwrap.fill` at all).

    Wrap width 34->40 and a blank-line-separated "WHAT TO DO" label, not just a small "↳" arrow
    (2026-09-08, Brady's feedback: "the user directions... kind of just get lost in the text and
    its not clear what I should do") -- some hints are now full sentences explaining WHY a fix
    won't help (see `_axial_bounds_hint`), and at the old 34-char width those wrapped into many
    short, choppy lines that read as more text-noise than guidance. The blank line + label gives
    the eye a clear place to stop reading "what happened" and start reading "what to do about
    it," without needing a second color (PropertyTree's value column is one foreground color per
    row -- see `_verification_group`'s own `row.setForeground` -- so structure/whitespace is the
    tool available here, not color, for calling out the hint)."""
    wrapped_text = textwrap.fill(text, width=40, subsequent_indent="   ")
    if check.get("pass") is False and check.get("hint"):
        wrapped_hint = textwrap.fill(check["hint"], width=40, subsequent_indent="   ")
        return wrapped_text + "\n\nWHAT TO DO:\n" + wrapped_hint
    return wrapped_text


def _verification_group(verif: dict):
    """The Output page's "Verification" group (engine-computed `report["verification"]`,
    input mesh vs produced solid): one row per check, value text leading with a colored glyph
    followed by the two measured values, the delta, and the tolerance in-line. A failed check
    that carries a `hint` shows it as a second line right in the Value column."""
    rows = []
    wt = verif.get("watertight")
    if wt:
        glyph, color = _verification_glyph(wt.get("pass"))
        text = f"{glyph}  {'watertight' if wt.get('pass') else 'NOT watertight'}"
        rows.append(("Watertight", text, str(wt), color))
    vol = verif.get("volume")
    if vol:
        glyph, color = _verification_glyph(vol.get("pass"))
        if vol.get("pass") is None or vol.get("input_mm3") is None:
            text = f"{glyph}  {vol.get('note', 'volume comparison unavailable')}"
        else:
            text = (f"{glyph}  {fmt_num(vol['input_mm3'] / 1e6, 3)} L vs "
                    f"{fmt_num(vol['solid_mm3'] / 1e6, 3)} L "
                    f"(Δ {vol['delta_pct']:.4g} % ≤ {vol['tol_pct']:g} %)")
        rows.append(("Volume", _with_hint(vol, text), str(vol), color))
    for ax in ("x", "y", "z"):
        b = (verif.get("bounds") or {}).get(ax)
        if not b:
            continue
        glyph, color = _verification_glyph(b.get("pass"))
        text = (f"{glyph}  {ax.upper()}: {fmt_num(b['input_mm'][0])}…{fmt_num(b['input_mm'][1])}"
                f" vs {fmt_num(b['solid_mm'][0])}…{fmt_num(b['solid_mm'][1])} mm "
                f"(max Δ {b['max_dev_mm']:.3g} ≤ {b['tol_mm']:.3g} mm)")
        rows.append((f"Bounds {ax.upper()}", _with_hint(b, text), str(b), color))
    bod = verif.get("bodies")
    if bod:
        glyph, color = _verification_glyph(bod.get("pass"))
        rows.append(("Bodies",
                     f"{glyph}  {bod['expected']} expected vs {bod['solid_bodies']} in STEP",
                     str(bod), color))
    dev = verif.get("deviation")
    if dev:
        glyph, color = _verification_glyph(dev.get("pass"))
        if dev.get("pass") is None:
            text = f"{glyph}  approx. deviation unavailable ({dev.get('error', 'n/a')})"
        else:
            text = (f"{glyph}  p95 {dev['approx_p95_mm']:.3g} mm, max "
                    f"{dev['approx_max_mm']:.3g} mm (p95 ≤ {dev['tol_mm']:.3g} mm, approx.)")
        rows.append(("Deviation", _with_hint(dev, text), str(dev), color))
    if "error" in verif:
        glyph, color = _verification_glyph(None)
        rows.append(("Note", f"{glyph}  verification incomplete: {verif['error']}",
                     verif["error"], color))
    return ("Verification", rows)


def _verification_all_passed(report: dict) -> bool:
    """Did every verification check the engine ran actually pass?

    Walks exactly the checks `_verification_group` renders, so what a guided demo claims about a
    run and what the Output page shows the user can never disagree. A check whose `pass` is None
    (not applicable -- e.g. no input volume to compare against) is not a failure.

    An absent or empty `verification` block returns False rather than True: nothing was proven,
    and a demo that branches on this needs "we did not verify" to land on the same side as "a
    check failed", never on the reassuring side. (`_verification_group`'s own caller makes the
    same call -- `ok = bool(report.get("verification"))`.)"""
    verif = (report or {}).get("verification") or {}
    checks = [verif.get("watertight"), verif.get("volume"), verif.get("bodies"),
              verif.get("deviation")]
    checks += [(verif.get("bounds") or {}).get(ax) for ax in ("x", "y", "z")]
    checks = [c for c in checks if isinstance(c, dict)]
    if not checks or "error" in verif:
        return False
    return all(c.get("pass") is not False for c in checks)


def _manifest_property_groups(man: dict) -> list:
    groups = []
    verif = man.get("verification")
    if verif:
        groups.append(_verification_group(verif))
    groups += [
        ("Output", [
            ("STEP file", _elide_path(man.get("output_path", "-")), man.get("output_path")),
            ("Preview STL", _elide_path(man.get("stl_path") or "-"), man.get("stl_path")),
        ]),
    ]
    if "bodies" in man:
        vol = man.get("volume_mm3")
        vol_text = f"{fmt_num(vol, 1)} mm³ ({fmt_num(vol / 1e6, 3)} L)" if vol is not None else "-"
        groups.append(("Result", [
            ("Bodies", str(man.get("bodies")), None),
            ("Faces", str(man.get("faces")), None),
            ("Volume", vol_text, vol),
            ("Stations", str(man.get("n_stations")), None),
        ]))
    events = man.get("topology_events_z_mm") or []
    paths_used = man.get("paths_used") or {}
    groups.append(("Report", [
        ("Topology events", str(len(events)), str(events) if events else None),
        ("Paths used", ", ".join(f"{k}={v}" for k, v in paths_used.items()) or "-", str(paths_used)),
    ]))
    warnings = man.get("warnings") or []
    if warnings:
        groups.append(("Warnings", [(f"#{i + 1}", w, w) for i, w in enumerate(warnings)]))
    return groups


def _axis_frame_metrics(mesh, axis_unit):
    """Radial/axial extents of `mesh` about the (arbitrary) motor axis direction.

    The motor axis generally does NOT pass through the world origin (`frame.origin_xy_mm` is
    in the engine's internal transverse basis and cannot be mapped back to world coordinates
    from the report alone); the transverse part of the mesh point centroid is a point on the
    axis to well under a millimetre for a body that's actually a body of revolution. Returns
    (radial_extent_mm, axis_point, proj_min, proj_max): axis_point is that on-axis point,
    proj_min/proj_max the mesh's axial-projection range along axis_unit, radial_extent the max
    distance of any mesh point from the line {axis_point + t*axis_unit}.

    Fixes the 2026-09-06 giant-rings bug: the old call site assumed axis == Z and took raw X/Y
    bounds as the radial extent, which is wrong -- and badly wrong, not just off-axis-slightly
    wrong -- whenever the detected motor axis isn't Z (it folded the axial span into what was
    supposed to be a radial estimate)."""
    axis = np.asarray(axis_unit, dtype=float)
    axis = axis / (np.linalg.norm(axis) or 1.0)
    pts = np.asarray(mesh.points, dtype=float)
    if pts.size == 0:
        return 1.0, np.zeros(3), 0.0, 0.0
    proj = pts @ axis
    c = pts.mean(axis=0)
    axis_point = c - (c @ axis) * axis
    rel = pts - axis_point
    radial = rel - np.outer(rel @ axis, axis)
    return (float(np.linalg.norm(radial, axis=1).max()), axis_point,
            float(proj.min()), float(proj.max()))


def _station_cross_sections(solid_mesh, z_mesh_list, axis_unit, axis_point=None) -> list:
    """Slice the rebuilt solid preview once per station plane and hand back BOTH consumers'
    inputs: the labeled cross-section geometry (the viewport draws it as the literal station
    ring) and the per-loop max radii, largest first (the Stations table's R_outer/R_bore).
    One shared slice instead of the table and the 3D ring layer measuring the same thing two
    divergent ways -- until 2026-09-08 the 3D layer didn't measure it at ALL (one global radius
    for every ring), which is why a zoomed-in feature region looked station-free on Brady's M9
    run even though adaptive placement had stations right there. `z_mesh_list` is in MESH frame
    (caller applies `axial_origin_z`). Radii are measured from the motor axis line
    ({axis_point + t*axis}); `axis_point` defaults to the origin, only correct when the motor
    axis actually passes through it (2026-09-07 fix). Returns
    [(section_polydata_or_None, radii_desc_list), ...] aligned to `z_mesh_list`."""
    axis = np.array(axis_unit, dtype=float)
    axis = axis / (np.linalg.norm(axis) or 1.0)
    axis_point = np.zeros(3) if axis_point is None else np.asarray(axis_point, dtype=float)
    out = []
    for z_mesh in z_mesh_list:
        section, radii = None, []
        if solid_mesh is not None and solid_mesh.n_points:
            try:
                cross = solid_mesh.slice(normal=axis, origin=float(z_mesh) * axis)
            except Exception:
                cross = None
            if cross is not None and cross.n_points:
                # extract_surface(): connectivity() can hand back an UnstructuredGrid; the
                # viewport needs line-celled PolyData to render the loop (RegionId and the
                # line cells both survive the conversion).
                labeled = cross.connectivity(extraction_mode="all").extract_surface(algorithm=None)
                region_ids = labeled.point_data.get("RegionId")
                if region_ids is not None and len(region_ids):
                    for rid in np.unique(region_ids):
                        pts = labeled.points[region_ids == rid]
                        rel = pts - axis_point
                        radial = rel - np.outer(rel @ axis, axis)
                        radii.append(float(np.linalg.norm(radial, axis=1).max()))
                    radii.sort(reverse=True)
                    section = labeled
        out.append((section, radii))
    return out


def _dome_cap_samples(stations_proj, proj_min, proj_max, n=8):
    """Extra slice z-values (mesh frame, aligned to `_station_cross_sections`'s convention)
    filling the two station-free end bands of the rebuilt solid, from the outermost real
    station on each side out to that side's true axial extreme. Returns (fore_list, aft_list),
    each ordered from the station toward its tip; empty when there's no meaningful band (or no
    stations at all).

    Sine-clustered toward the tip, not uniform: the analytic dome fit that owns these bands
    differs most from any flat/linear assumption exactly where curvature peaks -- at the apex --
    so that's where the eye needs the densest evidence that the built surface really converges.
    The tip-most sample stops a hair short of the extreme (`tip_eps`): a slice plane exactly
    tangent to the apex yields a degenerate/empty cross-section, and the meridian curves close
    the last fraction of a millimetre to the true tip point themselves (see
    `_meridian_polylines` in app/viewport.py)."""
    if not stations_proj:
        return [], []
    span = float(proj_max) - float(proj_min)
    lo, hi = float(min(stations_proj)), float(max(stations_proj))
    out = []
    for tip, station_edge in ((float(proj_min), lo), (float(proj_max), hi)):
        band = abs(station_edge - tip)
        if band <= max(1e-9, 1e-4 * abs(span)):
            out.append([])
            continue
        toward_tip = 1.0 if tip > station_edge else -1.0
        tip_eps = max(2e-3 * band, 1e-9)
        f_max = 1.0 - tip_eps / band
        zs = [station_edge + toward_tip * band * min(math.sin(0.5 * math.pi * k / n), f_max)
              for k in range(1, n + 1)]
        out.append(zs)
    return out[0], out[1]


def _station_rows(report: dict, stations_z_mm, events_z_set, solid_mesh, axis_unit,
                  axis_point=None, sections=None) -> list:
    """Honest per-station diagnostics (G3 visual review #2, item 1): slice the rebuilt solid
    preview mesh EXACTLY at each station plane (instead of the old band-around-Z point sample,
    which could miss the outer loop entirely on a sparse mesh and misreport a bore radius as
    R_outer -- Fable caught this at z=5302 in review #2), then classify every connected loop in
    that cross-section by its own max radius: the largest loop is R_outer, the next-largest (if
    any) is R_bore. `report["axial_origin_z"]` (added alongside this fix) re-aligns report-frame
    Z, which is relative to the fore-dome apex, with the exported mesh's own Z; stations_z_mm
    itself already carries that offset. `stations_z_mm`/`events_z_set` are in report frame.
    `sections` (optional): precomputed `_station_cross_sections` output aligned to
    `stations_z_mm`, so `_on_rebuilt` slices once for both this table and the 3D ring layer.
    Returns rows of (index, z_mm, n_loops, r_outer_mm_or_None, r_bore_mm_or_None,
    classification, is_event_bool)."""
    axial_origin_z = (report or {}).get("axial_origin_z") or 0.0
    if sections is None:
        sections = _station_cross_sections(
            solid_mesh, [z + axial_origin_z for z in stations_z_mm], axis_unit, axis_point)

    prelim = []  # (i, z, n_loops, r_outer, r_bore, is_event)
    for i, (z, (_section, radii)) in enumerate(zip(stations_z_mm, sections)):
        n_loops = len(radii)
        r_outer = radii[0] if radii else None
        r_bore = radii[1] if len(radii) > 1 else None
        prelim.append((i, z, n_loops, r_outer, r_bore, round(z, 6) in events_z_set))

    max_outer = max((r[3] for r in prelim if r[3] is not None), default=None)
    rows = []
    for i, z, n_loops, r_outer, r_bore, is_event in prelim:
        if is_event:
            cls = "transition"
        elif r_outer is None:
            cls = "—"
        elif max_outer and r_outer < 0.98 * max_outer:
            cls = "dome"
        else:
            cls = "barrel"
        rows.append((i + 1, z, n_loops, r_outer, r_bore, cls, is_event))
    return rows
