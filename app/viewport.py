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
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

try:
    from pyvistaqt import QtInteractor
except Exception:  # pragma: no cover - PySide6/pyvistaqt always installed per WORK_SETUP
    QtInteractor = None


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
        self.reset_scene()

    def reset_scene(self):
        self.plotter.clear()
        self.plotter.set_background("#1e1e1e")
        self._input_actor = None
        self._solid_actor = None
        self._add_axis_triad()
        self.render()

    def _add_axis_triad(self):
        try:
            self.plotter.add_axes(interactive=False, color="white")
        except Exception:
            pass

    def show_input_mesh(self, mesh: pv.PolyData):
        if self._input_actor is not None:
            self.plotter.remove_actor(self._input_actor)
        self._input_actor = self.plotter.add_mesh(
            mesh, color="lightsteelblue", opacity=0.35, show_edges=False, name="input_mesh")
        self.plotter.reset_camera()
        self.render()

    def show_solid_mesh(self, mesh: pv.PolyData):
        if self._solid_actor is not None:
            self.plotter.remove_actor(self._solid_actor)
        self._solid_actor = self.plotter.add_mesh(
            mesh, color="#3f9fdc", opacity=1.0, show_edges=True, edge_color="#0d3a57", name="solid_mesh")
        self.plotter.reset_camera()
        self.render()

    def show_station_planes(self, stations_z_mm, bounds_xy_mm, axis_unit=(0, 0, 1)):
        """`stations_z_mm`: axial coordinates along the reconstruction axis (mm).
        `bounds_xy_mm`: half-width used to size each plane's disc."""
        self.plotter.remove_actor("station_planes", render=False)
        if not stations_z_mm:
            self.render()
            return
        radius = max(bounds_xy_mm, 1.0) * 1.05
        normal = np.array(axis_unit, dtype=float)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        planes = pv.MultiBlock()
        for z in stations_z_mm:
            center = normal * float(z)
            planes.append(pv.Disc(center=center, inner=0, outer=radius, normal=normal, r_res=1, c_res=32))
        merged = planes.combine()
        self.plotter.add_mesh(merged, color="yellow", opacity=0.12, name="station_planes")
        self.render()

    def show_topology_events(self, events_z_mm, bounds_xy_mm, axis_unit=(0, 0, 1)):
        self.plotter.remove_actor("topology_events", render=False)
        if not events_z_mm:
            self.render()
            return
        radius = max(bounds_xy_mm, 1.0) * 1.1
        normal = np.array(axis_unit, dtype=float)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        rings = pv.MultiBlock()
        for z in events_z_mm:
            center = normal * float(z)
            rings.append(pv.Disc(center=center, inner=radius * 0.97, outer=radius, normal=normal, r_res=1, c_res=48))
        merged = rings.combine()
        self.plotter.add_mesh(merged, color="red", opacity=0.9, name="topology_events")
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
