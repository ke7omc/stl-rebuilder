"""In-app manual (`app/help.py` + `app/help_content/`): the content loads, every page renders,
and the manual still documents the things it is supposed to document."""
import pytest

from app.help import HelpDialog, load_pages
from app.main_window import MainWindow

# Strings that must never silently drop out of the manual. Cheap completeness canary: it catches
# an accidentally deleted page or a rewrite that quietly loses a feature, not wording drift.
CANARIES = [
    "Ctrl+R", "Ctrl+O", "Ctrl+E", "F5", "Esc", "Ctrl+1",
    "auto from mesh", "adaptive", "chord", "Sections",
    "Section view", "Deviation", "Bounds", "watertight", "Swap input", "Render mode",
    "Topology events", "Dome-cap fit", "Orthographic", "Guided Demos",
]


@pytest.fixture
def dialog(qtbot):
    dlg = HelpDialog()
    qtbot.addWidget(dlg)
    return dlg


def test_load_pages_returns_the_whole_manual():
    pages = load_pages()
    assert len(pages) >= 11
    assert all(body.strip() for _pid, _title, body in pages)
    assert all(title.strip() for _pid, title, _body in pages)


def test_titles_and_ids_are_unique():
    pages = load_pages()
    assert len({title for _pid, title, _body in pages}) == len(pages)
    assert len({pid for pid, _title, _body in pages}) == len(pages)


def test_pages_are_ordered_by_filename():
    ids = [pid for pid, _title, _body in load_pages()]
    assert ids == sorted(ids)
    assert ids[0] == "00_quick_start"


def test_every_toc_entry_renders_non_empty_text(dialog):
    assert dialog.toc.topLevelItemCount() == len(load_pages())
    for i in range(dialog.toc.topLevelItemCount()):
        dialog.toc.setCurrentItem(dialog.toc.topLevelItem(i))
        text = dialog.browser.toPlainText()
        assert len(text.strip()) > 200, dialog.toc.topLevelItem(i).text(0)


def test_show_page_deep_links(dialog):
    assert dialog.show_page("06_verification") is True
    assert "Verification" in dialog.browser.toPlainText()
    assert dialog.show_page("no_such_page") is False


def test_filter_hides_non_matching_pages(dialog):
    dialog.search.setText("zzzz-no-such-text")
    assert all(dialog.toc.topLevelItem(i).isHidden()
               for i in range(dialog.toc.topLevelItemCount()))
    dialog.search.setText("")
    assert not any(dialog.toc.topLevelItem(i).isHidden()
                   for i in range(dialog.toc.topLevelItemCount()))
    dialog.search.setText("deviation heatmap")
    shown = [dialog.toc.topLevelItem(i).text(0)
             for i in range(dialog.toc.topLevelItemCount())
             if not dialog.toc.topLevelItem(i).isHidden()]
    assert shown and len(shown) < dialog.toc.topLevelItemCount()


@pytest.mark.parametrize("needle", CANARIES)
def test_manual_still_documents(needle):
    manual = "\n".join(f"{title}\n{body}" for _pid, title, body in load_pages())
    assert needle in manual


def test_help_menu_opens_the_dialog(qtbot):
    window = MainWindow(offscreen=True)
    qtbot.addWidget(window)
    assert window._help_dialog is None
    window._show_help("08_shortcuts")
    assert window._help_dialog is not None
    assert "Keyboard shortcuts" in window._help_dialog.browser.toPlainText()
    # A second invocation re-uses the same window rather than stacking another one.
    first = window._help_dialog
    window._show_help(None)
    assert window._help_dialog is first


def test_guided_demos_is_live(qtbot):
    """Phase C connected this one. It was a disabled "coming in this build" stub, and the manual
    now describes it as working -- so an accidental revert to the stub has to fail here."""
    window = MainWindow(offscreen=True)
    qtbot.addWidget(window)
    assert window.demos_action.isEnabled() is True
    assert "coming in this build" not in window.demos_action.toolTip()


def test_later_phase_menu_actions_are_stubbed(qtbot):
    """Still stubbed: the desktop shortcut, which Phase D builds."""
    window = MainWindow(offscreen=True)
    qtbot.addWidget(window)
    assert window.create_shortcut_action.isEnabled() is False
    assert window.create_shortcut_action.toolTip() == "coming in this build"
