"""Main application window. MISSION §12/G2 layout: left Outline dock (Input -> Detected ->
Stations -> Output), Details dock for the selected node, central 3D viewport, bottom Log dock,
status bar with progress + cancel."""
import json
import os

import numpy as np
import pyvista as pv
import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QStackedWidget, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app import manifest as manifest_mod
from app.theme import ACCENT, ERROR, SUCCESS, TEXT_SECONDARY
from app.viewport import Viewport
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

        self._build_outline_dock()
        self._build_details_dock()
        self._build_log_dock()
        self._build_status_bar()

        self.outline.currentItemChanged.connect(self._on_outline_selection)
        self.outline.setCurrentItem(self.node_input)

    # ---- docks ---------------------------------------------------------
    def _build_outline_dock(self):
        dock = QDockWidget("Outline", self)
        dock.setObjectName("outline_dock")
        self.outline = QTreeWidget()
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
        self.details_stack = QStackedWidget()
        self.page_input = self._build_input_page()
        self.page_detected = self._build_readonly_page()
        self.page_stations = self._build_readonly_page()
        self.page_output = self._build_output_page()
        for page in (self.page_input, self.page_detected, self.page_stations, self.page_output):
            self.details_stack.addWidget(page)
        dock.setWidget(self.details_stack)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _build_readonly_page(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        text = QTextEdit()
        text.setReadOnly(True)
        layout.addWidget(text)
        w.text = text
        return w

    def _build_input_page(self):
        w = QWidget()
        form = QFormLayout(w)

        self.input_path_edit = QLineEdit()
        browse_row = QWidget()
        row_layout = QHBoxLayout(browse_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.input_path_edit)
        browse_btn = QPushButton(qta.icon("fa5s.folder-open", color=TEXT_SECONDARY), "Browse...")
        browse_btn.clicked.connect(self._browse_input)
        row_layout.addWidget(browse_btn)
        form.addRow("Input STL", browse_row)

        self.output_path_edit = QLineEdit("out/rebuilt.step")
        out_row = QWidget()
        out_layout = QHBoxLayout(out_row)
        out_layout.setContentsMargins(0, 0, 0, 0)
        out_layout.addWidget(self.output_path_edit)
        out_browse_btn = QPushButton(qta.icon("fa5s.folder-open", color=TEXT_SECONDARY), "Browse...")
        out_browse_btn.clicked.connect(self._browse_output)
        out_layout.addWidget(out_browse_btn)
        form.addRow("Output STEP", out_row)

        self.axis_combo = QComboBox()
        self.axis_combo.addItems(["auto", "x", "y", "z"])
        form.addRow("Axis", self.axis_combo)

        self.units_combo = QComboBox()
        self.units_combo.addItems(["mm", "in", "m"])
        form.addRow("Units", self.units_combo)

        self.sections_spin = QSpinBox()
        self.sections_spin.setRange(4, 500)
        self.sections_spin.setValue(40)
        form.addRow("Sections", self.sections_spin)

        self.chord_tol_auto = QCheckBox("auto from mesh")
        self.chord_tol_auto.setChecked(True)
        self.chord_tol_spin = QDoubleSpinBox()
        self.chord_tol_spin.setRange(0.01, 100.0)
        self.chord_tol_spin.setValue(0.5)
        self.chord_tol_spin.setEnabled(False)
        self.chord_tol_auto.toggled.connect(lambda on: self.chord_tol_spin.setEnabled(not on))
        chord_row = QWidget()
        chord_layout = QHBoxLayout(chord_row)
        chord_layout.setContentsMargins(0, 0, 0, 0)
        chord_layout.addWidget(self.chord_tol_spin)
        chord_layout.addWidget(self.chord_tol_auto)
        form.addRow("Chord tol (mm)", chord_row)

        self.adaptive_check = QCheckBox("adaptive stations")
        form.addRow("", self.adaptive_check)

        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        self.analyze_btn = QPushButton(qta.icon("fa5s.search", color=TEXT_SECONDARY), "Analyze")
        self.analyze_btn.clicked.connect(self.run_analyze)
        self.run_btn = QPushButton(qta.icon("fa5s.play", color="#ffffff"), "Run")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run_rebuild)
        self.cancel_btn = QPushButton(qta.icon("fa5s.stop", color=ERROR), "Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_rebuild)
        btn_layout.addWidget(self.analyze_btn)
        btn_layout.addWidget(self.run_btn)
        btn_layout.addWidget(self.cancel_btn)
        form.addRow(btn_row)

        return w

    def _build_output_page(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.error_banner = QLabel()
        self.error_banner.setObjectName("errorBanner")
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        layout.addWidget(self.error_banner)
        self.manifest_text = QTextEdit()
        self.manifest_text.setReadOnly(True)
        layout.addWidget(self.manifest_text)
        reveal_btn = QPushButton(qta.icon("fa5s.external-link-alt", color=TEXT_SECONDARY), "Reveal file")
        reveal_btn.clicked.connect(self._reveal_output)
        layout.addWidget(reveal_btn)
        return w

    def _build_log_dock(self):
        dock = QDockWidget("Log", self)
        dock.setObjectName("log_dock")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        dock.setWidget(self.log)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def _build_status_bar(self):
        bar = self.statusBar()
        self.status_label = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setMaximumWidth(240)
        bar.addWidget(self.status_label, 1)
        bar.addPermanentWidget(self.progress_bar)

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
    def log_line(self, text: str):
        self.log.appendPlainText(text)

    # ---- file pickers -------------------------------------------------
    def _browse_input(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select input STL", "", "STL files (*.stl)")
        if path:
            self.input_path_edit.setText(path)

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Select output STEP", "", "STEP files (*.step)")
        if path:
            self.output_path_edit.setText(path)

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
        self.log_line(f"analyze: {input_path} (axis={axis}, units={units})")
        worker = AnalyzeWorker(input_path, axis, units)
        worker.finished.connect(self._on_analyzed)
        worker.failed.connect(self._on_failed)
        thread = run_in_thread(worker)
        self._threads.append(thread)
        thread.start()

    def _on_analyzed(self, analysis):
        self._analysis = analysis
        self.status_label.setText("Analyzed")
        self.log_line(f"analyze done: axis={analysis.frame_axis}, extent={analysis.axial_extent_mm:.2f}mm, "
                      f"bodies={analysis.body_count}, watertight={analysis.is_watertight}")
        self.page_detected.text.setPlainText(json.dumps(_analysis_to_dict(analysis), indent=2))
        if not self.chord_tol_auto.isChecked():
            pass
        else:
            self.chord_tol_spin.setValue(max(analysis.suggested_chord_tol_mm, 0.01))
        try:
            mesh = pv.read(self.input_path_edit.text().strip())
            self.viewport.show_input_mesh(mesh)
        except Exception as exc:
            self.log_line(f"viewport: could not load input mesh preview: {exc}")
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
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
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

    def _on_progress(self, stage, frac, message):
        self.progress_bar.setValue(int(max(0.0, min(1.0, frac)) * 100))
        self.status_label.setText(f"{stage}: {message}")
        self.log_line(f"[{stage}] {frac:.2f} {message}")

    def _on_rebuilt(self, result):
        self._result = result
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.error_banner.hide()
        self.status_label.setText("Done")
        self.log_line(f"rebuild done: {result.output_path}")
        man = manifest_mod.build(result, self._analysis)
        self.manifest_text.setPlainText(json.dumps(man, indent=2))
        report = result.report or {}
        self.page_stations.text.setPlainText(json.dumps(
            {"n_stations": report.get("n_stations"), "stations_z_mm": report.get("stations_z_mm", [])},
            indent=2))
        try:
            if result.stl_path and os.path.exists(result.stl_path):
                solid_mesh = pv.read(result.stl_path)
                self.viewport.show_solid_mesh(solid_mesh)
                xmin, xmax, ymin, ymax, _zmin, _zmax = solid_mesh.bounds
                bounds_xy = float(max(abs(xmin), abs(xmax), abs(ymin), abs(ymax)))
                axis_unit = self._analysis.frame_axis if self._analysis else (0, 0, 1)
                self.viewport.show_station_planes(report.get("stations_z_mm", []), bounds_xy, axis_unit)
                self.viewport.show_topology_events(report.get("topology_events_z_mm", []), bounds_xy, axis_unit)
        except Exception as exc:
            self.log_line(f"viewport: could not load solid preview: {exc}")
        self.outline.setCurrentItem(self.node_output)

    def _on_failed(self, kind, message):
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(f"Failed: {kind}")
        self.log_line(f"FAILED [{kind}] {message}")
        self.error_banner.setText(f"{kind}: {message}")
        self.error_banner.show()
        self.manifest_text.setPlainText(json.dumps({"ok": False, "error": kind, "message": message}, indent=2))
        self.outline.setCurrentItem(self.node_output)


def _analysis_to_dict(analysis) -> dict:
    return {
        "frame_axis": analysis.frame_axis,
        "origin_xy_mm": analysis.origin_xy_mm,
        "axial_extent_mm": analysis.axial_extent_mm,
        "body_count": analysis.body_count,
        "is_watertight": analysis.is_watertight,
        "triangle_count": analysis.triangle_count,
        "median_edge_length_mm": analysis.median_edge_length_mm,
        "suggested_chord_tol_mm": analysis.suggested_chord_tol_mm,
        "axis_confidence": analysis.axis_confidence,
        "units": analysis.units,
        "n_dropped_islands": analysis.n_dropped_islands,
        "bounds_mm": analysis.bounds_mm,
    }
