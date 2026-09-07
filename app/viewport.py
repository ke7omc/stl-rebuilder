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
import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from app.theme import MONO_FAMILY, TELEMETRY, TEXT_DISABLED, TEXT_PRIMARY

try:
    from pyvistaqt import QtInteractor
except Exception:  # pragma: no cover - PySide6/pyvistaqt always installed per WORK_SETUP
    QtInteractor = None

INPUT_MESH_COLOR = "lightsteelblue"
INPUT_MESH_OPACITY_ONLY = 0.35
INPUT_MESH_OPACITY_OVER_SOLID = 0.10
INPUT_MESH_OPACITY_SWAP = 0.9
# Brady, 2026-09-07 (live testing the design-review pass): tried the steel-grey judgment call
# from that review and preferred the original blue after all -- reverted. Keep the input mesh
# as a translucent grey-blue ghost (INPUT_MESH_COLOR above) and the rebuilt solid as this blue.
SOLID_MESH_COLOR = "#3f9fdc"
SOLID_MESH_OPACITY_NORMAL = 1.0
# Matches INPUT_MESH_OPACITY_ONLY's "ghost" look, for a consistent transparent appearance
# whichever mesh is toggled translucent.
SOLID_MESH_OPACITY_TRANSPARENT = 0.35
STATION_RING_COLOR = "#f2c94c"
EVENT_RING_COLOR = "#e5534b"
STATION_HIGHLIGHT_COLOR = "#ffd76a"
DEVIATION_CMAP = "inferno"

EMPTY_HINT_TEXT = "Open a burnback STL to begin — File ▸ Open or the Input panel"


def _any_perpendicular(axis):
    """A unit vector perpendicular to `axis` -- used to place a station callout label out at the
    ring's edge rather than at its (occluded, inside-the-solid) center."""
    axis = np.asarray(axis, dtype=float)
    seed = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    perp = seed - (seed @ axis) * axis
    n = np.linalg.norm(perp)
    return perp / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])

LAYERS = ("input", "solid", "stations", "events", "axis")
# Layers that get a clickable legend row (axis is a reference line, not a data layer -- it never
# had a legend entry even before the legend became interactive).
LEGEND_LAYERS = {"Input mesh": ("input", INPUT_MESH_COLOR), "Rebuilt solid": ("solid", SOLID_MESH_COLOR),
                 "Stations": ("stations", STATION_RING_COLOR),
                 "Topology change": ("events", EVENT_RING_COLOR)}


class LegendRow(QWidget):
    """One clickable legend entry: a color swatch + label that dims when its layer is hidden.
    Clicking toggles the layer via the same `Viewport.set_layer_visible` the View menu uses --
    this widget holds no visibility state of its own, it only reflects/drives it."""
    clicked = Signal()

    def __init__(self, label: str, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self._on = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(6)
        self._swatch = QLabel("■")
        self._label = QLabel(label)
        layout.addWidget(self._swatch)
        layout.addWidget(self._label)
        self._restyle()

    def set_on(self, on: bool):
        self._on = bool(on)
        self._restyle()

    def _restyle(self):
        swatch_color = self._color if self._on else TEXT_DISABLED
        text_color = "#d6dbe3" if self._on else TEXT_DISABLED
        self._swatch.setStyleSheet(f"color: {swatch_color}; font-size: 14px;")
        self._label.setStyleSheet(f"color: {text_color};")

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class AxisGizmo(QWidget):
    """A single home hotspot at the viewport's very bottom-left corner. Used to be four buttons
    (X/Y/Z + home); Brady tried the new VTK camera-orientation widget live (`_add_axis_triad`)
    and confirmed its own click/drag interaction already snaps to each axis view reliably, so
    the redundant X/Y/Z buttons were dropped (2026-09-07) -- only "reset to home" has no VTK-
    native equivalent (dragging back to iso by hand isn't the same as a precise snap), so it
    keeps its own reliable Qt click target, sitting below-left of the VTK widget rather than
    overlapping it."""
    home_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        home = _ClickableLabel("⌂")
        home.setFixedSize(20, 20)
        home.setAlignment(Qt.AlignmentFlag.AlignCenter)
        home.setCursor(Qt.CursorShape.PointingHandCursor)
        home.setToolTip("Reset to the isometric home view")
        home.setStyleSheet(
            "color: #e6e8eb; font-weight: 700; font-size: 13px; "
            "background-color: rgba(20, 22, 26, 0.7); border: 1px solid rgba(255,255,255,0.15); "
            "border-radius: 3px;")
        home.clicked.connect(self.home_clicked.emit)
        layout.addWidget(home)


class _SectionSliderOverlay(QWidget):
    """Bottom-edge Qt overlay for View ▸ Section view (Phase 3, V2) -- same pattern as the
    legend/axis-gizmo overlays (a plain Qt widget floated over the plotter, reliable and simple,
    versus fighting VTK's own interactive clip-plane widget inside a QtInteractor)."""
    fraction_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "background-color: rgba(20, 22, 26, 0.85); border: 1px solid #3a3f47; "
            "border-radius: 4px;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)
        label = QLabel("SECTION")
        label.setStyleSheet(
            f"color: {TELEMETRY}; font-weight: 700; font-size: 11px; letter-spacing: 1px; "
            "background: transparent;")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.setFixedWidth(220)
        self.slider.valueChanged.connect(lambda v: self.fraction_changed.emit(v / 1000.0))
        layout.addWidget(label)
        layout.addWidget(self.slider)
        self.hide()


class Viewport(QWidget):
    layer_toggled = Signal(str, bool)  # layer key, new visibility -- for View-menu sync

    # Bottom-left orientation corner geometry (2026-09-07). Kept small and named so either can
    # be nudged in one place if the live layout still overlaps -- see `_reposition_axis_gizmo`.
    _CAMERA_WIDGET_SIZE = 70
    _AXIS_GIZMO_BOTTOM_MARGIN = 8

    def __init__(self, parent=None, offscreen: bool = False):
        super().__init__(parent)
        self._offscreen = offscreen or QtInteractor is None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if self._offscreen:
            self.plotter = pv.Plotter(off_screen=True, window_size=(900, 700), lighting="light kit")
            self._label = QLabel("3D viewport (offscreen)")
            self._label.setScaledContents(True)
            layout.addWidget(self._label)
        else:
            self.plotter = QtInteractor(self)
            layout.addWidget(self.plotter.interactor if hasattr(self.plotter, "interactor") else self.plotter)
        self._input_actor = None
        self._input_mesh_data = None  # kept so an opacity-only toggle can re-add without a caller
        self._solid_actor = None
        self._solid_mesh_data = None
        self._station_actor = None
        self._event_actor = None
        self._axis_actor = None
        self._has_events = False
        self._station_radius = 1.0
        self._station_axis = np.array([0.0, 0.0, 1.0])
        self._station_axis_point = np.zeros(3)
        # axis-frame cache for the section-view clip plane (Phase 3, V2) -- set by
        # `set_axis_frame`, which `_on_rebuilt` already has all four values for.
        self._axis_frame = None  # (axis_unit, axis_point, proj_min, proj_max) or None
        self._section_frac = None  # 0..1 along the axis, or None (off)
        self._deviation_mode = False
        self._deviation_clim = None
        self._deviation_tol_mm = None
        self._render_mode = "shaded"  # "shaded" | "edges" | "wireframe"
        # user preferences survive scene resets (a new run should respect the View menu)
        self._layer_visible = {k: True for k in LAYERS}
        self._swap = False
        self._solid_transparent = False
        self._input_opaque = False

        # A small centered stack (icon/title/hint/shortcuts), not a single sentence -- the empty
        # viewport was the first thing a user sees and read as an unstyled placeholder rather
        # than a considered part of the app (2026-09-07 design review, U4).
        self._empty_hint = QWidget(self)
        self._empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        hint_layout = QVBoxLayout(self._empty_hint)
        hint_layout.setSpacing(6)
        hint_icon = QLabel()
        hint_icon.setPixmap(qta.icon("fa5s.cube", color=TEXT_DISABLED).pixmap(48, 48))
        hint_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_title = QLabel("No geometry loaded")
        hint_title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 15px; background: transparent;")
        hint_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_text = QLabel(EMPTY_HINT_TEXT)
        hint_text.setObjectName("emptyHint")
        hint_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_text.setWordWrap(True)
        hint_shortcuts = QLabel("Ctrl+O — open  ·  F5 — analyze  ·  Ctrl+R — rebuild")
        hint_shortcuts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_shortcuts.setStyleSheet(
            f"color: {TEXT_DISABLED}; font-family: {MONO_FAMILY}; font-size: 11px; "
            "background: transparent;")
        for w in (hint_icon, hint_title, hint_text, hint_shortcuts):
            hint_layout.addWidget(w)

        # Qt overlay legend (replaces VTK's add_legend, whose text renders independent of its
        # box and clipped at every size we tried — 2026-09-04). Clickable rows (2026-09-05,
        # Brady's request): each entry toggles its own layer, so this is a plain container that
        # `_update_legend` populates with `LegendRow`s -- it is NOT WA_TransparentForMouseEvents
        # like `_empty_hint`, since it now needs to receive clicks.
        self._legend = QWidget(self)
        self._legend.setObjectName("viewportLegend")
        self._legend.setStyleSheet(
            "#viewportLegend { background-color: rgba(20, 22, 26, 0.85); "
            "border: 1px solid #3a3f47; border-radius: 4px; } "
            "#viewportLegend QLabel { background: transparent; font-size: 12px; }")
        legend_layout = QVBoxLayout(self._legend)
        legend_layout.setContentsMargins(10, 6, 10, 6)
        legend_layout.setSpacing(2)
        # Rows are created once, up front, and toggled with setVisible() rather than added/
        # removed on the fly -- a widget inserted into an already-visible parent's layout does
        # not actually become visible (and so contributes zero size) until the event loop gets a
        # spin, which made a rebuilt legend collapse/overlap on the second and later calls to
        # `_update_legend` (confirmed 2026-09-05: `show_input_mesh` then `show_solid_mesh`
        # produced a 20x12 box with garbled overlapping text). Pre-built rows sidestep that
        # entirely -- hide/show is a much better-trodden Qt layout code path than insert/remove.
        self._legend_rows = {}
        for label, (key, color) in LEGEND_LAYERS.items():
            row = LegendRow(label, color, self._legend)
            row.clicked.connect(lambda k=key: self._on_legend_row_clicked(k))
            row.hide()
            legend_layout.addWidget(row)
            self._legend_rows[key] = row
        self._legend.hide()

        self._axis_gizmo = AxisGizmo(self)
        self._axis_gizmo.home_clicked.connect(lambda: (self._iso_camera(), self.render()))

        self._section_overlay = _SectionSliderOverlay(self)
        self._section_overlay.fraction_changed.connect(self.set_section_fraction)

        self.reset_scene()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_hint()
        self._reposition_legend()
        self._reposition_axis_gizmo()
        self._reposition_section_overlay()

    def _reposition_section_overlay(self):
        self._section_overlay.adjustSize()
        w, h = self._section_overlay.width(), self._section_overlay.height()
        self._section_overlay.move(max((self.width() - w) // 2, 0), self.height() - h - 14)
        self._section_overlay.raise_()

    def _reposition_hint(self):
        h = 160  # icon + title + hint text + shortcuts row (U4, 2026-09-07)
        self._empty_hint.setGeometry(20, (self.height() - h) // 2, max(self.width() - 40, 0), h)
        self._empty_hint.raise_()

    def _reposition_legend(self):
        if self._legend.isVisible():
            self._legend.adjustSize()
            # The camera-orientation widget now anchors bottom-left (see `_add_axis_triad`), not
            # top-right, so the legend no longer needs to dodge it.
            self._legend.move(max(self.width() - self._legend.width() - 14, 0), 12)
            self._legend.raise_()

    def _reposition_axis_gizmo(self):
        # The single home button sits at the pane's own bottom-left corner, below-left of the
        # camera-orientation widget's own bottom-left anchor (`_add_axis_triad`, sized to
        # `_CAMERA_WIDGET_SIZE` there) -- Brady, 2026-09-07: the X/Y/Z buttons were dropped once
        # he confirmed the VTK widget's own click/drag already snaps to each axis view; only
        # "reset to home" keeps its own reliable Qt click target since dragging back to iso by
        # hand isn't a precise snap. Exact pixel placement relative to the VTK widget couldn't
        # be rendered/verified in this environment (no live display here) -- nudge
        # `_AXIS_GIZMO_BOTTOM_MARGIN` if it still overlaps on a real screen.
        self._axis_gizmo.adjustSize()
        self._axis_gizmo.move(8, self.height() - self._axis_gizmo.height() - self._AXIS_GIZMO_BOTTOM_MARGIN)
        self._axis_gizmo.raise_()

    def reset_scene(self):
        self.plotter.clear()
        try:
            # Plotter.clear() removes the light kit; without it VTK falls back to a single
            # camera headlight, which lights any camera-facing surface perfectly evenly and
            # makes solids render flat (root cause of the "coin stack" look, 2026-09-04)
            self.plotter.enable_lightkit()
        except Exception:
            pass
        self.plotter.set_background("#1e1e1e")
        self._input_actor = None
        self._input_mesh_data = None
        self._solid_actor = None
        self._solid_mesh_data = None
        self._station_actor = None
        self._event_actor = None
        self._axis_actor = None
        self._has_events = False
        self._axis_frame = None
        self._section_frac = None
        self._deviation_mode = False
        self._deviation_clim = None
        self._section_overlay.hide()
        self._legend.hide()
        self._add_axis_triad()
        self._empty_hint.show()
        self._reposition_hint()
        self._reposition_axis_gizmo()
        self.render()

    def _add_axis_triad(self):
        # ONE orientation indicator, not two (Brady, 2026-09-07 live-testing feedback: the
        # camera-orientation widget's default top-right dock overlapped the legend, AND having
        # it alongside the existing bottom-left AxisGizmo read as two competing axis displays).
        # Consolidated into the bottom-left corner: the plain white arrow triad this project
        # started with is dropped in favor of the camera-orientation widget's nicer ball-and-
        # stick look, re-anchored there via VTK's own `AnchorToLowerLeft` (pyvista's wrapper
        # doesn't expose positioning, so this drops one level to the raw widget representation).
        # `AxisGizmo`'s Qt overlay buttons (X/Y/Z/home) are UNCHANGED and stay the actual click
        # target -- this project already learned that lesson once (see AxisGizmo's own
        # docstring: "reliable click handling beats wrestling widget/actor picking"), so the
        # native widget's own click/drag interactivity is a bonus, not something relied on.
        self._has_camera_widget = False
        if not self._offscreen:
            try:
                widget = self.plotter.add_camera_orientation_widget()
                rep = widget.GetRepresentation()
                rep.AnchorToLowerLeft()
                rep.SetSize(self._CAMERA_WIDGET_SIZE, self._CAMERA_WIDGET_SIZE)
                self._has_camera_widget = True
            except Exception:
                pass

    def fit_view(self):
        """View ▸ Fit view (F): re-frame the camera on whatever's currently visible without
        changing its orientation."""
        self.plotter.reset_camera()
        self.render()

    def set_orthographic(self, on: bool):
        """View ▸ Orthographic projection (O): engineers verify silhouettes/alignment in ortho,
        where parallel edges stay parallel regardless of depth -- perspective distorts that."""
        try:
            if on:
                self.plotter.enable_parallel_projection()
            else:
                self.plotter.disable_parallel_projection()
        except Exception:
            pass
        self.render()

    # Named pyvista camera preset that looks straight down each axis (confirmed empirically,
    # 2026-09-06: view_xy looks down Z, view_yz down X, view_xz down Y).
    _AXIS_VIEW_METHOD = {"x": "view_yz", "y": "view_xz", "z": "view_xy"}

    def view_along_axis(self, axis: str):
        method = getattr(self.plotter, self._AXIS_VIEW_METHOD.get(axis, ""), None)
        if method is None:
            return
        method()
        self.plotter.reset_camera()
        self.render()

    # ---- layer visibility (View menu / swap) -----------------------------
    def set_layer_visible(self, layer: str, on: bool):
        if layer in self._layer_visible:
            self._layer_visible[layer] = bool(on)
            row = self._legend_rows.get(layer)
            if row is not None:
                row.set_on(bool(on))
            self._apply_visibility()
            self.render()

    def set_swap(self, on: bool):
        """A/B comparison: hide the rebuilt solid, show the input mesh near-opaque."""
        self._swap = bool(on)
        self._apply_visibility()
        self.render()

    def set_solid_transparent(self, on: bool):
        """Fade the rebuilt solid to a ghost, for comparing its fin/wall geometry against the
        (already-translucent) input mesh overlay."""
        self._solid_transparent = bool(on)
        self._apply_visibility()
        self.render()

    def set_input_opaque(self, on: bool):
        """Show the input mesh at full opacity, for comparing its outer boundary directly
        against the rebuilt solid. Wins over swap/overlay opacity -- an explicit request to see
        the input mesh solid should not be silently overridden by another mode."""
        self._input_opaque = bool(on)
        self._apply_visibility()
        self.render()

    def set_axis_frame(self, axis_unit, axis_point, proj_min: float, proj_max: float):
        """Cache the current rebuild's axis frame for the section-view clip plane (V2) --
        `main_window._on_rebuilt` already computes all four values via `_axis_frame_metrics`
        for the station rings, this just keeps a copy so the slider doesn't need them re-passed
        on every drag."""
        self._axis_frame = (np.asarray(axis_unit, dtype=float), np.asarray(axis_point, dtype=float),
                            float(proj_min), float(proj_max))

    def set_section_active(self, on: bool):
        """View ▸ Section view (S): shows/hides the bottom slider overlay and applies (or
        clears) the clip at its current position."""
        if on:
            self._section_overlay.show()
            self._reposition_section_overlay()
            self.set_section_fraction(self._section_overlay.slider.value() / 1000.0)
        else:
            self._section_overlay.hide()
            self.set_section_fraction(None)

    def set_section_fraction(self, frac):
        self._section_frac = None if frac is None else max(0.0, min(1.0, float(frac)))
        if self._solid_mesh_data is not None:
            self._readd_solid_actor()
        if self._input_mesh_data is not None:
            self._readd_input_actor()
        self._apply_visibility()
        self.render()

    def set_deviation_tolerance(self, tol_mm):
        """`report["verification"]["deviation"]["tol_mm"]` -- gives the heatmap's color scale a
        real engineering reference (the gate the run was actually checked against) instead of an
        arbitrary percentile of whatever this one mesh happens to measure."""
        self._deviation_tol_mm = float(tol_mm) if tol_mm else None

    def set_deviation_mode(self, on: bool):
        """View ▸ Deviation heatmap (Phase 3, V3): color the solid by its actual per-point
        distance to the input mesh, computed lazily on first use (both meshes are already
        loaded in the viewport by the time this can be toggled) rather than unconditionally on
        every rebuild, since not every session will use it."""
        self._deviation_mode = bool(on)
        if self._deviation_mode:
            self._ensure_deviation_scalars()
        self._readd_solid_actor()
        self._apply_visibility()
        self.render()

    def _ensure_deviation_scalars(self):
        mesh = self._solid_mesh_data
        if mesh is None or self._input_mesh_data is None or "deviation_mm" in mesh.point_data:
            return
        try:
            from scipy.spatial import cKDTree
            tree = cKDTree(self._input_mesh_data.points)
            d, _ = tree.query(mesh.points, k=1, workers=-1)
            mesh["deviation_mm"] = d
            upper = self._deviation_tol_mm or (float(np.percentile(d, 99)) if len(d) else 1.0)
            self._deviation_clim = (0.0, max(upper, 1e-6))
        except Exception:
            self._deviation_mode = False

    def set_render_mode(self, mode: str):
        """View ▸ Render mode (W cycles): "shaded" (current default look), "edges" (shaded +
        facet outlines), "wireframe"."""
        if mode not in ("shaded", "edges", "wireframe"):
            return
        self._render_mode = mode
        self._readd_solid_actor()
        self._apply_visibility()
        self.render()

    def _input_opacity(self) -> float:
        if self._input_opaque:
            return 1.0
        if self._swap:
            return INPUT_MESH_OPACITY_SWAP
        solid_shown = self._solid_actor is not None and self._layer_visible["solid"]
        return INPUT_MESH_OPACITY_OVER_SOLID if solid_shown else INPUT_MESH_OPACITY_ONLY

    def _solid_opacity(self) -> float:
        # Known tradeoff, not a bug: with the input mesh ALSO translucent (its default overlay
        # look, or --input-opaque off), two overlapping translucent surfaces can show minor VTK
        # depth-sorting artifacts. Inherent to order-independent-transparency-less rendering;
        # not worth the complexity of a proper OIT pass for a comparison aid.
        return SOLID_MESH_OPACITY_TRANSPARENT if self._solid_transparent else SOLID_MESH_OPACITY_NORMAL

    def _sync_input_opacity(self):
        """Re-add the input actor (not just mutate its Property) when its target opacity
        differs from what's currently set. Confirmed 2026-09-06: a bare `SetOpacity()` on an
        already-rendered actor can silently fail to visually update -- reproduced with a
        minimal pyvista script crossing the opaque/translucent boundary (1.0 <-> less than
        1.0, exactly what `--input-opaque` toggles) and confirmed unchanged even after an
        explicit `render()` in between. Removing and re-adding the actor is the reliable fix."""
        if self._input_actor is None:
            return
        target = self._input_opacity()
        if abs(self._input_actor.GetProperty().GetOpacity() - target) < 1e-9:
            return
        self._readd_input_actor()

    def _sync_solid_opacity(self):
        """Same fix as `_sync_input_opacity`, for the rebuilt-solid transparency toggle (the
        milestone case that surfaced this bug: 1.0 -> 0.35 crosses the same boundary)."""
        if self._solid_actor is None:
            return
        target = self._solid_opacity()
        if abs(self._solid_actor.GetProperty().GetOpacity() - target) < 1e-9:
            return
        self._readd_solid_actor()

    def _clip_for_section(self, mesh):
        """Section view (Phase 3, V2): clip `mesh` at the current slider fraction along the
        motor axis, so the bore interior is actually visible instead of relying on transparency
        alone (which stays muddy with two overlapping translucent surfaces -- see
        `_solid_opacity`'s own docstring on that tradeoff). No-op when section view is off or
        the axis frame hasn't been set yet (before the first rebuild)."""
        if mesh is None or self._section_frac is None or self._axis_frame is None:
            return mesh
        axis_unit, axis_point, proj_min, proj_max = self._axis_frame
        span = proj_max - proj_min
        plane_origin = axis_point + axis_unit * (proj_min + self._section_frac * span)
        try:
            return mesh.clip(normal=axis_unit, origin=plane_origin)
        except Exception:
            return mesh

    def _readd_input_actor(self):
        if self._input_mesh_data is None:
            return
        cam = self.plotter.camera_position
        if self._input_actor is not None:
            self.plotter.remove_actor(self._input_actor)
        self._input_actor = self.plotter.add_mesh(
            self._clip_for_section(self._input_mesh_data), color=INPUT_MESH_COLOR,
            opacity=self._input_opacity(), show_edges=False, name="input_mesh",
            label="Input mesh")
        self.plotter.camera_position = cam  # re-adding a mesh must not reset the user's view

    def _solid_render_kwargs(self) -> dict:
        """Material/coloring kwargs for the solid actor, composing the render-mode (V7) and
        deviation-heatmap (V3) toggles -- both mutate the SAME actor, so they're resolved in one
        place rather than each maintaining its own ad hoc re-add path."""
        kwargs = dict(
            smooth_shading=False, specular=0.1, ambient=0.25, diffuse=0.8,
            show_edges=self._render_mode == "edges", edge_color="#1c2026", line_width=0.5,
            style="wireframe" if self._render_mode == "wireframe" else "surface")
        mesh = self._solid_mesh_data
        if self._deviation_mode and mesh is not None and "deviation_mm" in mesh.point_data:
            clim = self._deviation_clim or (0.0, float(np.max(mesh["deviation_mm"])) or 1.0)
            kwargs.update(scalars="deviation_mm", cmap=DEVIATION_CMAP, clim=clim,
                          scalar_bar_args=dict(title="deviation (mm)", color="#e6e8eb", fmt="%.2f"))
        else:
            kwargs["color"] = SOLID_MESH_COLOR
        return kwargs

    def _readd_solid_actor(self):
        if self._solid_mesh_data is None:
            return
        cam = self.plotter.camera_position
        if self._solid_actor is not None:
            self.plotter.remove_actor(self._solid_actor)
        self._solid_actor = self.plotter.add_mesh(
            self._clip_for_section(self._solid_mesh_data), opacity=self._solid_opacity(),
            name="solid_mesh", label="Rebuilt solid", **self._solid_render_kwargs())
        self.plotter.camera_position = cam

    def _apply_visibility(self):
        # Opacity is synced (and, if needed, re-added) BEFORE the visibility pass below, so the
        # pairs it builds reference whichever actor object is current if a re-add just replaced
        # one -- a fresh `add_mesh` defaults to visible, and the loop below is what then applies
        # this layer's actual visibility state to it.
        self._sync_input_opacity()
        self._sync_solid_opacity()
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

    @property
    def has_input_mesh(self) -> bool:
        return self._input_actor is not None

    # ---- scene building --------------------------------------------------
    def show_input_mesh(self, mesh: pv.PolyData):
        self._empty_hint.hide()
        self._input_mesh_data = mesh
        if self._input_actor is not None:
            self.plotter.remove_actor(self._input_actor)
        self._input_actor = self.plotter.add_mesh(
            mesh, color=INPUT_MESH_COLOR, opacity=self._input_opacity(), show_edges=False,
            name="input_mesh", label="Input mesh")
        self._apply_visibility()
        self._iso_camera()
        self._update_legend()
        self.render()

    def show_solid_mesh(self, mesh: pv.PolyData):
        # FLAT shading, not smooth (still the default in `_solid_render_kwargs`):
        # `smooth_shading=True` makes VTK average per-vertex normals across adjacent facets
        # (Gouraud/Phong interpolation) -- fine on the gently-curved outer wall, but at ANY
        # bore/hole (where the mesh's radial facets all converge toward a small circle) that
        # per-vertex normal averaging creates a spurious pinwheel of bright/dark spokes
        # radiating from the hole under specular lighting. On a milestone whose bore already has
        # real fin/slot geometry (e.g. M9) this reads as literally "a second set of fins"
        # superimposed on the real ones (Brady, 2026-09-04) -- confirmed by rendering the same
        # STL both ways: `smooth_shading=True` reproduces the pinwheel at ANY tessellation
        # fidelity tested (0.3 down to 0.015 rad angular deflection, i.e. up to 250k triangles --
        # more resolution only sharpens the spokes, it doesn't remove them, since the artifact is
        # the shading model, not facet size); flat shading (each triangle lit by its own true
        # normal, no interpolation) removes it completely at the same tessellation
        # `pipeline/export.py::write_stl` already produces, while the outer wall still reads as
        # smoothly curved at that facet density.
        self._empty_hint.hide()
        self._solid_mesh_data = mesh
        self._deviation_clim = None
        self._readd_solid_actor()
        # once the rebuilt solid is present, the input mesh fades to a reference overlay
        # (G3 review #4); _apply_visibility owns the opacity/visibility rules incl. swap.
        self._apply_visibility()
        self._iso_camera()
        self._update_legend()
        self.render()

    def _iso_camera(self):
        """Isometric default view: a straight-on camera flattens cylinders under the light
        kit; iso gives the shading gradient that makes a solid read as a solid."""
        try:
            self.plotter.camera_position = "iso"
        except Exception:
            pass
        self.plotter.reset_camera()

    def _update_legend(self):
        active = set()
        if self._input_actor is not None:
            active.add("input")
        if self._solid_actor is not None:
            active.add("solid")
        if active:
            active.add("stations")
            if self._has_events:
                active.add("events")
        for key, row in self._legend_rows.items():
            row.setVisible(key in active)
            if key in active:
                row.set_on(self._layer_visible.get(key, True))
        if not active:
            self._legend.hide()
            return
        self._legend.show()
        self._reposition_legend()

    def _on_legend_row_clicked(self, key: str):
        on = not self._layer_visible.get(key, True)
        self.set_layer_visible(key, on)
        self.layer_toggled.emit(key, on)

    def show_station_planes(self, stations_z_mm, bounds_xy_mm, axis_unit=(0, 0, 1),
                            axis_point=(0.0, 0.0, 0.0)):
        """`stations_z_mm`: axial coordinates along the reconstruction axis (mm), in the SAME
        frame as `axis_point` (both input-frame axial projections — caller applies
        `axial_origin_z` before calling this). Drawn as thin rings hugging the outer surface
        silhouette (not full discs, which the opaque solid occludes almost entirely -- G3 review
        #4), centered on `axis_point + z*axis_unit` -- the actual motor axis, which generally
        does NOT pass through the world origin (2026-09-07 fix; the old `axis_point`-less version
        assumed it did, offsetting every ring by the motor axis's true transverse position).
        ALL stations are drawn — the ring count must match the report's n_stations (adaptive sets
        legitimately stack rings at features; the View menu can hide the layer if it reads busy)."""
        self.plotter.remove_actor("station_planes", render=False)
        self._station_actor = None
        if not stations_z_mm:
            self.render()
            return
        self._station_radius = max(bounds_xy_mm, 1.0) * 1.02
        self._station_axis = np.array(axis_unit, dtype=float)
        self._station_axis = self._station_axis / (np.linalg.norm(self._station_axis) or 1.0)
        self._station_axis_point = np.asarray(axis_point, dtype=float)
        stations_sorted = sorted(stations_z_mm)
        radius = self._station_radius
        normal = self._station_axis
        base = self._station_axis_point
        rings = pv.MultiBlock()
        for z in stations_sorted:
            center = base + normal * float(z)
            # Thin, near-transparent rings (0.85 -> 0.35 opacity, thinner band): at real section
            # counts (40-60+) full-opacity rings completely wallpaper the solid, hiding the very
            # geometry they're meant to annotate (2026-09-07 design review, seen on M8's fins/
            # star bore). `highlight_station` draws ONE full-weight ring on top for the case a
            # user actually wants one station to stand out.
            rings.append(pv.Disc(center=center, inner=radius * 0.99, outer=radius, normal=normal, r_res=1, c_res=48))
        merged = rings.combine()
        self._station_actor = self.plotter.add_mesh(
            merged, color=STATION_RING_COLOR, opacity=0.35, name="station_planes")
        self._apply_visibility()
        self.render()

    def highlight_station(self, z_mm, label: str = ""):
        """Draw ONE full-weight ring at `z_mm` (report-frame-adjusted, same convention as
        `show_station_planes`) with an optional text callout (e.g. "z=207.9  R=39.7"), or clear
        both if `z_mm` is None. Companion to the Stations table: selecting a row calls this so
        the table and the 3D view are two views of the same selection, not two disconnected
        displays (2026-09-07 design review, V1)."""
        self.plotter.remove_actor("station_highlight", render=False)
        self.plotter.remove_actor("station_label", render=False)
        if z_mm is None or self._station_actor is None:
            self.render()
            return
        radius = self._station_radius
        normal = self._station_axis
        center = self._station_axis_point + normal * float(z_mm)
        ring = pv.Disc(center=center, inner=radius * 0.955, outer=radius * 1.01,
                       normal=normal, r_res=1, c_res=64)
        self.plotter.add_mesh(ring, color=STATION_HIGHLIGHT_COLOR, opacity=1.0,
                              name="station_highlight")
        if label:
            try:
                label_point = center + _any_perpendicular(normal) * radius
                self.plotter.add_point_labels(
                    [label_point], [label], name="station_label", font_size=12,
                    text_color=STATION_HIGHLIGHT_COLOR, shape_opacity=0.35, shape_color="#14161a",
                    always_visible=True, show_points=False)
            except Exception:
                pass
        self._apply_visibility()
        self.render()

    def show_topology_events(self, events_z_mm, bounds_xy_mm, axis_unit=(0, 0, 1),
                             axis_point=(0.0, 0.0, 0.0)):
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
        base = np.asarray(axis_point, dtype=float)
        rings = pv.MultiBlock()
        for z in events_z_mm:
            center = base + normal * float(z)
            rings.append(pv.Disc(center=center, inner=radius * 0.97, outer=radius, normal=normal, r_res=1, c_res=48))
        merged = rings.combine()
        self._event_actor = self.plotter.add_mesh(
            merged, color=EVENT_RING_COLOR, opacity=0.9, name="topology_events")
        self._apply_visibility()
        self._update_legend()
        self.render()

    def show_axis_line(self, bounds_xy_mm, axial_extent_mm, axis_unit=(0, 0, 1), origin_z_mm=0.0,
                       axis_point=(0.0, 0.0, 0.0)):
        """Faint centerline through the detected reconstruction axis (G3 review #4), through
        `axis_point` -- the motor axis's true position, not assumed to pass through the world
        origin (2026-09-07 fix)."""
        self.plotter.remove_actor("axis_line", render=False)
        normal = np.array(axis_unit, dtype=float)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        # `bounds_xy_mm` dropped from this max (2026-09-07): it used to paper over the old
        # inflated radial estimate: a line 1.1x the actual axial span is the right length.
        half = max(axial_extent_mm, 1.0) * 0.55
        base = np.asarray(axis_point, dtype=float)
        p1 = base + normal * (origin_z_mm - half)
        p2 = base + normal * (origin_z_mm + half)
        line = pv.Line(p1, p2)
        self._axis_actor = self.plotter.add_mesh(
            line, color="#7a8290", opacity=0.5, line_width=1.5, name="axis_line")
        self._apply_visibility()
        self.render()

    def render(self):
        if self._offscreen:
            # `screenshot()` alone can return a STALE frame after an actor-property-only change
            # (opacity, visibility) with no accompanying geometry change -- confirmed 2026-09-06:
            # toggling an actor's opacity/visibility and calling `screenshot()` directly renders
            # the OLD state; an explicit `render()` first forces the real redraw `screenshot()`
            # then correctly captures. Cheap and always safe to call.
            self.plotter.render()
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
