"""Widget-level GUI tests (pytest-qt, offscreen). MISSION §12/G2 layout contract."""
import pytest
from PySide6.QtWidgets import QDockWidget

from app.main_window import MainWindow


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
