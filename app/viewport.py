"""3D viewport widget. MISSION §12/G2: input STL translucent over the rebuilt solid, station
planes, topology-event markers, axis triad.

`QOpenGLWidget` (what pyvistaqt's `QtInteractor` embeds) cannot create a GL context under
`QT_QPA_PLATFORM=offscreen` on every platform (confirmed: fails on macOS here). Plain
`pyvista.Plotter(off_screen=True)` renders fine everywhere (VTK's own offscreen context, no Qt
GL widget involved) -- so `Viewport` uses a real `QtInteractor` normally, and in `offscreen=True`
mode (only ever set by `app.smoke`) swaps in an off-screen `Plotter` composited into a `QLabel`.
Both are `pyvista.Plotter` subclasses/instances, so all scene-building code below is shared.
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
SOLID_MESH_COLOR = "#3f9fdc"
STATION_RING_COLOR = "#f2c94c"
EVENT_RING_COLOR = "#e5534b"

EMPTY_HINT_TEXT = "Open a burnback STL to begin — File ▸ Open or the Input panel"


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

        self._empty_hint = QLabel(EMPTY_HINT_TEXT, self)
        self._empty_hint.setObjectName("emptyHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setWordWrap(True)
        self._empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.reset_scene()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_hint()

    def _reposition_hint(self):
        h = 60
        self._empty_hint.setGeometry(20, (self.height() - h) // 2, max(self.width() - 40, 0), h)
        self._empty_hint.raise_()

    def reset_scene(self):
        self.plotter.clear()
        self.plotter.set_background("#1e1e1e")
        self._input_actor = None
        self._solid_actor = None
        self._add_axis_triad()
        self._empty_hint.show()
        self._reposition_hint()
        self.render()

    def _add_axis_triad(self):
        try:
            self.plotter.add_axes(interactive=False, color="white")
        except Exception:
            pass

    def show_input_mesh(self, mesh: pv.PolyData):
        self._empty_hint.hide()
        if self._input_actor is not None:
            self.plotter.remove_actor(self._input_actor)
        self._input_actor = self.plotter.add_mesh(
            mesh, color=INPUT_MESH_COLOR, opacity=INPUT_MESH_OPACITY_ONLY, show_edges=False,
            name="input_mesh", label="Input mesh")
        self.plotter.reset_camera()
        self.render()

    def show_solid_mesh(self, mesh: pv.PolyData):
        self._empty_hint.hide()
        if self._solid_actor is not None:
            self.plotter.remove_actor(self._solid_actor)
        self._solid_actor = self.plotter.add_mesh(
            mesh, color=SOLID_MESH_COLOR, opacity=1.0, show_edges=True, edge_color="#0d3a57",
            name="solid_mesh", label="Rebuilt solid")
        if self._input_actor is not None:
            # once the rebuilt solid is present, fade the input mesh so it reads as a
            # reference overlay instead of a second, competing opaque body (G3 review #4).
            self._input_actor.GetProperty().SetOpacity(INPUT_MESH_OPACITY_OVER_SOLID)
        self.plotter.reset_camera()
        self._update_legend()
        self.render()

    def _update_legend(self):
        try:
            self.plotter.remove_legend(render=False)
        except Exception:
            pass
        entries = []
        if self._input_actor is not None:
            entries.append(["Input", INPUT_MESH_COLOR])
        if self._solid_actor is not None:
            entries.append(["Rebuilt", SOLID_MESH_COLOR])
        if entries:
            entries.append(["Stations", STATION_RING_COLOR])
            try:
                self.plotter.add_legend(entries, bcolor=(0.1, 0.1, 0.1), face="rectangle",
                                         size=(0.18, 0.05 * len(entries)))
            except Exception:
                pass

    MAX_DISPLAYED_STATION_RINGS = 16

    def show_station_planes(self, stations_z_mm, bounds_xy_mm, axis_unit=(0, 0, 1)):
        """`stations_z_mm`: axial coordinates along the reconstruction axis (mm). Drawn as thin
        rings hugging the outer surface silhouette (not full discs, which the opaque solid
        occludes almost entirely -- G3 review #4: "none are visible today"). Dense station
        sets are subsampled for display so the overlay reads as sectioning marks rather than a
        solid yellow wireframe."""
        self.plotter.remove_actor("station_planes", render=False)
        if not stations_z_mm:
            self.render()
            return
        stations_sorted = sorted(stations_z_mm)
        n = len(stations_sorted)
        if n > self.MAX_DISPLAYED_STATION_RINGS:
            step = n / self.MAX_DISPLAYED_STATION_RINGS
            idxs = sorted({int(i * step) for i in range(self.MAX_DISPLAYED_STATION_RINGS)} | {n - 1})
            stations_sorted = [stations_sorted[i] for i in idxs]
        radius = max(bounds_xy_mm, 1.0) * 1.02
        normal = np.array(axis_unit, dtype=float)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        rings = pv.MultiBlock()
        for z in stations_sorted:
            center = normal * float(z)
            rings.append(pv.Disc(center=center, inner=radius * 0.96, outer=radius, normal=normal, r_res=1, c_res=48))
        merged = rings.combine()
        self.plotter.add_mesh(merged, color=STATION_RING_COLOR, opacity=0.85, name="station_planes")
        self.render()

    def show_topology_events(self, events_z_mm, bounds_xy_mm, axis_unit=(0, 0, 1)):
        self.plotter.remove_actor("topology_events", render=False)
        if not events_z_mm:
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
        self.plotter.add_mesh(merged, color=EVENT_RING_COLOR, opacity=0.9, name="topology_events")
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
        self.plotter.add_mesh(line, color="#7a8290", opacity=0.5, line_width=1.5, name="axis_line")
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
