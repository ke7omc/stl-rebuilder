"""Widget-level GUI tests (pytest-qt, offscreen). MISSION §12/G2 layout contract."""
import numpy as np
import pyvista as pv
import pytest
from PySide6.QtWidgets import QDockWidget

from app.main_window import MainWindow, _axis_frame_metrics, _dome_cap_samples, _station_rows


@pytest.fixture
def window(qtbot):
    win = MainWindow(offscreen=True)
    qtbot.addWidget(win)
    return win


def test_docks_present(window):
    names = {d.objectName() for d in window.findChildren(QDockWidget)}
    assert {"outline_dock", "details_dock", "log_dock"}.issubset(names)


def test_outline_nodes(window):
    labels = [window.outline.topLevelItem(i).text(0) for i in range(window.outline.topLevelItemCount())]
    assert labels == ["Input", "Detected", "Stations", "Output"]


def test_outline_selection_switches_details_page(window):
    window.outline.setCurrentItem(window.node_detected)
    assert window.details_stack.currentWidget() is window.page_detected
    window.outline.setCurrentItem(window.node_output)
    assert window.details_stack.currentWidget() is window.page_output


def test_central_widget_is_viewport(window):
    assert window.centralWidget() is window.viewport


def test_run_without_input_shows_warning(window, monkeypatch):
    warned = {}
    monkeypatch.setattr(
        "app.main_window.QMessageBox.warning",
        lambda *a, **k: warned.setdefault("called", True))
    window.input_path_edit.setText("")
    window.output_path_edit.setText("")
    window.run_rebuild()
    assert warned.get("called") is True


def test_status_bar_widgets(window):
    assert window.progress_bar is not None
    assert window.status_label.text() == "Ready"


def test_run_without_analyze_runs_analyze_first_when_auto_chord_tol(window, monkeypatch):
    """Regression test for Brady's 2026-09-06 report: M8 at 40 sections + adaptive failed
    BRepCheck_Analyzer validity with "auto from mesh" checked but Analyze never run -- root
    cause was `_start_rebuild` silently falling back to the raw 0.5mm spinbox default instead
    of the mesh's own ~0.94mm suggestion, which was never computed because no Analysis existed.
    "Run" must now transparently run Analyze first in that situation, not use a wrong default."""
    started = []
    monkeypatch.setattr(window, "_start_rebuild", lambda: started.append(True))
    analyzed = []
    monkeypatch.setattr(window, "run_analyze", lambda: analyzed.append(True))
    window.input_path_edit.setText("input.stl")
    window.output_path_edit.setText("out/rebuilt.step")
    assert window.chord_tol_auto.isChecked()  # the default -- this bug only bites in this mode

    window.run_rebuild()
    assert analyzed == [True]
    assert started == []  # must NOT proceed with a wrong default chord-tol
    assert window._pending_rebuild is True

    # Simulate AnalyzeWorker completing: the chained rebuild must now actually start.
    from pipeline.engine import Analysis
    analysis = Analysis(frame_axis=[0, 0, 1], origin_xy_mm=[0, 0], axial_extent_mm=100.0,
                        body_count=1, is_watertight=True, triangle_count=10,
                        median_edge_length_mm=1.0, suggested_chord_tol_mm=0.94,
                        axis_confidence=1.0, units="mm", n_dropped_islands=0,
                        bounds_mm=[[-1, -1, -1], [1, 1, 1]])
    window._on_analyzed(analysis, None)
    assert started == [True]
    assert window._pending_rebuild is False


class _NullSignal:
    def connect(self, *_a, **_k):
        pass


class _FakeRebuildWorker:
    def __init__(self, opts):
        self.opts = opts
        self.progress = _NullSignal()
        self.finished = _NullSignal()
        self.failed = _NullSignal()


class _FakeThread:
    def start(self):
        pass


def test_start_rebuild_passes_none_roundness_tol_when_auto(window, monkeypatch, tmp_path):
    """Decoupled roundness plan (2026-09-09): mirrors the chord-tol auto/manual pattern -- "auto"
    must pass `None` through to `RebuildOptions.roundness_tol` (so the ENGINE measures the floor
    itself from the actual run's own chord_tol), not some GUI-side pre-measured value."""
    captured = []

    def fake_worker_ctor(opts):
        captured.append(opts)
        return _FakeRebuildWorker(opts)

    monkeypatch.setattr("app.main_window.RebuildWorker", fake_worker_ctor)
    monkeypatch.setattr("app.main_window.run_in_thread", lambda worker: _FakeThread())
    window.input_path_edit.setText("harness/truth/M1.stl")
    window.output_path_edit.setText(str(tmp_path / "out.step"))

    assert window.roundness_tol_auto.isChecked()  # the default
    window._start_rebuild()
    assert captured[-1].roundness_tol is None

    window.roundness_tol_auto.setChecked(False)
    window.roundness_tol_spin.setValue(0.42)
    window._start_rebuild()
    assert captured[-1].roundness_tol == pytest.approx(0.42)


def test_on_analyzed_fills_roundness_tol_spin_when_auto(window):
    from pipeline.engine import Analysis
    analysis = Analysis(frame_axis=[0, 0, 1], origin_xy_mm=[0, 0], axial_extent_mm=100.0,
                        body_count=1, is_watertight=True, triangle_count=10,
                        median_edge_length_mm=1.0, suggested_chord_tol_mm=0.94,
                        axis_confidence=1.0, units="mm", n_dropped_islands=0,
                        bounds_mm=[[-1, -1, -1], [1, 1, 1]], suggested_roundness_tol_mm=0.812)
    assert window.roundness_tol_auto.isChecked()  # the default
    window._on_analyzed(analysis, None)
    assert window.roundness_tol_spin.value() == pytest.approx(0.812)


def test_run_skips_analyze_when_already_analyzed_for_this_input(window, monkeypatch):
    started = []
    monkeypatch.setattr(window, "_start_rebuild", lambda: started.append(True))
    analyzed = []
    monkeypatch.setattr(window, "run_analyze", lambda: analyzed.append(True))
    window.input_path_edit.setText("input.stl")
    window.output_path_edit.setText("out/rebuilt.step")
    window._analysis = object()  # any truthy sentinel -- only presence/path matter here
    window._analyzed_input_path = "input.stl"

    window.run_rebuild()
    assert analyzed == []
    assert started == [True]


def test_run_re_analyzes_when_input_path_changed_since_last_analyze(window, monkeypatch):
    started = []
    monkeypatch.setattr(window, "_start_rebuild", lambda: started.append(True))
    analyzed = []
    monkeypatch.setattr(window, "run_analyze", lambda: analyzed.append(True))
    window._analysis = object()
    window._analyzed_input_path = "old_input.stl"
    window.input_path_edit.setText("new_input.stl")  # changed after that analysis
    window.output_path_edit.setText("out/rebuilt.step")

    window.run_rebuild()
    assert analyzed == [True]
    assert started == []


def test_failed_analyze_clears_pending_rebuild(window):
    window._pending_rebuild = True
    window._on_failed("Error", "boom")
    assert window._pending_rebuild is False


def test_browsing_input_shows_preview_immediately(window, qtbot, monkeypatch):
    """Regression test for Brady's 2026-09-06 report: right after selecting an input STL,
    nothing appeared in the viewport until Analyze (or Run) completed -- no confirmation a file
    had actually loaded. Selecting a file must now show it right away, via `PreviewWorker`
    (app/worker.py), independent of Analyze/Run ever running."""
    monkeypatch.setattr(
        "app.main_window.QFileDialog.getOpenFileName",
        lambda *a, **k: ("harness/truth/M1.stl", ""))
    assert not window.viewport.has_input_mesh

    window._browse_input()
    qtbot.waitUntil(lambda: window.viewport.has_input_mesh, timeout=15000)
    assert window.input_path_edit.text() == "harness/truth/M1.stl"


def test_editing_input_path_directly_shows_preview(window, qtbot):
    """Same fix, for typing/pasting a path directly instead of using Browse..."""
    window.input_path_edit.setText("harness/truth/M1.stl")
    window.input_path_edit.editingFinished.emit()
    qtbot.waitUntil(lambda: window.viewport.has_input_mesh, timeout=15000)


def test_editing_input_path_to_nonexistent_file_does_not_load(window, qtbot):
    window.input_path_edit.setText("does/not/exist.stl")
    window.input_path_edit.editingFinished.emit()
    qtbot.wait(200)
    assert not window.viewport.has_input_mesh


def test_analyze_disables_analyze_and_run_while_in_flight(window, qtbot):
    window.input_path_edit.setText("harness/truth/M1.stl")
    assert window.analyze_btn.isEnabled()
    assert window.run_btn.isEnabled()

    window.run_analyze()
    assert not window.analyze_btn.isEnabled()
    assert not window.run_btn.isEnabled()

    qtbot.waitUntil(lambda: window.analyze_btn.isEnabled(), timeout=15000)
    assert window.run_btn.isEnabled()


def test_clicking_analyze_again_while_busy_is_a_no_op(window, qtbot):
    """Regression test for Brady's 2026-09-06 report: clicking Analyze, then Run before Analyze
    finished (M13's Analyze alone can run ~19 minutes), started a SECOND, redundant
    AnalyzeWorker -- confirmed by two identical "Analyze done" log lines from what should have
    been one Analyze click. The Analyze action must be disabled for the whole duration, so a
    second trigger (a real UI action, not a direct method call bypassing the guard) is a no-op."""
    window.input_path_edit.setText("harness/truth/M1.stl")
    window.run_analyze()
    assert not window.analyze_action.isEnabled()
    workers_before = len(window._workers)

    window.analyze_action.trigger()  # disabled -- must not start a second worker
    assert len(window._workers) == workers_before

    qtbot.waitUntil(lambda: window.analyze_action.isEnabled(), timeout=15000)


def _make_analysis(**overrides):
    from pipeline.engine import Analysis
    defaults = dict(
        frame_axis=[0, 0, 1], origin_xy_mm=[0, 0], axial_extent_mm=10000.0, body_count=1,
        is_watertight=True, triangle_count=123456, median_edge_length_mm=1.0,
        suggested_chord_tol_mm=0.5, axis_confidence=1.0, units="mm", n_dropped_islands=0,
        bounds_mm=[[-1, -1, -1], [1, 1, 1]],
    )
    defaults.update(overrides)
    return Analysis(**defaults)


def test_analysis_property_groups_includes_roundness_noise_row():
    from app.main_window import _analysis_property_groups
    groups = _analysis_property_groups(_make_analysis(suggested_roundness_tol_mm=0.734))
    rows = next(g for label, g in groups if label == "Suggested run settings")
    display_by_name = {name: display for name, display, _raw in rows}
    assert "0.734" in display_by_name["Roundness noise (auto)"]


def test_analyze_log_line_states_watertight_pass_plainly(window):
    """Brady, 2026-09-06: "it needs to state that it is watertight when it passes that check,
    etc" -- not a raw `watertight=True` dump."""
    window._on_analyzed(_make_analysis(is_watertight=True), None)
    text = window.log.toPlainText()
    assert "✓ watertight" in text
    assert "watertight=True" not in text


def test_analyze_log_line_flags_non_watertight_as_a_warning(window):
    window._on_analyzed(_make_analysis(is_watertight=False), None)
    text = window.log.toPlainText()
    assert "NOT watertight" in text


def test_analyze_log_line_flags_multiple_bodies(window):
    window._on_analyzed(_make_analysis(body_count=3), None)
    text = window.log.toPlainText()
    assert "3 bodies detected" in text


def test_axis_frame_metrics_off_origin_non_z_axis():
    """Regression test for Brady's 2026-09-06 report: a real motor whose detected axis is X
    (not Z) and whose axis does not pass through the world origin produced station/topology
    rings ~12x too big -- `main_window.py`'s old `bounds_xy = max(abs(x), abs(y))` hardcoded
    "radial = X/Y plane", i.e. assumed axis == Z. This is a cylinder along X, offset in Y/Z, so
    the correct radial extent (5) is nowhere near the raw X bound (250) the old code would have
    used."""
    cyl = pv.Cylinder(center=(200, -30, 50), direction=(1, 0, 0), radius=5, height=100)
    radial, axis_point, proj_min, proj_max = _axis_frame_metrics(cyl, (1, 0, 0))
    assert radial == pytest.approx(5, rel=0.1)
    assert axis_point == pytest.approx([0, -30, 50], abs=0.5)
    assert proj_min == pytest.approx(150, abs=0.5)
    assert proj_max == pytest.approx(250, abs=0.5)


def test_axis_frame_metrics_answers_per_axis_not_per_frame():
    """Same mesh, a different (wrong-for-this-mesh) axis: the helper must not silently reuse a
    cached/frame-level answer -- it measures radial extent relative to WHATEVER axis it's given."""
    cyl = pv.Cylinder(center=(200, -30, 50), direction=(1, 0, 0), radius=5, height=100)
    radial, *_ = _axis_frame_metrics(cyl, (0, 0, 1))
    # Along Z, the cylinder's 100-long/10-wide profile is what's radial -- much bigger than 5.
    assert radial > 40


def test_station_rows_measures_radius_from_the_motor_axis_not_the_origin():
    """Regression test for the same 2026-09-06 incident: `_station_rows`' radius math measured
    distance from the line through the WORLD ORIGIN along the axis, not from the actual motor
    axis -- wrong by the axis's transverse offset for any off-origin input (measured on Brady's
    real run: 97.5mm reported vs 39.7mm correct). A legacy caller passing no `axis_point`
    preserves the old (documented-wrong) origin-line behavior, matching the previous default."""
    cyl = pv.Cylinder(center=(200, -30, 50), direction=(1, 0, 0), radius=5, height=100)
    report = {"axial_origin_z": 0.0}
    rows_correct = _station_rows(report, [200.0], set(), cyl, (1, 0, 0), axis_point=(0, -30, 50))
    rows_legacy = _station_rows(report, [200.0], set(), cyl, (1, 0, 0), axis_point=None)
    assert rows_correct[0][3] == pytest.approx(5, rel=0.15)  # r_outer
    assert rows_legacy[0][3] == pytest.approx(np.hypot(30, 50) + 5, rel=0.1)


def test_elide_path_keeps_head_and_tail_of_a_long_path():
    from app.main_window import _elide_path
    long_path = "/Users/bradyhales/Projects/stl-rebuilder/out/very/deep/nested/M8_rebuilt.step"
    elided = _elide_path(long_path, keep=38)
    assert len(elided) <= 39  # allows for the single ellipsis character
    assert elided.startswith(long_path[:5])
    assert elided.endswith(long_path[-5:])
    assert "…" in elided


def test_elide_path_leaves_a_short_path_untouched():
    from app.main_window import _elide_path
    assert _elide_path("out/rebuilt.step", keep=38) == "out/rebuilt.step"


def test_selecting_a_station_row_highlights_it_in_the_viewport_and_chart(window, monkeypatch):
    """Regression test for the station-table <-> viewport <-> profile-chart sync (2026-09-07
    design review, V1): selecting a row must convert its REPORT-frame z into the input-frame
    axial projection the viewport was drawn in (report z + axial_origin_z) before calling
    `highlight_station`, not pass the raw report z straight through."""
    calls = []
    monkeypatch.setattr(window.viewport, "highlight_station", lambda z, label="": calls.append((z, label)))
    chart_calls = []
    monkeypatch.setattr(window.page_stations.chart, "set_highlight", lambda z: chart_calls.append(z))
    window._last_axial_origin_z = 99.18
    window.page_stations.table.set_rows([(1, 100.0, 2, 39.7, None, "barrel", False)])
    window.page_stations.table.setCurrentItem(window.page_stations.table.topLevelItem(0))
    assert calls == [(199.18, "z=100.0  R=39.7")]
    assert chart_calls == [100.0]

    window.page_stations.table.setCurrentItem(None)
    assert calls[-1] == (None, "")  # clearing the row selection clears the highlight too
    assert chart_calls[-1] is None


def test_window_title_shows_the_loaded_file(window):
    window._update_window_title("harness/truth/M8.stl")
    assert "M8.stl" in window.windowTitle()
    window._update_window_title(None)
    assert window.windowTitle() == "STL Rebuilder"


def test_outline_gains_count_and_status_badges_after_a_rebuild(window):
    """Brady, 2026-09-07 design review (U7): "Stations (60)", "Output check-mark" badges give
    quick orientation without opening either page."""
    from pipeline.engine import Result
    # `_on_rebuilt` reads the STEP back via `manifest.build` -> OCCT's `STEPControl_Reader` --
    # on a path that doesn't exist on disk at all, that read SEGFAULTS instead of raising a
    # catchable Python exception (confirmed the hard way writing this test). Point at a real
    # STEP file already produced by an earlier milestone run instead of a fake path.
    result = Result(
        report={"stations_z_mm": [1.0, 2.0], "topology_events_z_mm": [],
               "verification": {"watertight": {"pass": True}}},
        output_path="out/M1.step", stl_path=None)
    window._on_rebuilt(result, None, None)
    assert window.node_stations.text(0) == "Stations (2)"
    assert "✓" in window.node_output.text(0)


def test_view_menu_stays_in_sync_with_the_viewport_display_overlay(window):
    """The in-viewport display overlay (2026-09-07) is a second entry point into state the View
    menu's QActions also control -- clicking the overlay must update the menu checkboxes (same
    two-way sync already proven for the legend/layer-visibility actions), not leave them stale."""
    window.viewport._display_overlay.section_clicked.emit()
    assert window.section_view_action.isChecked() is True

    window.viewport._display_overlay.deviation_clicked.emit()
    assert window.deviation_action.isChecked() is True

    window.viewport._display_overlay.render_mode_clicked.emit()  # shaded -> edges
    assert window._render_mode_actions["edges"].isChecked() is True
    assert window._render_mode_actions["shaded"].isChecked() is False


def test_render_mode_menu_action_drives_the_viewport_not_a_local_copy(window):
    """Regression test: `_set_render_mode`/`_cycle_render_mode` used to duplicate cycling logic
    in MainWindow with its OWN idea of "current mode" (read from the menu's checked state) --
    removed in favor of Viewport owning render_mode as the single source of truth, reached via
    `viewport.set_render_mode`/`cycle_render_mode` directly."""
    window._render_mode_actions["wireframe"].trigger()
    assert window.viewport._render_mode == "wireframe"
    window.render_mode_cycle_action.trigger()
    assert window.viewport._render_mode == "shaded"  # wraps around


def test_station_cross_sections_radii_track_local_geometry_not_a_global_bound():
    """Regression test for Brady's 2026-09-08 M9 report: the 3D station rings were all drawn at
    ONE global max radius, so zoomed into a local feature the rings sat off-frame at the far
    silhouette ("no stations here"). `_station_cross_sections` must return each station's OWN
    local radius -- on a cone (apex +z), r varies linearly with z, so two stations must come
    back with two DIFFERENT radii matching the cone's own r(z), not the base radius twice."""
    from app.main_window import _station_cross_sections
    cone = pv.Cone(center=(0, 0, 0), direction=(0, 0, 1), height=100.0, radius=40.0,
                   resolution=64)
    sections = _station_cross_sections(cone, [-25.0, 25.0], (0, 0, 1), (0, 0, 0))
    (sec_a, radii_a), (sec_b, radii_b) = sections
    # r(z) = 40 * (50 - z) / 100 -> 30 at z=-25, 10 at z=+25
    assert radii_a[0] == pytest.approx(30.0, rel=0.05)
    assert radii_b[0] == pytest.approx(10.0, rel=0.05)
    # The traced loop geometry itself must come back renderable: line cells, RegionId intact.
    for sec in (sec_a, sec_b):
        assert sec is not None and sec.n_points
        assert sec.n_lines > 0 or sec.n_cells > 0
        assert "RegionId" in sec.point_data


def test_station_rows_accepts_precomputed_sections_and_matches_self_computed():
    """`_on_rebuilt` slices once and feeds BOTH the viewport rings and the table -- the shared
    path must produce the same rows the standalone (self-slicing) path does."""
    from app.main_window import _station_cross_sections
    cyl = pv.Cylinder(center=(200, -30, 50), direction=(1, 0, 0), radius=5, height=100)
    report = {"axial_origin_z": 0.0}
    sections = _station_cross_sections(cyl, [200.0], (1, 0, 0), (0, -30, 50))
    rows_shared = _station_rows(report, [200.0], set(), cyl, (1, 0, 0),
                                axis_point=(0, -30, 50), sections=sections)
    rows_self = _station_rows(report, [200.0], set(), cyl, (1, 0, 0), axis_point=(0, -30, 50))
    assert rows_shared == rows_self
    assert rows_shared[0][3] == pytest.approx(5, rel=0.15)


def test_dome_cap_samples_reach_the_true_extremes_and_cluster_at_the_tip():
    """The end-band sampling behind the Dome-cap layer (Brady's 2026-09-08 M8 question: rings
    stop ~2% of the length short of each dome tip because the engine deliberately places no
    stations there). The extra slice z's must actually fill both bands out to within a hair of
    the solid's true axial extremes -- the whole point is that the display no longer stops
    short -- and cluster toward the tip, where the analytic dome fit differs most from any flat
    assumption."""
    fore, aft = _dome_cap_samples([200.0, 9700.0], 0.0, 9900.0)
    assert fore and aft
    assert all(0.0 < z < 200.0 for z in fore)
    assert all(9700.0 < z < 9900.0 for z in aft)
    # Reaches within 1% of the band width of each true extreme (tip_eps keeps the slice plane
    # off the exactly-tangent apex, where the cross-section degenerates).
    assert min(fore) < 0.01 * 200.0
    assert max(aft) > 9900.0 - 0.01 * 200.0
    gaps = np.diff(np.sort(np.asarray(aft)))
    assert gaps[-1] < gaps[0]  # sine clustering: spacing shrinks approaching the tip


def test_dome_cap_samples_empty_without_stations_or_band():
    assert _dome_cap_samples([], 0.0, 100.0) == ([], [])
    # Stations already at the extremes: no end band exists, nothing supplemental to draw.
    fore, aft = _dome_cap_samples([0.0, 100.0], 0.0, 100.0)
    assert fore == [] and aft == []


def test_view_menu_opacity_actions_sync_with_the_legend_ghost_buttons(window):
    """The legend rows' ghost buttons (2026-09-08) are a second entry point into the two
    opacity modes the View menu also controls -- driving them must update the menu checkmarks,
    and the menu must keep driving the viewport (single source of truth, no parallel state)."""
    window.viewport.show_input_mesh(pv.Sphere())
    window.viewport.show_solid_mesh(pv.Sphere())
    window.viewport._legend_rows["solid"].ghost_clicked.emit()
    assert window.solid_transparent_action.isChecked() is True
    window.viewport._legend_rows["input"].ghost_clicked.emit()
    assert window.input_opaque_action.isChecked() is True
    # And the menu direction still works after the sync wiring (no signal loop / stale state).
    window.solid_transparent_action.setChecked(False)
    assert window.viewport._solid_transparent is False


def test_input_triangulation_menu_action_drives_the_viewport(window):
    window.viewport.show_input_mesh(pv.Sphere())
    window.input_edges_action.setChecked(True)
    assert window.viewport._input_actor.GetProperty().GetEdgeVisibility() == 1
    window.input_edges_action.setChecked(False)
    assert window.viewport._input_actor.GetProperty().GetEdgeVisibility() == 0
