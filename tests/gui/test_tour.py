"""Coachmark engine (`app/tour.py`): bubble placement geometry, the controller's step machine,
the `expect` corrector, and the soft input constraint.

Placement is tested as pure geometry against synthetic rects and a stub screen -- `place_near`
never needs a shown widget, which is what keeps it testable at all under `offscreen`."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

from app.main_window import MainWindow, _verification_all_passed
from app.tour import CoachmarkBubble, TargetHalo, TourController, TourStep, _resolve_target


class _StubScreen:
    """Everything `place_near`/`place_centered` need from a QScreen."""

    def __init__(self, rect):
        self._rect = rect

    def availableGeometry(self):
        return self._rect


@pytest.fixture
def window(qtbot):
    win = MainWindow(offscreen=True)
    qtbot.addWidget(win)
    return win


@pytest.fixture
def bubble(qtbot):
    widget = CoachmarkBubble()
    qtbot.addWidget(widget)
    widget.set_step("Step 1 — Load the mesh", "Pick the input STL.", 0, 3)
    return widget


def _controller(window, steps, title="Test tour"):
    controller = TourController(window, steps, title)
    controller.start()
    return controller


def _press_event():
    return QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(1, 1), QPointF(1, 1),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def _escape_event():
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)


THREE_STEPS = [
    TourStep(target=None, title="Step 1", body="First."),
    TourStep(target="run_btn", title="Step 2", body="Second."),
    TourStep(target="outline", title="Step 3", body="Third."),
]


# ---- placement geometry -------------------------------------------------
def test_place_near_prefers_the_right_side(bubble):
    screen = _StubScreen(QRect(0, 0, 1920, 1080))
    target = QRect(900, 500, 80, 30)
    side = bubble.place_near(target, screen)
    # Bubble sits to the RIGHT of the target, so its pointer is on its own LEFT edge.
    assert side == "left"
    assert bubble.x() > target.right()


def test_place_near_flips_left_at_the_screen_right_edge(bubble):
    screen = _StubScreen(QRect(0, 0, 1920, 1080))
    target = QRect(1860, 500, 50, 30)
    side = bubble.place_near(target, screen)
    assert side == "right"
    assert bubble.x() + bubble.width() <= target.left()


def test_place_near_goes_below_when_neither_side_fits(bubble):
    # A screen only a little wider than the bubble, with the target dead center: no room either
    # side, but plenty above/below.
    screen = _StubScreen(QRect(0, 0, bubble.width() + 40, 1400))
    target = QRect(screen.availableGeometry().center().x() - 20, 200, 40, 30)
    side = bubble.place_near(target, screen)
    assert side == "top"
    assert bubble.y() > target.bottom()


def test_place_near_falls_back_to_centered_on_a_tiny_screen(bubble):
    screen = _StubScreen(QRect(0, 0, 300, 200))
    target = QRect(140, 90, 20, 20)
    assert bubble.place_near(target, screen) is None
    assert bubble._pointer_side is None


def test_pointer_stays_off_the_bubble_corners(bubble):
    screen = _StubScreen(QRect(0, 0, 1920, 1080))
    # Target far above the bubble's own vertical span: the pointer wants to aim off the top.
    bubble.place_near(QRect(900, 0, 80, 10), screen)
    assert bubble._pointer_side == "left"
    assert 0 < bubble._pointer_along < bubble.height()


def test_bubble_grows_taller_for_longer_text_but_never_wider(bubble):
    short = bubble.height()
    width = bubble.width()
    bubble.set_step("Title", "word " * 200, 0, 3)
    assert bubble.height() > short
    assert bubble.width() == width


# ---- bubble content -----------------------------------------------------
def test_step_counter_and_back_visibility(bubble):
    assert bubble.counter_label.text() == "1 / 3"
    assert not bubble.back_btn.isVisibleTo(bubble)
    bubble.set_step("Step 2", "Second.", 1, 3)
    assert bubble.counter_label.text() == "2 / 3"
    assert bubble.back_btn.isVisibleTo(bubble)


def test_waiting_state_replaces_the_next_button(bubble):
    bubble.set_step("Step 2", "Click Run.", 1, 3, waiting="waiting — click Run to continue")
    assert not bubble.next_btn.isVisibleTo(bubble)
    assert bubble.waiting_label.isVisibleTo(bubble)
    bubble.set_step("Step 3", "Read this.", 2, 3)
    assert bubble.next_btn.isVisibleTo(bubble)
    assert not bubble.waiting_label.isVisibleTo(bubble)


def test_correction_state_round_trips(bubble):
    bubble.show_correction(["Sections: 40 → set to 80"])
    assert bubble.in_correction
    assert "Sections" in bubble.body_label.text()
    assert bubble.next_btn.text() == "Re-check"
    bubble.clear_correction()
    assert not bubble.in_correction
    assert bubble.body_label.text() == "Pick the input STL."
    assert bubble.next_btn.text().startswith("Next")


# ---- halo ---------------------------------------------------------------
def test_halo_geometry_grows_the_target_rect(qtbot):
    halo = TargetHalo()
    qtbot.addWidget(halo)
    halo.set_target_rect(QRect(100, 200, 60, 24))
    assert halo.geometry() == QRect(94, 194, 72, 36)


def test_halo_flash_goes_to_full_brightness(qtbot):
    halo = TargetHalo()
    qtbot.addWidget(halo)
    halo._tick()
    halo.flash()
    assert halo.alpha() == 255


# ---- controller ---------------------------------------------------------
def test_target_paths_resolve_and_typos_raise(window):
    assert _resolve_target(window, "run_btn") is window.run_btn
    assert _resolve_target(window, "page_stations.table") is window.page_stations.table
    with pytest.raises(AttributeError):
        _resolve_target(window, "run_buttonn")


def test_every_signal_key_and_on_enter_action_resolves(window):
    """The whole table, not just the keys a demo happens to use today: a signal renamed on
    MainWindow would otherwise only surface as an AttributeError mid-tour, in front of a user."""
    for key, getter in TourController.SIGNAL_KEYS.items():
        assert getter(window) is not None, key
    for key, action in TourController.ON_ENTER.items():
        action(window)
        assert window.outline.currentItem() is not None, key


def test_next_advances_back_returns(window):
    controller = _controller(window, THREE_STEPS)
    assert controller.index == 0
    controller.bubble.next_clicked.emit()
    assert controller.index == 1
    controller.bubble.next_clicked.emit()
    assert controller.index == 2
    controller.bubble.back_clicked.emit()
    assert controller.index == 1
    controller.bubble.back_clicked.emit()
    assert controller.index == 0
    controller.bubble.back_clicked.emit()  # Back on step 1 must not end the tour
    assert controller.index == 0
    assert controller.active
    controller.cancel()


def test_next_on_the_last_step_finishes_once(window):
    controller = _controller(window, THREE_STEPS)
    finishes = []
    controller.finished.connect(lambda: finishes.append(True))
    for _ in range(3):
        controller.bubble.next_clicked.emit()
    assert finishes == [True]
    assert not controller.active
    controller.cancel()  # a second cancel must not emit again
    assert finishes == [True]


def test_escape_finishes_the_tour(window):
    controller = _controller(window, THREE_STEPS)
    finishes = []
    controller.finished.connect(lambda: finishes.append(True))
    assert controller.eventFilter(window, _escape_event()) is True
    assert finishes == [True]
    assert not controller.active


def test_signal_advance(window):
    steps = [TourStep(target=None, title="Step 1", body="First."),
             TourStep(target=None, title="Step 2", body="Click Analyze.",
                      advance="analyze_done"),
             TourStep(target=None, title="Step 3", body="Done.")]
    controller = _controller(window, steps)
    controller.bubble.next_clicked.emit()
    assert controller.index == 1
    assert not controller.bubble.next_btn.isVisibleTo(controller.bubble)
    window.analyze_finished.emit()
    assert controller.index == 2
    controller.cancel()


def test_signal_advance_captures_the_signals_arguments(window):
    steps = [TourStep(target=None, title="Step 1", body="Run it.", advance="rebuild_done"),
             TourStep(target=None, title="Step 2", body="Look at what failed.")]
    controller = _controller(window, steps)
    window.rebuild_finished.emit(False)
    assert controller.index == 1
    assert controller.last_signal_args == (False,)
    controller.cancel()


def test_a_finished_step_stops_listening_to_its_signal(window):
    steps = [TourStep(target=None, title="Step 1", body="Run it.", advance="rebuild_done"),
             TourStep(target=None, title="Step 2", body="Read this."),
             TourStep(target=None, title="Step 3", body="And this.")]
    controller = _controller(window, steps)
    window.rebuild_finished.emit(True)
    assert controller.index == 1
    window.rebuild_finished.emit(True)  # step 2 does not listen -- must not skip ahead
    assert controller.index == 1
    controller.cancel()


def test_on_enter_selects_an_outline_node(window):
    steps = [TourStep(target=None, title="Step 1", body="Output.",
                      on_enter="select_outline_output")]
    controller = _controller(window, steps)
    assert window.outline.currentItem() is window.node_output
    controller.cancel()


def test_hidden_target_gets_a_where_to_find_it_prefix(window):
    """The window is never shown in these tests, so every widget reports invisible -- the same
    state a dock closed mid-tour produces, and the bubble has to say where the control went
    instead of pointing at nothing."""
    steps = [TourStep(target="sections_spin", title="Step 1", body="Set it to 80.")]
    controller = _controller(window, steps)
    body = controller.bubble.body_label.text()
    assert "Sections is in the Details dock" in body
    assert "Set it to 80." in body
    assert controller.bubble._pointer_side is None
    controller.cancel()


def test_visible_target_is_anchored_with_a_pointer_and_a_halo(window, qtbot):
    window.show()
    qtbot.waitExposed(window)
    steps = [TourStep(target="run_btn", title="Step 1", body="Click Run.")]
    controller = _controller(window, steps)
    assert controller.bubble.body_label.text() == "Click Run."
    assert controller.bubble._pointer_side is not None
    target_rect = window.run_btn.rect().translated(window.run_btn.mapToGlobal(QPoint(0, 0)))
    assert controller.halo.geometry().contains(target_rect)
    controller.cancel()


def test_unknown_advance_and_on_enter_are_rejected(window):
    with pytest.raises(ValueError):
        TourController(window, [TourStep(None, "T", "B", advance="nope")], "t")
    with pytest.raises(ValueError):
        TourController(window, [TourStep(None, "T", "B", on_enter="nope")], "t")


def test_expect_is_rejected_on_a_signal_advanced_step(window):
    step = TourStep(None, "T", "B", advance="rebuild_done", expect={"sections_spin": 80})
    with pytest.raises(ValueError):
        TourController(window, [step], "t")


# ---- expect corrector ---------------------------------------------------
def test_expect_mismatch_blocks_the_step_then_recheck_advances(window):
    steps = [TourStep(target=None, title="Step 1", body="Check settings.",
                      expect={"sections_spin": 80, "adaptive_check": True}),
             TourStep(target=None, title="Step 2", body="Now run.")]
    window.sections_spin.setValue(40)
    window.adaptive_check.setChecked(False)
    controller = _controller(window, steps)
    controller.bubble.next_clicked.emit()
    assert controller.index == 0
    assert controller.bubble.in_correction
    body = controller.bubble.body_label.text()
    assert "Sections: 40 → set to 80" in body
    assert "Adaptive: off → set to on" in body

    window.sections_spin.setValue(80)
    window.adaptive_check.setChecked(True)
    controller.bubble.next_clicked.emit()
    assert controller.index == 1
    assert not controller.bubble.in_correction
    controller.cancel()


def test_expect_matches_floats_and_combo_text(window):
    window.chord_tol_auto.setChecked(False)
    window.chord_tol_spin.setValue(5.0)
    window.axis_combo.setCurrentText("z")
    step = TourStep(None, "T", "B", expect={"chord_tol_spin": 5.0, "axis_combo": "z",
                                            "chord_tol_auto": False})
    controller = TourController(window, [step], "t")
    assert controller.check_expectations(step) == []
    window.chord_tol_spin.setValue(4.25)
    assert controller.check_expectations(step) == ["Chord tol: 4.25 → set to 5"]


# ---- soft input constraint ---------------------------------------------
def test_constraint_allows_the_target_and_swallows_everything_else(window):
    steps = [TourStep(target="run_btn", title="Step 1", body="Click Run.", constrain=True)]
    controller = _controller(window, steps)
    assert controller.eventFilter(window.run_btn, _press_event()) is False
    assert controller.eventFilter(window.outline, _press_event()) is True
    # The menubar stays live so Quit/Cancel can never be trapped by a tour.
    assert controller.eventFilter(window.menuBar(), _press_event()) is False
    controller.cancel()


def test_unconstrained_step_swallows_nothing(window):
    steps = [TourStep(target="run_btn", title="Step 1", body="Read this.")]
    controller = _controller(window, steps)
    assert controller.eventFilter(window.outline, _press_event()) is False
    controller.cancel()


def test_event_filter_is_inert_and_removed_after_finish(window):
    """A tour that ended must leave application-wide event dispatch exactly as it found it --
    a stale filter swallowing clicks after "End tour" would look like a frozen app."""
    steps = [TourStep(target="run_btn", title="Step 1", body="Click Run.", constrain=True)]
    controller = _controller(window, steps)
    assert controller.filter_installed is True
    controller.cancel()
    assert controller.filter_installed is False
    assert controller.eventFilter(window.outline, _press_event()) is False
    assert controller.eventFilter(window, _escape_event()) is False


# ---- rebuild_finished's payload ----------------------------------------
def test_verification_all_passed():
    passing = {"verification": {"watertight": {"pass": True},
                                "volume": {"pass": True},
                                "deviation": {"pass": None}}}
    failing = {"verification": {"watertight": {"pass": True},
                                "bounds": {"z": {"pass": False}}}}
    assert _verification_all_passed(passing) is True
    assert _verification_all_passed(failing) is False
    # Nothing verified is not the same as everything passed.
    assert _verification_all_passed({}) is False
    assert _verification_all_passed({"verification": {"error": "boom"}}) is False


def test_main_window_emits_run_lifecycle_signals(window, qtbot):
    with qtbot.waitSignal(window.run_failed, timeout=1000) as blocker:
        window._on_failed("GeometryError", "no loops")
    assert blocker.args == ["GeometryError", "no loops"]
