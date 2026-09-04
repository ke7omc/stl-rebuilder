"""Main application window. MISSION §12/G2 layout: left Outline dock (Input -> Detected ->
Stations -> Output), Details dock for the selected node, central 3D viewport, bottom Log dock,
status bar with progress + cancel."""
import datetime
import html
import os

import numpy as np
import pyvista as pv
import qtawesome as qta
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QSpinBox, QStackedWidget, QTextEdit, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app import manifest as manifest_mod
from app.theme import ACCENT, ERROR, SUCCESS, TEXT_DISABLED, TEXT_SECONDARY, WARNING
from app.viewport import Viewport
from app.widgets import BusySpinner, PropertyTree, StationTable, axis_label, fmt_bounds, fmt_num
from app.worker import AnalyzeWorker, RebuildWorker, run_in_thread
from pipeline.engine import RebuildOptions

NODE_INPUT, NODE_DETECTED, NODE_STATIONS, NODE_OUTPUT = "Input", "Detected", "Stations", "Output"


class MainWindow(QMainWindow):
    def __init__(self, offscreen: bool = False):
        super().__init__()
        self.setWindowTitle("stl-rebuilder")
        self.setWindowIcon(qta.icon("fa5s.cube", color=ACCENT))
        self.resize(1400, 900)

        self._analysis = None
        self._result = None
        self._threads = []  # keep QThread refs alive until done

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
        open_action = QAction(qta.icon("fa5s.folder-open", color=TEXT_SECONDARY), "&Open STL...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._browse_input)
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        view_menu = menubar.addMenu("&View")
        self._layer_actions = {}
        for key, label in (("input", "Input mesh"), ("solid", "Rebuilt solid"),
                           ("stations", "Station rings"), ("events", "Topology events"),
                           ("axis", "Axis line")):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(True)
            act.toggled.connect(lambda on, k=key: self.viewport.set_layer_visible(k, on))
            view_menu.addAction(act)
            self._layer_actions[key] = act
        view_menu.addSeparator()
        self.swap_action = QAction("Swap input ↔ rebuilt", self)
        self.swap_action.setCheckable(True)
        self.swap_action.setShortcut("B")
        self.swap_action.setToolTip("A/B compare: show the input mesh near-opaque, hide the rebuilt solid")
        self.swap_action.toggled.connect(self.viewport.set_swap)
        view_menu.addAction(self.swap_action)

        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About stl-rebuilder", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        toolbar = QToolBar("Main", self)
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        self.analyze_action = QAction(qta.icon("fa5s.search", color=TEXT_SECONDARY), "Analyze", self)
        self.analyze_action.triggered.connect(self.run_analyze)
        self.run_action = QAction(qta.icon("fa5s.play", color=ACCENT), "Run", self)
        self.run_action.triggered.connect(self.run_rebuild)
        self.cancel_action = QAction(qta.icon("fa5s.stop", color=ERROR), "Cancel", self)
        self.cancel_action.setEnabled(False)
        self.cancel_action.triggered.connect(self.cancel_rebuild)
        toolbar.addAction(open_action)
        toolbar.addSeparator()
        toolbar.addAction(self.analyze_action)
        toolbar.addAction(self.run_action)
        toolbar.addAction(self.cancel_action)
        self.addToolBar(toolbar)

    def _show_about(self):
        QMessageBox.about(
            self, "About stl-rebuilder",
            "stl-rebuilder\nBurnback-surface STL -> BRep STEP converter.\n"
            "See MISSION.md for the geometry pipeline spec.")

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
        self.node_input.setIcon(0, qta.icon("fa5s.file-import", color=TEXT_SECONDARY))
        self.node_detected.setIcon(0, qta.icon("fa5s.search-location", color=TEXT_SECONDARY))
        self.node_stations.setIcon(0, qta.icon("fa5s.layer-group", color=TEXT_SECONDARY))
        self.node_output.setIcon(0, qta.icon("fa5s.cube", color=TEXT_SECONDARY))
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
        table = StationTable()
        layout.addWidget(table)
        w.table = table
        return w

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
        browse_row = QWidget()
        row_layout = QHBoxLayout(browse_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.input_path_edit)
        browse_btn = QPushButton(qta.icon("fa5s.folder-open", color=TEXT_SECONDARY), "Browse...")
        browse_btn.clicked.connect(self._browse_input)
        row_layout.addWidget(browse_btn)
        form.addRow("Input STL", browse_row)

        self.output_path_edit = QLineEdit("out/rebuilt.step")
        self.output_path_edit.setMinimumWidth(220)
        out_row = QWidget()
        out_layout = QHBoxLayout(out_row)
        out_layout.setContentsMargins(0, 0, 0, 0)
        out_layout.addWidget(self.output_path_edit)
        out_browse_btn = QPushButton(qta.icon("fa5s.folder-open", color=TEXT_SECONDARY), "Browse...")
        out_browse_btn.clicked.connect(self._browse_output)
        out_layout.addWidget(out_browse_btn)
        form.addRow("Output STEP", out_row)

        form.addRow(self._section_label("Geometry"))

        self.axis_combo = QComboBox()
        self.axis_combo.addItems(["auto", "x", "y", "z"])
        form.addRow("Axis", self.axis_combo)

        self.units_combo = QComboBox()
        self.units_combo.addItems(["mm", "in", "m"])
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

        self.adaptive_check = QCheckBox("adaptive stations")
        form.addRow("", self.adaptive_check)

        outer.addStretch(1)

        form2 = QFormLayout()
        form2.addRow(self._section_label("Actions"))
        outer.addLayout(form2)

        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)
        self.analyze_btn = QPushButton(qta.icon("fa5s.search", color=TEXT_SECONDARY), "Analyze")
        self.analyze_btn.clicked.connect(self.run_analyze)
        self.run_btn = QPushButton(qta.icon("fa5s.play", color="#ffffff"), "Run")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run_rebuild)
        self.cancel_btn = QPushButton(qta.icon("fa5s.stop", color=TEXT_DISABLED), "Cancel")
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
        reveal_btn = QPushButton(qta.icon("fa5s.external-link-alt", color=TEXT_SECONDARY), "Reveal file")
        reveal_btn.clicked.connect(self._reveal_output)
        layout.addWidget(reveal_btn)
        return w

    def _build_log_dock(self):
        dock = QDockWidget("Log", self)
        dock.setObjectName("log_dock")
        self.log = QTextEdit()
        self.log.setObjectName("logConsole")
        self.log.setReadOnly(True)
        dock.setWidget(self.log)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def _build_status_bar(self):
        bar = self.statusBar()
        self.status_label = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setMaximumWidth(240)
        self.progress_bar.hide()  # G3 review #2 item 3: only visible during an active run
        self.spinner = BusySpinner()
        bar.addWidget(self.status_label, 1)
        bar.addPermanentWidget(self.progress_bar)
        bar.addPermanentWidget(self.spinner)

    # ---- outline selection ----------------------------------------------
    def _on_outline_selection(self, current, _previous):
        if current is None:
            return
        page = {
            NODE_INPUT: self.page_input,
            NODE_DETECTED: self.page_detected,
            NODE_STATIONS: self.page_stations,
            NODE_OUTPUT: self.page_output,
        }[current.text(0)]
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

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Select output STEP", "", "STEP files (*.step)")
        if path:
            self._set_path_field(self.output_path_edit, path)

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
        self.log_line(f"analyze: {input_path} (axis={axis}, units={units})")
        worker = AnalyzeWorker(input_path, axis, units)
        worker.finished.connect(self._on_analyzed)
        worker.failed.connect(self._on_failed)
        thread = run_in_thread(worker)
        self._threads.append(thread)
        thread.start()

    def _on_analyzed(self, analysis):
        self._analysis = analysis
        self.spinner.stop()
        self.status_label.setText("Analyzed")
        self.log_line(f"analyze done: axis={analysis.frame_axis}, extent={analysis.axial_extent_mm:.2f}mm, "
                      f"bodies={analysis.body_count}, watertight={analysis.is_watertight}")
        self.page_detected.tree.set_groups(_analysis_property_groups(analysis))
        if self.chord_tol_auto.isChecked():
            self.chord_tol_spin.setValue(max(analysis.suggested_chord_tol_mm, 0.01))
        try:
            mesh = pv.read(self.input_path_edit.text().strip())
            self.viewport.show_input_mesh(mesh)
        except Exception as exc:
            self.log_line(f"viewport: could not load input mesh preview: {exc}", level="warn")
        self.outline.setCurrentItem(self.node_detected)

    def run_rebuild(self):
        input_path = self.input_path_edit.text().strip()
        output_path = self.output_path_edit.text().strip()
        if not input_path or not output_path:
            QMessageBox.warning(self, "Run", "Input STL and output STEP path are required.")
            return
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        chord_tol = (self._analysis.suggested_chord_tol_mm if (self.chord_tol_auto.isChecked() and self._analysis)
                     else self.chord_tol_spin.value())
        report_path = os.path.splitext(output_path)[0] + ".report.json"
        stl_preview_path = os.path.splitext(output_path)[0] + ".preview.stl"
        opts = RebuildOptions(
            input_stl=input_path, output=output_path, axis=self.axis_combo.currentText(),
            units=self.units_combo.currentText(), sections=self.sections_spin.value(),
            adaptive=self.adaptive_check.isChecked(), chord_tol=chord_tol,
            report=report_path, stl=stl_preview_path,
        )
        self.status_label.setText("Running...")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.spinner.start()
        self._set_running(True)
        self.log_line(f"rebuild: {input_path} -> {output_path} (sections={opts.sections}, "
                      f"chord_tol={opts.chord_tol:.3f}, adaptive={opts.adaptive})")
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
        self.run_btn.setEnabled(not running)
        self.run_action.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.cancel_action.setEnabled(running)
        self.cancel_btn.setIcon(qta.icon("fa5s.stop", color=ERROR if running else TEXT_DISABLED))

    def _on_progress(self, stage, frac, message):
        self.progress_bar.setValue(int(max(0.0, min(1.0, frac)) * 100))
        self.status_label.setText(f"{stage}: {message}")
        self.log_line(f"[{stage}] {frac:.2f} {message}")

    def _on_rebuilt(self, result):
        self._result = result
        self._set_running(False)
        self.progress_bar.hide()
        self.spinner.stop()
        self.error_banner.hide()
        self.status_label.setText("Done")
        self.log_line(f"rebuild done: {result.output_path}")
        man = manifest_mod.build(result, self._analysis)
        self.manifest_tree.set_groups(_manifest_property_groups(man))
        report = result.report or {}
        stations_z = report.get("stations_z_mm", [])
        events_z = set(round(z, 6) for z in report.get("topology_events_z_mm", []))
        solid_mesh = None
        try:
            # Run without a prior Analyze: the input mesh was never loaded into the scene,
            # which left the Input-mesh layer (and B-swap) empty (Brady, 2026-09-04)
            if not self.viewport.has_input_mesh:
                in_path = self.input_path_edit.text().strip()
                if in_path and os.path.exists(in_path):
                    self.viewport.show_input_mesh(pv.read(in_path))
            if result.stl_path and os.path.exists(result.stl_path):
                solid_mesh = pv.read(result.stl_path)
                self.viewport.show_solid_mesh(solid_mesh)
                xmin, xmax, ymin, ymax, _zmin, _zmax = solid_mesh.bounds
                bounds_xy = float(max(abs(xmin), abs(xmax), abs(ymin), abs(ymax)))
                axis_unit = self._analysis.frame_axis if self._analysis else (0, 0, 1)
                self.viewport.show_station_planes(stations_z, bounds_xy, axis_unit)
                self.viewport.show_topology_events(list(events_z), bounds_xy, axis_unit)
                self.viewport.show_axis_line(bounds_xy, report.get("axial_extent_mm", bounds_xy), axis_unit)
        except Exception as exc:
            self.log_line(f"viewport: could not load solid preview: {exc}", level="warn")
        self.page_stations.table.set_rows(
            _station_rows(report, stations_z, events_z, solid_mesh,
                           self._analysis.frame_axis if self._analysis else (0, 0, 1)))
        self.outline.setCurrentItem(self.node_output)

    def _on_failed(self, kind, message):
        self._set_running(False)
        self.progress_bar.hide()
        self.spinner.stop()
        self.status_label.setText(f"Failed: {kind}")
        self.log_line(f"FAILED [{kind}] {message}", level="error")
        self.error_banner.setText(f"{kind}: {message}")
        self.error_banner.show()
        self.manifest_tree.set_groups([("Error", [("Kind", kind, None), ("Message", message, message)])])
        self.outline.setCurrentItem(self.node_output)


def _analysis_property_groups(analysis) -> list:
    return [
        ("Frame", [
            ("Axis", f"{axis_label(analysis.frame_axis)} (confidence {analysis.axis_confidence * 100:.0f} %)",
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


def _verification_group(verif: dict):
    """The Output page's "Verification" group (engine-computed `report["verification"]`,
    input mesh vs produced solid): one row per check, value text leading with a colored glyph
    followed by the two measured values, the delta, and the tolerance in-line."""
    rows = []
    vol = verif.get("volume")
    if vol:
        glyph, color = _verification_glyph(vol.get("pass"))
        if vol.get("pass") is None or vol.get("input_mm3") is None:
            text = f"{glyph}  {vol.get('note', 'volume comparison unavailable')}"
        else:
            text = (f"{glyph}  {fmt_num(vol['input_mm3'] / 1e6, 3)} L vs "
                    f"{fmt_num(vol['solid_mm3'] / 1e6, 3)} L "
                    f"(Δ {vol['delta_pct']:.4g} % ≤ {vol['tol_pct']:g} %)")
        rows.append(("Volume", text, str(vol), color))
    for ax in ("x", "y", "z"):
        b = (verif.get("bounds") or {}).get(ax)
        if not b:
            continue
        glyph, color = _verification_glyph(b.get("pass"))
        text = (f"{glyph}  {ax.upper()}: {fmt_num(b['input_mm'][0])}…{fmt_num(b['input_mm'][1])}"
                f" vs {fmt_num(b['solid_mm'][0])}…{fmt_num(b['solid_mm'][1])} mm "
                f"(max Δ {b['max_dev_mm']:.3g} ≤ {b['tol_mm']:.3g} mm)")
        rows.append((f"Bounds {ax.upper()}", text, str(b), color))
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
        rows.append(("Deviation", text, str(dev), color))
    if "error" in verif:
        glyph, color = _verification_glyph(None)
        rows.append(("Note", f"{glyph}  verification incomplete: {verif['error']}",
                     verif["error"], color))
    return ("Verification", rows)


def _manifest_property_groups(man: dict) -> list:
    groups = []
    verif = man.get("verification")
    if verif:
        groups.append(_verification_group(verif))
    groups += [
        ("Output", [
            ("STEP file", man.get("output_path", "-"), man.get("output_path")),
            ("Preview STL", man.get("stl_path") or "-", man.get("stl_path")),
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


def _station_rows(report: dict, stations_z_mm, events_z_set, solid_mesh, axis_unit) -> list:
    """Honest per-station diagnostics (G3 visual review #2, item 1): slice the rebuilt solid
    preview mesh EXACTLY at each station plane (instead of the old band-around-Z point sample,
    which could miss the outer loop entirely on a sparse mesh and misreport a bore radius as
    R_outer -- Fable caught this at z=5302 in review #2), then classify every connected loop in
    that cross-section by its own max radius: the largest loop is R_outer, the next-largest (if
    any) is R_bore. `report["axial_origin_z"]` (added alongside this fix) re-aligns report-frame
    Z, which is relative to the fore-dome apex, with the exported mesh's own Z; stations_z_mm
    itself already carries that offset. `stations_z_mm`/`events_z_set` are in report frame.
    Returns rows of (index, z_mm, n_loops, r_outer_mm_or_None, r_bore_mm_or_None,
    classification, is_event_bool)."""
    axis = np.array(axis_unit, dtype=float)
    axis = axis / (np.linalg.norm(axis) or 1.0)
    axial_origin_z = (report or {}).get("axial_origin_z") or 0.0

    prelim = []  # (i, z, n_loops, r_outer, r_bore, is_event)
    for i, z in enumerate(stations_z_mm):
        n_loops, r_outer, r_bore = 0, None, None
        if solid_mesh is not None and solid_mesh.n_points:
            z_mesh = z + axial_origin_z
            try:
                cross = solid_mesh.slice(normal=axis, origin=z_mesh * axis)
            except Exception:
                cross = None
            if cross is not None and cross.n_points:
                labeled = cross.connectivity(extraction_mode="all")
                region_ids = labeled.point_data.get("RegionId")
                if region_ids is not None and len(region_ids):
                    radii = []
                    for rid in np.unique(region_ids):
                        pts = labeled.points[region_ids == rid]
                        radial = pts - np.outer(pts @ axis, axis)
                        radii.append(float(np.linalg.norm(radial, axis=1).max()))
                    radii.sort(reverse=True)
                    n_loops = len(radii)
                    r_outer = radii[0]
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
