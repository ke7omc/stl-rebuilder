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
