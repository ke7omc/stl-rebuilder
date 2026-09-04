"""3D viewport widget. MISSION §12/G2: input STL translucent over the rebuilt solid, station
planes, topology-event markers, axis triad.

`QOpenGLWidget` (what pyvistaqt's `QtInteractor` embeds) cannot create a GL context under
`QT_QPA_PLATFORM=offscreen` on every platform (confirmed: fails on macOS here). Plain
`pyvista.Plotter(off_screen=True)` renders fine everywhere (VTK's own offscreen context, no Qt
GL widget involved) -- so `Viewport` uses a real `QtInteractor` normally, and in `offscreen=True`
mode (only ever set by `app.smoke`) swaps in an off-screen `Plotter` composited into a `QLabel`.
Both are `pyvista.Plotter` subclasses/instances, so all scene-building code below is shared.

Layer model (added 2026-09-04, Brady's request): five named layers — input / solid / stations /
events / axis — each with persistent user-controlled visibility (View menu), plus a "swap" mode
that hides the rebuilt solid and shows the input mesh near-opaque for A/B comparison. ALL
station rings are drawn (an earlier build silently subsampled to 16, which made the ring count
contradict the report's n_stations — dishonest, removed).
"""
import numpy as np
import pyvista as pv
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.theme import TEXT_SECONDARY

try:
    from pyvistaqt import QtInteractor
except Exception:  # pragma: no cover - PySide6/pyvistaqt always installed per WORK_SETUP
    QtInteractor = None

INPUT_MESH_COLOR = "lightsteelblue"
INPUT_MESH_OPACITY_ONLY = 0.35
INPUT_MESH_OPACITY_OVER_SOLID = 0.10
INPUT_MESH_OPACITY_SWAP = 0.9
SOLID_MESH_COLOR = "#3f9fdc"
STATION_RING_COLOR = "#f2c94c"
EVENT_RING_COLOR = "#e5534b"

EMPTY_HINT_TEXT = "Open a burnback STL to begin — File ▸ Open or the Input panel"

LAYERS = ("input", "solid", "stations", "events", "axis")


class Viewport(QWidget):
    def __init__(self, parent=None, offscreen: bool = False):
        super().__init__(parent)
        self._offscreen = offscreen or QtInteractor is None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if self._offscreen:
            self.plotter = pv.Plotter(off_screen=True, window_size=(900, 700))
            self._label = QLabel("3D viewport (offscreen)")
            self._label.setScaledContents(True)
            layout.addWidget(self._label)
        else:
            self.plotter = QtInteractor(self)
            layout.addWidget(self.plotter.interactor if hasattr(self.plotter, "interactor") else self.plotter)
        self._input_actor = None
        self._solid_actor = None
        self._station_actor = None
        self._event_actor = None
        self._axis_actor = None
        self._has_events = False
        # user preferences survive scene resets (a new run should respect the View menu)
        self._layer_visible = {k: True for k in LAYERS}
        self._swap = False

        self._empty_hint = QLabel(EMPTY_HINT_TEXT, self)
        self._empty_hint.setObjectName("emptyHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setWordWrap(True)
        self._empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # Qt overlay legend (replaces VTK's add_legend, whose text renders independent of its
        # box and clipped at every size we tried — 2026-09-04)
        self._legend = QLabel(self)
        self._legend.setObjectName("viewportLegend")
        self._legend.setStyleSheet(
            "background-color: rgba(20, 22, 26, 0.85); color: #d6dbe3; "
            "border: 1px solid #3a3f47; border-radius: 4px; padding: 6px 10px; font-size: 12px;")
        self._legend.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._legend.hide()

        self.reset_scene()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_hint()
        self._reposition_legend()

    def _reposition_hint(self):
        h = 60
        self._empty_hint.setGeometry(20, (self.height() - h) // 2, max(self.width() - 40, 0), h)
        self._empty_hint.raise_()

    def _reposition_legend(self):
        if self._legend.isVisible():
            self._legend.adjustSize()
            self._legend.move(max(self.width() - self._legend.width() - 14, 0), 12)
            self._legend.raise_()

    def reset_scene(self):
        self.plotter.clear()
        self.plotter.set_background("#1e1e1e")
        self._input_actor = None
        self._solid_actor = None
        self._station_actor = None
        self._event_actor = None
        self._axis_actor = None
        self._has_events = False
        self._legend.hide()
        self._add_axis_triad()
        self._empty_hint.show()
        self._reposition_hint()
        self.render()

    def _add_axis_triad(self):
        try:
            self.plotter.add_axes(interactive=False, color="white")
        except Exception:
            pass

    # ---- layer visibility (View menu / swap) -----------------------------
    def set_layer_visible(self, layer: str, on: bool):
        if layer in self._layer_visible:
            self._layer_visible[layer] = bool(on)
            self._apply_visibility()
            self.render()

    def set_swap(self, on: bool):
        """A/B comparison: hide the rebuilt solid, show the input mesh near-opaque."""
        self._swap = bool(on)
        self._apply_visibility()
        self.render()

    def _input_opacity(self) -> float:
        if self._swap:
            return INPUT_MESH_OPACITY_SWAP
        return INPUT_MESH_OPACITY_OVER_SOLID if self._solid_actor is not None else INPUT_MESH_OPACITY_ONLY

    def _apply_visibility(self):
        pairs = (("input", self._input_actor), ("solid", self._solid_actor),
                 ("stations", self._station_actor), ("events", self._event_actor),
                 ("axis", self._axis_actor))
        for key, actor in pairs:
            if actor is None:
                continue
            visible = self._layer_visible[key]
            if key == "solid" and self._swap:
                visible = False
            try:
                actor.SetVisibility(bool(visible))
            except Exception:
                pass
        if self._input_actor is not None:
            try:
                self._input_actor.GetProperty().SetOpacity(self._input_opacity())
            except Exception:
                pass

    # ---- scene building --------------------------------------------------
    def show_input_mesh(self, mesh: pv.PolyData):
        self._empty_hint.hide()
        if self._input_actor is not None:
            self.plotter.remove_actor(self._input_actor)
        self._input_actor = self.plotter.add_mesh(
            mesh, color=INPUT_MESH_COLOR, opacity=INPUT_MESH_OPACITY_ONLY, show_edges=False,
            name="input_mesh", label="Input mesh")
        self._apply_visibility()
        self.plotter.reset_camera()
        self.render()

    def show_solid_mesh(self, mesh: pv.PolyData):
        self._empty_hint.hide()
        if self._solid_actor is not None:
            self.plotter.remove_actor(self._solid_actor)
        self._solid_actor = self.plotter.add_mesh(
            mesh, color=SOLID_MESH_COLOR, opacity=1.0, show_edges=True, edge_color="#0d3a57",
            name="solid_mesh", label="Rebuilt solid")
        # once the rebuilt solid is present, the input mesh fades to a reference overlay
        # (G3 review #4); _apply_visibility owns the opacity/visibility rules incl. swap.
        self._apply_visibility()
        self.plotter.reset_camera()
        self._update_legend()
        self.render()

    def _update_legend(self):
        entries = []
        if self._input_actor is not None:
            entries.append(("Input mesh", INPUT_MESH_COLOR))
        if self._solid_actor is not None:
            entries.append(("Rebuilt solid", SOLID_MESH_COLOR))
        if entries:
            entries.append(("Stations", STATION_RING_COLOR))
            if self._has_events:
                entries.append(("Topology change", EVENT_RING_COLOR))
        if not entries:
            self._legend.hide()
            return
        rows = "".join(
            f'<div><span style="color:{color}; font-size:14px;">■</span>&nbsp;{label}</div>'
            for label, color in entries)
        self._legend.setText(rows)
        self._legend.show()
        self._reposition_legend()

    def show_station_planes(self, stations_z_mm, bounds_xy_mm, axis_unit=(0, 0, 1)):
        """`stations_z_mm`: axial coordinates along the reconstruction axis (mm). Drawn as thin
        rings hugging the outer surface silhouette (not full discs, which the opaque solid
        occludes almost entirely -- G3 review #4). ALL stations are drawn — the ring count must
        match the report's n_stations (adaptive sets legitimately stack rings at features; the
        View menu can hide the layer if it reads busy)."""
        self.plotter.remove_actor("station_planes", render=False)
        self._station_actor = None
        if not stations_z_mm:
            self.render()
            return
        stations_sorted = sorted(stations_z_mm)
        radius = max(bounds_xy_mm, 1.0) * 1.02
        normal = np.array(axis_unit, dtype=float)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        rings = pv.MultiBlock()
        for z in stations_sorted:
            center = normal * float(z)
            rings.append(pv.Disc(center=center, inner=radius * 0.96, outer=radius, normal=normal, r_res=1, c_res=48))
        merged = rings.combine()
        self._station_actor = self.plotter.add_mesh(
            merged, color=STATION_RING_COLOR, opacity=0.85, name="station_planes")
        self._apply_visibility()
        self.render()

    def show_topology_events(self, events_z_mm, bounds_xy_mm, axis_unit=(0, 0, 1)):
        self.plotter.remove_actor("topology_events", render=False)
        self._event_actor = None
        self._has_events = bool(events_z_mm)
        if not events_z_mm:
            self._update_legend()
            self.render()
            return
        radius = max(bounds_xy_mm, 1.0) * 1.08
        normal = np.array(axis_unit, dtype=float)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        rings = pv.MultiBlock()
        for z in events_z_mm:
            center = normal * float(z)
            rings.append(pv.Disc(center=center, inner=radius * 0.97, outer=radius, normal=normal, r_res=1, c_res=48))
        merged = rings.combine()
        self._event_actor = self.plotter.add_mesh(
            merged, color=EVENT_RING_COLOR, opacity=0.9, name="topology_events")
        self._apply_visibility()
        self._update_legend()
        self.render()

    def show_axis_line(self, bounds_xy_mm, axial_extent_mm, axis_unit=(0, 0, 1), origin_z_mm=0.0):
        """Faint centerline through the detected reconstruction axis (G3 review #4)."""
        self.plotter.remove_actor("axis_line", render=False)
        normal = np.array(axis_unit, dtype=float)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        half = max(axial_extent_mm, bounds_xy_mm, 1.0) * 0.6
        p1 = normal * (origin_z_mm - half)
        p2 = normal * (origin_z_mm + half)
        line = pv.Line(p1, p2)
        self._axis_actor = self.plotter.add_mesh(
            line, color="#7a8290", opacity=0.5, line_width=1.5, name="axis_line")
        self._apply_visibility()
        self.render()

    def render(self):
        if self._offscreen:
            arr = self.plotter.screenshot(return_img=True)
            h, w = arr.shape[0], arr.shape[1]
            arr = np.ascontiguousarray(arr)
            img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888)
            self._label.setPixmap(QPixmap.fromImage(img.copy()))
            self._label.resize(w, h)
        else:
            self.plotter.render()

    def screenshot(self, path: str):
        if self._offscreen:
            self.plotter.screenshot(path)
        else:
            self.plotter.screenshot(path)
