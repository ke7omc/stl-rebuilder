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

from app.theme import (
    MONO_FAMILY, TELEMETRY, TEXT_DISABLED, TEXT_PRIMARY, TEXT_SECONDARY, VIEWPORT_BG_BOTTOM,
    VIEWPORT_BG_TOP,
)

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


def _radial_ticks(z_r_pairs, normal, base, inner_frac, outer_frac):
    """Four short radial tick lines per station, poking just OUTSIDE that station's own local
    silhouette. A flat ring viewed edge-on collapses to a near-invisible sliver (Brady's M9
    report, 2026-09-08) -- but a tick that crosses the silhouette projects as a visible comb
    tooth along the body's profile from any side view, so adaptive clustering reads directly as
    comb-tooth density, straight from the real z distribution (nothing smoothed or faked). Four
    azimuths so that at any camera angle at least two ticks survive foreshortening (the pair
    roughly perpendicular to the view direction lands on the silhouette edges)."""
    u = _any_perpendicular(normal)
    v = np.cross(normal, u)
    pts = np.empty((len(z_r_pairs) * 8, 3), dtype=float)
    k = 0
    for z, r in z_r_pairs:
        center = base + normal * float(z)
        for d in (u, -u, v, -v):
            pts[k] = center + d * (r * inner_frac)
            pts[k + 1] = center + d * (r * outer_frac)
            k += 2
    n = len(z_r_pairs) * 4
    lines = np.empty((n, 3), dtype=np.int64)
    lines[:, 0] = 2
    lines[:, 1] = np.arange(n) * 2
    lines[:, 2] = np.arange(n) * 2 + 1
    return pv.PolyData(pts, lines=lines.ravel())


def _offset_section_loops(section, normal, base):
    """Nudge a station's true traced cross-section loops slightly OFF the surface they lie
    exactly on (a slice IS the surface; drawn in place it z-fights and gets occluded): the outer
    loop 2% radially outward (just clear of the solid), every inner loop 2% inward -- into the
    bore void, which is where a star/fin bore's shape is actually visible to the camera. The
    homothety is about the motor axis so each loop keeps its real shape: this is what lets a
    ring literally hug a lobed silhouette instead of circumscribing it with a circle."""
    pts = np.asarray(section.points, dtype=float)
    rel = pts - base
    ax_c = np.outer(rel @ normal, normal)
    radial = rel - ax_c
    rnorm = np.linalg.norm(radial, axis=1)
    scale = np.full(len(pts), 1.02)
    region = section.point_data.get("RegionId")
    if region is not None and len(region) == len(pts) and rnorm.size:
        region = np.asarray(region)
        outer_rid = region[int(np.argmax(rnorm))]
        scale[region != outer_rid] = 0.98
    out = section.copy(deep=True)
    out.points = base + ax_c + radial * scale[:, None]
    return out

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
    """A single home hotspot, positioned immediately to the right of the viewport's bottom-left
    camera-orientation widget (see `Viewport._reposition_axis_gizmo`). Used to be four buttons
    (X/Y/Z + home); Brady tried the new VTK camera-orientation widget live (`_add_axis_triad`)
    and confirmed its own click/drag interaction already snaps to each axis view reliably, so
    the redundant X/Y/Z buttons were dropped (2026-09-07) -- only "reset to home" has no VTK-
    native equivalent (dragging back to iso by hand isn't the same as a precise snap), so it
    keeps its own reliable Qt click target beside the VTK widget rather than trying to overlap
    or replace it."""
    home_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        home = _ClickableLabel()
        home.setFixedSize(20, 20)
        home.setPixmap(qta.icon("ph.house-bold", color="#e6e8eb").pixmap(13, 13))
        home.setAlignment(Qt.AlignmentFlag.AlignCenter)
        home.setCursor(Qt.CursorShape.PointingHandCursor)
        home.setToolTip("Reset to the isometric home view")
        home.setStyleSheet(
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


class _DisplayToggleButton(_ClickableLabel):
    """One icon button in the in-viewport display-toggle overlay (`_DisplayOverlay`) -- a
    checkable state for the two boolean toggles (section view, deviation heatmap), a plain
    click target for the render-mode cycle."""

    def __init__(self, icon_name: str, tooltip: str, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._checked = False
        self.setFixedSize(26, 26)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._restyle()

    def set_checked(self, on: bool):
        self._checked = bool(on)
        self._restyle()

    def _restyle(self):
        color = TELEMETRY if self._checked else TEXT_SECONDARY
        bg = "rgba(53, 184, 200, 0.22)" if self._checked else "rgba(20, 22, 26, 0.7)"
        self.setPixmap(qta.icon(self._icon_name, color=color).pixmap(15, 15))
        self.setStyleSheet(
            f"background-color: {bg}; border: 1px solid rgba(255,255,255,0.15); "
            "border-radius: 3px;")


class _DisplayOverlay(QWidget):
    """Top-left in-viewport display-toggle bar (2026-09-07, premium-CAD research pass, "the
    bigger structural ideas"): Fusion 360 and Onshape both keep the most-used display state
    (section/clip, render mode, orientation) as translucent controls floating IN the canvas
    rather than buried only in a top menu -- same Qt-overlay pattern already proven here for the
    legend/axis-gizmo/section-slider (reliable, simple, no VTK widget picking). The View menu
    stays the keyboard-accessible source of truth; this is a convenience layer on top of it,
    kept in sync in both directions via Viewport's own signals (same pattern as the legend)."""
    section_clicked = Signal()
    deviation_clicked = Signal()
    render_mode_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "background-color: rgba(20, 22, 26, 0.85); border: 1px solid #3a3f47; "
            "border-radius: 4px;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)
        self.render_mode_btn = _DisplayToggleButton(
            "ph.polygon-bold", "Cycle render mode: shaded / edges / wireframe (W)")
        self.section_btn = _DisplayToggleButton(
            "ph.scissors-bold", "Section view: clip along the motor axis (S)")
        self.deviation_btn = _DisplayToggleButton(
            "ph.thermometer-bold", "Deviation heatmap: color by distance to the input mesh")
        self.render_mode_btn.clicked.connect(self.render_mode_clicked.emit)
        self.section_btn.clicked.connect(self.section_clicked.emit)
        self.deviation_btn.clicked.connect(self.deviation_clicked.emit)
        for btn in (self.render_mode_btn, self.section_btn, self.deviation_btn):
            layout.addWidget(btn)


class Viewport(QWidget):
    layer_toggled = Signal(str, bool)  # layer key, new visibility -- for View-menu sync
    section_view_toggled = Signal(bool)  # for View-menu sync, same pattern as layer_toggled
    deviation_mode_toggled = Signal(bool)
    render_mode_changed = Signal(str)

    # Bottom-left orientation corner geometry (2026-09-07). Kept small and named so either can
    # be nudged in one place -- see `_reposition_axis_gizmo`. `_CAMERA_WIDGET_PADDING` is VTK's
    # OWN default inset for a corner-anchored `vtkCameraOrientationRepresentation` (confirmed
    # against the installed VTK build by the 2026-09-07 implementation review), not a value this
    # app controls -- it has to be accounted for when placing anything else in the same corner.
    _CAMERA_WIDGET_SIZE = 70
    _CAMERA_WIDGET_PADDING = 10

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
        self._station_tick_actor = None
        self._event_actor = None
        self._event_tick_actor = None
        self._axis_actor = None
        self._feature_edges_actor = None
        self._camera_widget = None  # created once by `_add_axis_triad`, never recreated
        self._has_events = False
        self._station_radius = 1.0
        self._station_local_radii = []  # sorted (z, local r_outer) pairs for highlight_station
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
        # Without this, the global `QWidget { background-color: ... }` app QSS paints this
        # container as an opaque rectangle -- invisible back when the viewport background was a
        # flat color that happened to match, but it now sits on top of the gradient canvas
        # (2026-09-07 implementation review) as an unstyled slab behind the icon/title/hint text.
        self._empty_hint.setStyleSheet("background: transparent;")
        hint_layout = QVBoxLayout(self._empty_hint)
        hint_layout.setSpacing(6)
        hint_icon = QLabel()
        hint_icon.setPixmap(qta.icon("ph.cube-bold", color=TEXT_DISABLED).pixmap(48, 48))
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

        self._display_overlay = _DisplayOverlay(self)
        self._display_overlay.render_mode_btn.setToolTip(
            f"Cycle render mode: shaded / edges / wireframe (W) -- now: {self._render_mode}")
        self._display_overlay.render_mode_clicked.connect(self.cycle_render_mode)
        self._display_overlay.section_clicked.connect(
            lambda: self.set_section_active(self._section_frac is None))
        self._display_overlay.deviation_clicked.connect(
            lambda: self.set_deviation_mode(not self._deviation_mode))

        self.reset_scene()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_hint()
        self._reposition_legend()
        self._reposition_axis_gizmo()
        self._reposition_section_overlay()
        self._reposition_display_overlay()

    def _reposition_section_overlay(self):
        self._section_overlay.adjustSize()
        w, h = self._section_overlay.width(), self._section_overlay.height()
        self._section_overlay.move(max((self.width() - w) // 2, 0), self.height() - h - 14)
        self._section_overlay.raise_()

    def _reposition_display_overlay(self):
        self._display_overlay.adjustSize()
        self._display_overlay.move(12, 12)
        self._display_overlay.raise_()

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
        # The single home button sits immediately to the RIGHT of the camera-orientation
        # widget's own corner-anchored footprint, not "below-left of" it -- a widget anchored to
        # the same corner has no free space below-left of itself to sit in (2026-09-07
        # implementation review, geometry checked against the installed VTK build's own default
        # padding: `AnchorToLowerLeft` + `SetSize(_CAMERA_WIDGET_SIZE, _CAMERA_WIDGET_SIZE)`
        # occupies roughly x in [_CAMERA_WIDGET_PADDING, _CAMERA_WIDGET_PADDING +
        # _CAMERA_WIDGET_SIZE] from the corner -- the button's old position at x=8 sat INSIDE
        # that box on every axis, not beside it). Vertically centered on the gizmo's own span.
        # The VTK widget itself still can't be rendered in this offscreen environment to confirm
        # the exact look -- this is the geometrically-correct fix, not a pixel-verified one.
        self._axis_gizmo.adjustSize()
        gizmo_right = self._CAMERA_WIDGET_PADDING + self._CAMERA_WIDGET_SIZE + 8
        gizmo_center_y = self.height() - self._CAMERA_WIDGET_PADDING - self._CAMERA_WIDGET_SIZE / 2
        y = int(gizmo_center_y - self._axis_gizmo.height() / 2)
        self._axis_gizmo.move(gizmo_right, y)
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
        # Gradient canvas distinct from the panel chrome (see VIEWPORT_BG_* docstring in
        # theme.py), plus anti-aliasing -- both cheap, both confirmed working offscreen at
        # sub-second cost on a real 77k-cell motor mesh (2026-09-07 premium-CAD research pass).
        self.plotter.set_background(VIEWPORT_BG_BOTTOM, top=VIEWPORT_BG_TOP)
        try:
            self.plotter.enable_anti_aliasing("ssaa")
        except Exception:
            pass
        self._input_actor = None
        self._input_mesh_data = None
        self._solid_actor = None
        self._solid_mesh_data = None
        self._station_actor = None
        self._station_tick_actor = None
        self._event_actor = None
        self._event_tick_actor = None
        self._axis_actor = None
        self._feature_edges_actor = None
        self._has_events = False
        self._station_local_radii = []
        self._axis_frame = None
        self._section_frac = None
        self._deviation_mode = False
        self._deviation_clim = None
        self._render_mode = "shaded"
        self._section_overlay.hide()
        self._display_overlay.section_btn.set_checked(False)
        self._display_overlay.deviation_btn.set_checked(False)
        self._display_overlay.render_mode_btn.setToolTip(
            "Cycle render mode: shaded / edges / wireframe (W) -- now: shaded")
        self._legend.hide()
        self._add_axis_triad()
        self._empty_hint.show()
        self._reposition_hint()
        self._reposition_axis_gizmo()
        self._reposition_display_overlay()
        self.render()

    def _update_ssao(self):
        """(Re)compute SSAO's radius/bias from the CURRENT scene's own scale -- a fixed default
        radius tuned for a small part (e.g. pyvista's own SSAO example) is meaningless at this
        app's scale, these motors range from ~1m to ~10m.

        A ground floor plane used to live here too (KeyShot-style "Occlusion Ground Shadows").
        Removed 2026-09-08, Brady's call after live-testing: the flat plane read as a stray
        artifact at the origin rather than a deliberate ground reference, and actively got in
        the way of the transparency/ghost-overlay comparison mode (a second translucent surface
        added to the two the input/solid overlay was already juggling). SSAO itself stays --
        that's the part that actually made the render look better, per direct feedback -- it's
        just self-occlusion within the part's own geometry, no floor actor involved."""
        try:
            b = self.plotter.bounds
            diag = ((b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2) ** 0.5
        except Exception:
            diag = None
        if not diag or diag <= 0:
            return
        try:
            self.plotter.enable_ssao(radius=0.02 * diag, bias=0.0005 * diag, kernel_size=128)
        except Exception:
            pass

    def _add_axis_triad(self):
        # ONE orientation indicator, not two (Brady, 2026-09-07 live-testing feedback: the
        # camera-orientation widget's default top-right dock overlapped the legend, AND having
        # it alongside the existing bottom-left AxisGizmo read as two competing axis displays).
        # Consolidated into the bottom-left corner: the plain white arrow triad this project
        # started with is dropped in favor of the camera-orientation widget's nicer ball-and-
        # stick look, re-anchored there via VTK's own `AnchorToLowerLeft` (pyvista's wrapper
        # doesn't expose positioning, so this drops one level to the raw widget representation).
        # Its own click/drag already snaps to each axis view (Brady confirmed live) -- the
        # X/Y/Z buttons that used to do that in Qt were removed for exactly that reason; only
        # `AxisGizmo`'s single "home" button remains, since snapping back to iso has no native
        # equivalent on the VTK widget.
        #
        # Created ONCE, not on every `reset_scene()` (2026-09-07 implementation review):
        # `add_camera_orientation_widget()` has no dedupe in the installed pyvista (source-
        # checked) -- it allocates a fresh `vtkCameraOrientationWidget` and appends to
        # `camera_widgets` every call, and `Plotter.clear()` (called at the top of every
        # `reset_scene`) does not remove widgets. Calling this on every "Open STL" stacked a new
        # live widget in the same corner each time -- same pixels, but N widgets processing
        # interaction and an unbounded leak across a long session. Guard on the stored widget
        # HANDLE, not `_has_camera_widget` (that flag gets reset to False by every caller anyway,
        # so it can't be the guard).
        if getattr(self, "_camera_widget", None) is not None or self._offscreen:
            return
        try:
            self._camera_widget = self.plotter.add_camera_orientation_widget()
            rep = self._camera_widget.GetRepresentation()
            rep.AnchorToLowerLeft()
            rep.SetSize(self._CAMERA_WIDGET_SIZE, self._CAMERA_WIDGET_SIZE)
        except Exception:
            self._camera_widget = None

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
        """View ▸ Section view (S), or the in-viewport display-overlay scissors button (2026-09-07)
        -- shows/hides the bottom slider overlay and applies (or clears) the clip at its current
        position. Both entry points converge here and emit `section_view_toggled` so the OTHER
        one (View-menu QAction <-> overlay button) stays in sync, same pattern as the legend."""
        on = bool(on)
        if on:
            self._section_overlay.show()
            self._reposition_section_overlay()
            self.set_section_fraction(self._section_overlay.slider.value() / 1000.0)
        else:
            self._section_overlay.hide()
            self.set_section_fraction(None)
        self._display_overlay.section_btn.set_checked(on)
        self.section_view_toggled.emit(on)

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
        """View ▸ Deviation heatmap (Phase 3, V3), or the overlay's thermometer button
        (2026-09-07): color the solid by its actual per-point distance to the input mesh,
        computed lazily on first use (both meshes are already loaded in the viewport by the
        time this can be toggled) rather than unconditionally on every rebuild, since not every
        session will use it."""
        self._deviation_mode = bool(on)
        if self._deviation_mode:
            self._ensure_deviation_scalars()
        self._readd_solid_actor()
        self._apply_visibility()
        self._display_overlay.deviation_btn.set_checked(self._deviation_mode)
        self.deviation_mode_toggled.emit(self._deviation_mode)
        self.render()

    def _ensure_deviation_scalars(self):
        """Regression test for a real bug caught by the 2026-09-07 implementation review: this
        used to measure nearest-VERTEX distance (`cKDTree(input.points).query(solid.points)`) --
        on a real motor the input mesh's own vertex spacing dwarfs the actual reconstruction
        error, so it reported p95 ~19mm / max ~29mm against a ~1.9mm tolerance on a run that
        actually passed at p95 0.32mm (the engine's own point-to-triangle verification metric).
        The whole solid rendered saturated, uniform "everything over tolerance" -- exactly
        backwards. `compute_implicit_distance` (VTK's `vtkImplicitPolyDataDistance`, true nearest
        point-to-SURFACE distance, not nearest sample point) matches the engine's own number:
        re-verified directly on M8, p95 0.25mm here vs the engine's reported 0.32mm -- same order
        of magnitude, unlike the ~60x-inflated vertex-distance reading it replaces."""
        mesh = self._solid_mesh_data
        if mesh is None or self._input_mesh_data is None or "deviation_mm" in mesh.point_data:
            return
        try:
            d = np.abs(mesh.compute_implicit_distance(self._input_mesh_data)["implicit_distance"])
            mesh["deviation_mm"] = d
            upper = self._deviation_tol_mm or (float(np.percentile(d, 99)) if len(d) else 1.0)
            self._deviation_clim = (0.0, max(upper, 1e-6))
        except Exception:
            self._deviation_mode = False

    _RENDER_MODES = ("shaded", "edges", "wireframe")

    def set_render_mode(self, mode: str):
        """View ▸ Render mode submenu, or the overlay's polygon button's cycle (W / click):
        "shaded" (current default look), "edges" (real feature-edge overlay, not raw
        triangulation -- see `_update_feature_edges`), "wireframe"."""
        if mode not in self._RENDER_MODES:
            return
        self._render_mode = mode
        self._readd_solid_actor()
        self._apply_visibility()
        self._display_overlay.render_mode_btn.setToolTip(
            f"Cycle render mode: shaded / edges / wireframe (W) -- now: {mode}")
        self._display_overlay.render_mode_btn.set_checked(mode != "shaded")
        self.render_mode_changed.emit(mode)
        self.render()

    def cycle_render_mode(self):
        i = self._RENDER_MODES.index(self._render_mode)
        self.set_render_mode(self._RENDER_MODES[(i + 1) % len(self._RENDER_MODES)])

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
            # smooth_shading + split_sharp_edges (2026-09-07, premium-CAD research pass), NOT
            # smooth_shading alone: plain `smooth_shading=True` was rejected 2026-09-04 because
            # it averages per-vertex normals across ANY adjacent facets, which at a bore hole
            # (radial facets converging to a small circle) creates a spurious pinwheel of bright/
            # dark spokes -- confirmed by rendering the same STL both ways. `split_sharp_edges`
            # duplicates vertices at genuinely sharp creases BEFORE that averaging happens, so
            # smooth interpolation only ever blends normals within an actually-smooth region --
            # re-verified directly on M8's star bore (the exact geometry that broke before): no
            # pinwheel, clean soft dome shading, crisp fin edges. Flat shading is gone; it was
            # working around a problem this combination solves properly.
            smooth_shading=True, split_sharp_edges=True, specular=0.15, ambient=0.2, diffuse=0.85,
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
        clipped = self._clip_for_section(self._solid_mesh_data)
        self._solid_actor = self.plotter.add_mesh(
            clipped, opacity=self._solid_opacity(),
            name="solid_mesh", label="Rebuilt solid", **self._solid_render_kwargs())
        self._update_feature_edges(clipped)
        self.plotter.camera_position = cam

    def _update_feature_edges(self, clipped_mesh):
        """Render mode "edges" (V7): real geometric edges only, not the raw triangulation --
        `show_edges=True` drew every one of the mesh's thousands of tessellation facet edges, a
        dense mess with no relationship to the part's actual geometry. `extract_feature_edges`
        (a dihedral-angle threshold) separates genuine edges -- rim transitions, fillet tangent
        lines, the boundary of a bore -- from the fine near-coplanar facet noise that approximates
        a smoothly curved wall at this chord-tol; only the former survives the ~25 deg threshold.
        This is the tessellated-mesh equivalent of the "tangent edges dimmed, not drawn raw"
        convention real CAD viewers use on true BRep tangency data (Onshape's camera/render
        options, 2026-09-07 premium-CAD research pass) -- verified cheap even on a real motor
        mesh (~17k points -> ~0.02s)."""
        self.plotter.remove_actor("solid_feature_edges", render=False)
        self._feature_edges_actor = None
        if self._render_mode != "edges" or clipped_mesh is None or clipped_mesh.n_points == 0:
            return
        try:
            edges = clipped_mesh.extract_feature_edges(
                feature_angle=25, boundary_edges=True, non_manifold_edges=True,
                feature_edges=True, manifold_edges=False)
            if edges.n_points:
                self._feature_edges_actor = self.plotter.add_mesh(
                    edges, color="#05070a", line_width=1.5, opacity=1.0,
                    name="solid_feature_edges", pickable=False)
        except Exception:
            pass

    def _apply_visibility(self):
        # Opacity is synced (and, if needed, re-added) BEFORE the visibility pass below, so the
        # pairs it builds reference whichever actor object is current if a re-add just replaced
        # one -- a fresh `add_mesh` defaults to visible, and the loop below is what then applies
        # this layer's actual visibility state to it.
        self._sync_input_opacity()
        self._sync_solid_opacity()
        pairs = (("input", self._input_actor), ("solid", self._solid_actor),
                 ("stations", self._station_actor), ("stations", self._station_tick_actor),
                 ("events", self._event_actor), ("events", self._event_tick_actor),
                 ("axis", self._axis_actor), ("solid", self._feature_edges_actor))
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
        self._update_ssao()
        self.render()

    def show_solid_mesh(self, mesh: pv.PolyData):
        # See `_solid_render_kwargs` for the shading-mode history (flat -> smooth_shading +
        # split_sharp_edges, 2026-09-07).
        self._empty_hint.hide()
        self._solid_mesh_data = mesh
        self._deviation_clim = None
        self._readd_solid_actor()
        # once the rebuilt solid is present, the input mesh fades to a reference overlay
        # (G3 review #4); _apply_visibility owns the opacity/visibility rules incl. swap.
        self._apply_visibility()
        self._iso_camera()
        self._update_legend()
        self._update_ssao()
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
                            axis_point=(0.0, 0.0, 0.0), radii_mm=None, sections=None):
        """`stations_z_mm`: axial coordinates along the reconstruction axis (mm), in the SAME
        frame as `axis_point` (both input-frame axial projections — caller applies
        `axial_origin_z` before calling this). Centered on `axis_point + z*axis_unit` -- the
        actual motor axis, which generally does NOT pass through the world origin (2026-09-07
        fix; the old `axis_point`-less version assumed it did).

        `radii_mm` (optional, aligned to `stations_z_mm`): each station's OWN local outer radius
        from the same slice the Stations table measures. Without it every ring falls back to the
        single global `bounds_xy_mm` -- the exact bug from Brady's 2026-09-08 M9 session: rings
        all out at the full-body max radius meant a zoomed-in feature region looked station-free
        (its rings were off-frame at the far silhouette) even when adaptive placement had packed
        stations right there. `sections` (optional, aligned): the true traced cross-section
        loops at each station; when present the ring drawn IS that loop, offset just off the
        surface (`_offset_section_loops`), hugging star/fin silhouettes literally instead of
        circumscribing them with a circle -- for a grain, the interesting shape is usually the
        BORE loop, which a circle at r_outer never showed at all.

        ALL stations are drawn — one ring per station (adaptive sets legitimately stack rings at
        features; the View menu can hide the layer if it reads busy). A four-azimuth tick comb
        (`_radial_ticks`) rides in a second actor on the same layer for edge-on visibility."""
        self.plotter.remove_actor("station_planes", render=False)
        self.plotter.remove_actor("station_ticks", render=False)
        self._station_actor = None
        self._station_tick_actor = None
        self._station_local_radii = []
        if not stations_z_mm:
            self.render()
            return
        fallback_r = max(bounds_xy_mm, 1.0)
        n = len(stations_z_mm)
        radii = list(radii_mm) if radii_mm is not None else [None] * n
        secs = list(sections) if sections is not None else [None] * n
        self._station_radius = fallback_r * 1.02
        self._station_axis = np.array(axis_unit, dtype=float)
        self._station_axis = self._station_axis / (np.linalg.norm(self._station_axis) or 1.0)
        self._station_axis_point = np.asarray(axis_point, dtype=float)
        normal = self._station_axis
        base = self._station_axis_point
        rings = pv.MultiBlock()
        z_r = []
        for z, r, sec in sorted(zip(stations_z_mm, radii, secs), key=lambda t: float(t[0])):
            r_local = float(r) if r else fallback_r
            z_r.append((float(z), r_local))
            if sec is not None and getattr(sec, "n_points", 0):
                rings.append(_offset_section_loops(sec, normal, base))
            else:
                center = base + normal * float(z)
                rr = r_local * 1.02
                rings.append(pv.Disc(center=center, inner=rr * 0.985, outer=rr,
                                     normal=normal, r_res=1, c_res=48))
        self._station_local_radii = z_r
        merged = rings.combine()
        # Thin, near-transparent rings (0.85 -> 0.35 opacity): at real section counts (40-60+)
        # full-opacity rings completely wallpaper the solid, hiding the very geometry they're
        # meant to annotate (2026-09-07 design review, seen on M8's fins/star bore).
        # `highlight_station` draws ONE full-weight ring on top for the case a user actually
        # wants one station to stand out. The tick comb gets full-ish weight instead -- it's a
        # few pixels per station out past the silhouette, so it can't wallpaper anything, and at
        # 0.35 it disappeared exactly in the edge-on views it exists for.
        self._station_actor = self.plotter.add_mesh(
            merged, color=STATION_RING_COLOR, opacity=0.35, line_width=1.6, name="station_planes")
        ticks = _radial_ticks(z_r, normal, base, 1.03, 1.10)
        self._station_tick_actor = self.plotter.add_mesh(
            ticks, color=STATION_RING_COLOR, opacity=0.9, line_width=1.4, name="station_ticks")
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
        # The station's OWN local radius (nearest-z lookup into what show_station_planes drew),
        # not the global fallback -- a highlight ring out at the full-body radius around a small
        # dome-tip station would miss the very geometry the selected table row describes
        # (same 2026-09-08 M9 locality bug as the main ring layer).
        radius = self._station_radius
        if self._station_local_radii:
            _, r_local = min(self._station_local_radii, key=lambda zr: abs(zr[0] - float(z_mm)))
            radius = r_local * 1.02
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
                             axis_point=(0.0, 0.0, 0.0), radii_mm=None):
        """Same per-event local-radius sizing as `show_station_planes` (`radii_mm` aligned to
        `events_z_mm`, falling back to the global `bounds_xy_mm` -- the shared 2026-09-08 M9
        fixed-global-radius bug lived here too), plus a longer red tick comb so an event stays
        findable edge-on. Events keep circles rather than traced loops: an event z is exactly
        where the loop topology CHANGES, so "the" cross-section there is ambiguous by nature."""
        self.plotter.remove_actor("topology_events", render=False)
        self.plotter.remove_actor("topology_event_ticks", render=False)
        self._event_actor = None
        self._event_tick_actor = None
        self._has_events = bool(events_z_mm)
        if not events_z_mm:
            self._update_legend()
            self.render()
            return
        fallback_r = max(bounds_xy_mm, 1.0)
        radii = list(radii_mm) if radii_mm is not None else [None] * len(events_z_mm)
        normal = np.array(axis_unit, dtype=float)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        base = np.asarray(axis_point, dtype=float)
        rings = pv.MultiBlock()
        z_r = []
        for z, r in zip(events_z_mm, radii):
            r_local = float(r) if r else fallback_r
            z_r.append((float(z), r_local))
            center = base + normal * float(z)
            rr = r_local * 1.04
            rings.append(pv.Disc(center=center, inner=rr * 0.97, outer=rr, normal=normal, r_res=1, c_res=48))
        merged = rings.combine()
        self._event_actor = self.plotter.add_mesh(
            merged, color=EVENT_RING_COLOR, opacity=0.9, name="topology_events")
        ticks = _radial_ticks(z_r, normal, base, 1.04, 1.16)
        self._event_tick_actor = self.plotter.add_mesh(
            ticks, color=EVENT_RING_COLOR, opacity=0.95, line_width=1.8, name="topology_event_ticks")
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
