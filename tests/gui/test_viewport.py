"""Viewport legend/layer-toggle tests (2026-09-05): clickable legend rows, rebuilt-solid
transparency, input-mesh opacity. See app/viewport.py's module docstring and PROGRESS.md for the
Qt layout timing bug this guards against (a legend rebuilt after already having shown once used
to collapse to a tiny box with overlapping row text -- widgets inserted into an already-visible
parent's layout don't become visible, and so don't get sized, until the event loop spins)."""
import numpy as np
import pyvista as pv
import pytest

from app.viewport import (
    INPUT_MESH_OPACITY_OVER_SOLID, SOLID_MESH_OPACITY_NORMAL, SOLID_MESH_OPACITY_TRANSPARENT,
    Viewport,
)


@pytest.fixture
def viewport(qtbot):
    vp = Viewport(offscreen=True)
    qtbot.addWidget(vp)
    vp.resize(900, 700)
    vp.show()
    qtbot.wait(0)
    vp.show_input_mesh(pv.Sphere())
    vp.show_solid_mesh(pv.Sphere())
    qtbot.wait(0)
    return vp


def test_legend_rows_exist_for_visible_layers(viewport):
    assert viewport._legend.isVisible()
    for key in ("input", "solid", "stations"):
        assert viewport._legend_rows[key].isVisible()
    # No topology events were shown, so that row stays hidden.
    assert not viewport._legend_rows["events"].isVisible()


def test_legend_row_click_toggles_layer_and_actor(viewport):
    seen = []
    viewport.layer_toggled.connect(lambda k, on: seen.append((k, on)))
    assert viewport._layer_visible["solid"] is True
    viewport._legend_rows["solid"].clicked.emit()
    assert viewport._layer_visible["solid"] is False
    assert viewport._solid_actor.GetVisibility() == 0
    assert seen == [("solid", False)]
    viewport._legend_rows["solid"].clicked.emit()
    assert viewport._layer_visible["solid"] is True
    assert viewport._solid_actor.GetVisibility() == 1


def test_view_menu_and_legend_stay_in_sync(viewport):
    # `set_layer_visible` is the single source of truth both the legend row and the (separately
    # tested, in test_main_window.py) View-menu action funnel through -- verify the row reflects
    # a toggle made the other way.
    viewport.set_layer_visible("stations", False)
    assert viewport._legend_rows["stations"]._on is False


def test_solid_transparent_toggle(viewport):
    assert viewport._solid_actor.GetProperty().GetOpacity() == SOLID_MESH_OPACITY_NORMAL
    viewport.set_solid_transparent(True)
    assert viewport._solid_actor.GetProperty().GetOpacity() == SOLID_MESH_OPACITY_TRANSPARENT
    viewport.set_solid_transparent(False)
    assert viewport._solid_actor.GetProperty().GetOpacity() == SOLID_MESH_OPACITY_NORMAL


def test_input_opaque_toggle_wins_over_default_overlay_opacity(viewport):
    # With the solid shown, the input mesh defaults to the faint overlay opacity.
    assert viewport._input_actor.GetProperty().GetOpacity() == INPUT_MESH_OPACITY_OVER_SOLID
    viewport.set_input_opaque(True)
    assert viewport._input_actor.GetProperty().GetOpacity() == 1.0
    viewport.set_input_opaque(False)
    assert viewport._input_actor.GetProperty().GetOpacity() == INPUT_MESH_OPACITY_OVER_SOLID


def test_legend_does_not_collapse_when_rebuilt_after_first_show(viewport):
    """Regression test for the exact bug found manually 2026-09-05: showing the input mesh
    (1 legend row + Stations) and THEN the solid mesh (adds a 2nd row) used to leave the legend
    container sized for the first call only, with new rows invisible/zero-size."""
    assert viewport._legend.layout().count() == len(viewport._legend_rows)
    for key in ("input", "solid", "stations"):
        row = viewport._legend_rows[key]
        assert row.isVisible()
        assert row.size().height() > 0
        assert row.size().width() > 0
    # Rows must be stacked (non-overlapping), not all pinned at the same position.
    ys = sorted(viewport._legend_rows[k].pos().y() for k in ("input", "solid", "stations"))
    assert ys[0] < ys[1] < ys[2]


def test_view_along_axis_looks_straight_down_the_given_axis(viewport):
    """`Viewport.view_along_axis` (formerly wired to AxisGizmo's own X/Y/Z buttons, dropped
    2026-09-07 once Brady confirmed the VTK camera-orientation widget's own click/drag already
    does this) must look straight down the given axis -- verified empirically (not just "some
    view changed") against a box with axis-marker spheres: looking down an axis puts that
    axis's +marker sphere near dead-center (foreshortened toward the camera), not off to a
    side."""
    viewport.plotter.clear()
    viewport.plotter.add_mesh(pv.Box(bounds=(-1, 1, -2, 2, -3, 3)), color="lightgray")
    markers = {"x": (3, 0, 0), "y": (0, 3, 0), "z": (0, 0, 3)}
    for axis, center in markers.items():
        viewport.plotter.add_mesh(pv.Sphere(radius=0.3, center=center), name=f"marker_{axis}")
    viewport.plotter.reset_camera()

    for axis in ("x", "y", "z"):
        viewport.view_along_axis(axis)
        # The clicked axis's own marker should now project near the viewport center (looking
        # straight down that axis foreshortens it to ~0 in screen space); the other two remain
        # off-center. Compare screen-space distance from center via the renderer's world-to-
        # display transform.
        renderer = viewport.plotter.renderer
        w, h = viewport.plotter.window_size
        dists = {}
        for other, center in markers.items():
            renderer.SetWorldPoint(*center, 1.0)
            renderer.WorldToDisplay()
            x, y, _ = renderer.GetDisplayPoint()
            dists[other] = ((x - w / 2) ** 2 + (y - h / 2) ** 2) ** 0.5
        assert dists[axis] < min(dists[o] for o in markers if o != axis)


def test_view_along_axis_ignores_unknown_axis(viewport):
    # No exception, no-op -- defensive against a future typo/refactor, not a reachable UI path.
    viewport.view_along_axis("w")


def _center_pixel(vp):
    vp.render()
    img = vp._label.pixmap().toImage()
    return img.pixelColor(img.width() // 2, img.height() // 2).getRgb()[:3]


def test_solid_transparent_toggle_actually_rerenders(qtbot):
    """Regression test for a real VTK rendering bug found 2026-09-06: mutating an
    already-rendered actor's opacity via a bare `SetOpacity()` can silently fail to visually
    update on the NEXT render call -- reproduced directly in a minimal pyvista script (crossing
    the opaque/1.0 <-> translucent boundary in EITHER direction never re-rendered, even with an
    explicit `render()` call in between; changes that stayed within the translucent range did
    update fine). `_sync_solid_opacity`/`_readd_solid_actor` fix this by re-adding the actor
    instead of mutating it in place.

    A `GetOpacity() == expected` property assertion does NOT catch this class of bug -- the
    property value updates correctly even when the bug is present, which is exactly what let it
    ship undetected. This has to be a real pixel comparison against an actual rendered frame."""
    vp = Viewport(offscreen=True)
    qtbot.addWidget(vp)
    vp.resize(300, 300)
    vp.show()
    vp.plotter.set_background("black")
    vp.show_solid_mesh(pv.Sphere(radius=2.0))
    opaque_pixel = _center_pixel(vp)

    vp.set_solid_transparent(True)
    ghost_pixel = _center_pixel(vp)

    # Against a pure-black background, fading toward transparent blends the surface's own lit
    # color darker -- a stale (bug-present) render would show the IDENTICAL pixel here.
    assert ghost_pixel != opaque_pixel


def test_input_opaque_toggle_actually_rerenders(qtbot):
    """Same regression as `test_solid_transparent_toggle_actually_rerenders`, for the input
    mesh's opacity toggle (which crosses the same 1.0 boundary in the opposite direction)."""
    vp = Viewport(offscreen=True)
    qtbot.addWidget(vp)
    vp.resize(300, 300)
    vp.show()
    vp.plotter.set_background("black")
    vp.show_input_mesh(pv.Sphere(radius=2.0))
    ghost_pixel = _center_pixel(vp)

    vp.set_input_opaque(True)
    opaque_pixel = _center_pixel(vp)

    assert ghost_pixel != opaque_pixel


def test_legend_visibility_toggle_actually_rerenders(qtbot):
    """Same class of bug, for `SetVisibility()` -- confirmed the offscreen `render()` path could
    return a stale frame after a visibility-only change too (fixed by `Viewport.render()`
    forcing a real `plotter.render()` before `screenshot()`)."""
    vp = Viewport(offscreen=True)
    qtbot.addWidget(vp)
    vp.resize(300, 300)
    vp.show()
    vp.plotter.set_background("black")
    vp.show_solid_mesh(pv.Sphere(radius=2.0))
    shown_pixel = _center_pixel(vp)

    vp.set_layer_visible("solid", False)
    hidden_pixel = _center_pixel(vp)

    assert hidden_pixel != shown_pixel
    assert hidden_pixel == (0, 0, 0)  # nothing left but the black background


def test_station_planes_sized_and_centered_on_an_off_origin_x_axis(viewport):
    """Regression test for Brady's 2026-09-06 report: a real motor with detected axis X (not Z)
    and an axis that doesn't pass through the world origin got station rings ~12x too big and
    offset onto the wrong line -- the old call site (`bounds_xy = max(abs(x), abs(y))`, ring
    center `normal * z`) assumed axis == Z and axis-through-origin. This fails against that old
    code path: it would compute a radius from raw X bounds (~hundreds of mm here if X carried
    the axial span) and center rings on the origin line instead of (0, -30, 50)."""
    viewport.show_station_planes([180.0, 220.0], 5.0, (1, 0, 0), axis_point=(0, -30, 50))
    b = viewport._station_actor.GetBounds()  # (xmin, xmax, ymin, ymax, zmin, zmax)
    assert 179.0 <= b[0] and b[1] <= 221.0  # rings at the given axial projections, not spread out
    assert (b[3] - b[2]) <= 12  # ring diameter ~10.2 (radius 5 * 1.02 margin), NOT ~1000
    assert (b[5] - b[4]) <= 12
    assert -32 <= (b[2] + b[3]) / 2 <= -28  # centered near y=-30
    assert 48 <= (b[4] + b[5]) / 2 <= 52  # centered near z=50


def test_highlight_station_draws_and_clears_a_ring(viewport):
    viewport.show_station_planes([180.0, 220.0], 5.0, (1, 0, 0), axis_point=(0, -30, 50))
    viewport.highlight_station(200.0, "z=200.0  R=5.0")
    assert viewport.plotter.actors.get("station_highlight") is not None
    # `add_point_labels` registers under "<name>-labels", not the bare name (verified against
    # the installed pyvista directly) -- `remove_actor(name)` still matches it correctly.
    assert viewport.plotter.actors.get("station_label-labels") is not None
    viewport.highlight_station(None)
    assert viewport.plotter.actors.get("station_highlight") is None
    assert viewport.plotter.actors.get("station_label-labels") is None


def test_section_view_clips_the_solid_along_the_axis(viewport):
    """View > Section view (Phase 3, V2): clipping a sphere at the midpoint of its own bounding
    axial range must shrink the visible extent on that axis to roughly half."""
    full_bounds = viewport._solid_actor.GetBounds()
    viewport.set_axis_frame((0, 0, 1), (0, 0, 0), full_bounds[4], full_bounds[5])
    viewport.set_section_fraction(0.5)
    clipped_bounds = viewport._solid_actor.GetBounds()
    full_span = full_bounds[5] - full_bounds[4]
    clipped_span = clipped_bounds[5] - clipped_bounds[4]
    assert clipped_span < full_span * 0.7
    viewport.set_section_fraction(None)  # restores the full mesh
    restored_bounds = viewport._solid_actor.GetBounds()
    assert (restored_bounds[5] - restored_bounds[4]) == pytest.approx(full_span, rel=0.05)


def test_render_mode_switches_without_error(viewport):
    for mode in ("edges", "wireframe", "shaded"):
        viewport.set_render_mode(mode)
        assert viewport._render_mode == mode
        assert viewport._solid_actor is not None


def test_deviation_mode_computes_scalars_once_both_meshes_are_present(viewport):
    viewport.set_deviation_mode(True)
    mesh = viewport._solid_mesh_data
    assert "deviation_mm" in mesh.point_data
    assert viewport._deviation_clim is not None
    viewport.set_deviation_mode(False)
    # scalars stay cached (cheap to recompute-avoid); the toggle just stops USING them
    assert viewport._deviation_mode is False


def test_render_mode_edges_adds_a_feature_edge_overlay_not_raw_wireframe(viewport):
    """2026-09-07 premium-CAD research pass: "edges" mode used to be `show_edges=True` (every
    triangulation facet edge -- a dense, meaningless mess on a fine mesh). It must now draw only
    real geometric edges (extract_feature_edges), as a separate actor, present only in "edges"
    mode."""
    # A UV sphere (the fixture's default mesh) has no genuinely sharp edges anywhere -- extract
    # a mesh with real corners so there's something for the feature-edge filter to actually find.
    viewport.show_solid_mesh(pv.Cube())
    viewport.set_render_mode("edges")
    assert viewport.plotter.actors.get("solid_feature_edges") is not None
    viewport.set_render_mode("shaded")
    assert viewport.plotter.actors.get("solid_feature_edges") is None


def test_ssao_is_enabled_after_showing_a_solid_with_no_floor_plane(viewport):
    """SSAO (self-occlusion within the part's own geometry) stays enabled -- confirmed via the
    renderer's own active render pass, since pyvista doesn't expose a plain getter. The ground
    floor plane that used to live alongside it is GONE (2026-09-08, Brady's call after live-
    testing: it read as a stray artifact and got in the way of transparency comparisons) --
    assert there's no floor actor left over from that removed feature."""
    # SSAA (anti-aliasing) wraps SSAO as its delegate pass, so the renderer's OUTERMOST active
    # pass is the AA one -- walk the delegate chain to find SSAO nested inside it.
    pass_names = []
    p = viewport.plotter.renderer.GetPass()
    for _ in range(5):
        if p is None:
            break
        pass_names.append(type(p).__name__)
        p = p.GetDelegatePass() if hasattr(p, "GetDelegatePass") else None
    assert "vtkSSAOPass" in pass_names
    assert not any("loor" in k for k in viewport.plotter.actors)


def test_display_overlay_buttons_drive_viewport_state_and_emit_sync_signals(viewport):
    """The in-viewport display overlay (2026-09-07, Fusion-360/Onshape-style canvas controls)
    must be a second entry point into the SAME state Viewport already owns, not a parallel copy
    -- clicking a button changes the real state and emits the signal main_window listens to for
    keeping its View-menu QActions in sync (same pattern as the legend)."""
    render_modes = []
    viewport.render_mode_changed.connect(render_modes.append)
    viewport._display_overlay.render_mode_clicked.emit()
    assert viewport._render_mode == "edges"
    assert render_modes == ["edges"]
    assert viewport._display_overlay.render_mode_btn._checked is True

    section_states = []
    viewport.section_view_toggled.connect(section_states.append)
    viewport._display_overlay.section_clicked.emit()
    assert viewport._section_frac is not None
    assert section_states == [True]
    assert viewport._display_overlay.section_btn._checked is True

    deviation_states = []
    viewport.deviation_mode_toggled.connect(deviation_states.append)
    viewport._display_overlay.deviation_clicked.emit()
    assert viewport._deviation_mode is True
    assert deviation_states == [True]
    assert viewport._display_overlay.deviation_btn._checked is True


def test_deviation_scalars_are_point_to_surface_not_nearest_vertex(viewport):
    """Regression test for a real bug caught by the 2026-09-07 implementation review:
    `_ensure_deviation_scalars` used to measure nearest-VERTEX distance (cKDTree over the input
    mesh's own points), which is wrong by however sparse that mesh's vertex spacing is -- on a
    real motor this reported p95 ~19mm against a ~1.9mm tolerance on a run that actually passed
    at p95 0.32mm, rendering the whole solid as a saturated, meaningless "everything over
    tolerance" field. Build two meshes where the two metrics give CLEARLY different answers (a
    coarse/sparse-vertex sphere as input, a slightly larger fine-vertex concentric sphere as the
    "solid") and assert the ACTUAL (surface-distance) metric is substantially smaller than what
    the old (vertex-distance) metric would have reported on the same pair -- proving the fix
    without hard-coding an exact magnitude that would be fragile to tessellation specifics."""
    import pyvista as pv
    from scipy.spatial import cKDTree
    input_sphere = pv.Sphere(radius=10.0, theta_resolution=8, phi_resolution=8)  # coarse/sparse
    solid_sphere = pv.Sphere(radius=10.5, theta_resolution=60, phi_resolution=60)  # fine
    viewport.show_input_mesh(input_sphere)
    viewport.show_solid_mesh(solid_sphere)
    viewport.set_deviation_mode(True)
    surface_d = viewport._solid_mesh_data["deviation_mm"]

    old_vertex_d, _ = cKDTree(input_sphere.points).query(solid_sphere.points, k=1)
    # The old metric is inflated by the coarse input's own vertex spacing on top of the real
    # 0.5-unit radius gap; the fixed metric should track much closer to that true gap.
    assert float(np.percentile(surface_d, 95)) < float(np.percentile(old_vertex_d, 95)) * 0.6


def test_home_button_does_not_overlap_the_camera_widget_footprint(viewport):
    """Regression test: the home button used to sit at a fixed (8, ...) offset that landed
    INSIDE the corner-anchored camera-orientation widget's own footprint (verified by the
    2026-09-07 implementation review via geometry, since the VTK widget itself can't render
    offscreen to check directly). The button must clear the widget's box
    (`_CAMERA_WIDGET_PADDING` to `_CAMERA_WIDGET_PADDING + _CAMERA_WIDGET_SIZE`) on the x axis."""
    viewport.resize(900, 700)
    viewport._reposition_axis_gizmo()
    gizmo_right_edge = viewport._CAMERA_WIDGET_PADDING + viewport._CAMERA_WIDGET_SIZE
    assert viewport._axis_gizmo.pos().x() >= gizmo_right_edge


def test_empty_hint_background_is_transparent(viewport):
    """Regression test: the empty-state container picked up the app's global opaque QWidget
    background, painting a visible rectangle over the new gradient viewport background instead
    of blending into it (2026-09-07 implementation review)."""
    assert "transparent" in viewport._empty_hint.styleSheet()
