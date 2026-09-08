"""Guided demos (`app/demos.py`, `app/demo_scripts.py`): the catalog, the thirteen scripts, and
the picker.

Nothing here generates a mesh or runs the engine -- M13's input alone is 253 MB and its Analyze
step can run ~19 minutes, so a test that touched the heavy path would be a test nobody runs. What
IS checked is everything that can go wrong silently: an argument drifting away from the
milestone's official command, a target attribute that no longer exists on the window, a branch
index pointing at the wrong paragraph, and the M9 lesson being watered down."""
import pytest
from PySide6.QtWidgets import QListWidget

from app import demos
from app.main_window import MainWindow
from app.tour import TourController, _resolve_target

MILESTONES = [f"M{i}" for i in range(1, 14)]


@pytest.fixture
def window(qtbot):
    win = MainWindow(offscreen=True)
    qtbot.addWidget(win)
    return win


# ---- the catalog --------------------------------------------------------
def test_thirteen_demos_named_and_unique():
    assert list(demos.DEMOS) == MILESTONES
    names = [d.name for d in demos.DEMOS.values()]
    assert len(set(names)) == 13
    for milestone, demo in demos.DEMOS.items():
        assert demo.milestone == milestone
        assert demo.name.strip()
        assert not demo.name.lower().startswith("demo ")
        assert demo.blurb.strip()
        assert demo.steps, f"{milestone} has no script"


# Copied by hand from HANDOFF.md §3.2 ("Exact commands") -- the arguments the frozen scorer
# actually runs each milestone with, which is what makes a demo a demonstration of a validated
# result rather than of a plausible one. Milestones without a `--units` flag there run at the
# CLI default, mm. If this ever has to change, change HANDOFF first and copy from it again.
EXPECTED_ARGS = {
    "M1": {"axis": "z", "units": "mm", "sections": 40, "chord_tol": 0.5, "adaptive": False},
    "M2": {"axis": "z", "units": "mm", "sections": 40, "chord_tol": 0.5, "adaptive": False},
    "M3": {"axis": "z", "units": "mm", "sections": 60, "chord_tol": 0.5, "adaptive": False},
    "M4": {"axis": "z", "units": "mm", "sections": 80, "chord_tol": 0.5, "adaptive": False},
    "M5": {"axis": "z", "units": "mm", "sections": 40, "chord_tol": 0.5, "adaptive": True},
    "M6": {"axis": "z", "units": "mm", "sections": 60, "chord_tol": 0.5, "adaptive": False},
    "M7": {"axis": "z", "units": "mm", "sections": 60, "chord_tol": 0.5, "adaptive": False},
    "M8": {"axis": "z", "units": "mm", "sections": 80, "chord_tol": 0.5, "adaptive": True},
    "M9": {"axis": "z", "units": "mm", "sections": 80, "chord_tol": 5.0, "adaptive": True},
    "M10": {"axis": "auto", "units": "in", "sections": 80, "chord_tol": 0.0125, "adaptive": True},
    "M11": {"axis": "z", "units": "mm", "sections": 40, "chord_tol": 0.5, "adaptive": False},
    "M12": {"axis": "z", "units": "mm", "sections": 120, "chord_tol": 0.5, "adaptive": True},
    "M13": {"axis": "auto", "units": "in", "sections": 120, "chord_tol": 8.0, "adaptive": True},
}


def test_args_match_the_official_commands():
    assert {m: d.args for m, d in demos.DEMOS.items()} == EXPECTED_ARGS


def test_heavy_and_skimage_flags():
    for milestone, demo in demos.DEMOS.items():
        heavy = milestone in ("M9", "M13")
        assert demo.heavy is heavy, milestone
        assert demo.needs_skimage is heavy, milestone
    # The confirmation the picker shows must name the cost, not gesture at it.
    assert "44 MB" in demos.HEAVY_WARNING["M9"]
    for fragment in ("253 MB", "several minutes", "19 minutes"):
        assert fragment in demos.HEAVY_WARNING["M13"], fragment
    assert set(demos.HEAVY_BADGE) == {"M9", "M13"}


def test_the_manual_lists_the_same_thirteen_names():
    """The Guided-demos manual page names all thirteen in a table. Two lists of the same names in
    two files drift; this is what stops the manual describing a demo the picker does not have."""
    import os

    from app.help import HELP_DIR
    page = open(os.path.join(HELP_DIR, "10_demos.md"), encoding="utf-8").read()
    for milestone, demo in demos.DEMOS.items():
        assert f"| {milestone} | {demo.name} |" in page, f"{milestone} — {demo.name}"


def test_demo_stl_path_is_relative_to_the_repo_root():
    assert demos.demo_stl_path("M7") == "harness/truth/M7.stl"


# ---- every step of every script ----------------------------------------
def test_every_target_resolves_on_a_real_window(window):
    for milestone, demo in demos.DEMOS.items():
        for step in demo.steps:
            if step.target is None:
                continue
            assert _resolve_target(window, step.target) is not None, \
                f"{milestone}: {step.target!r} ({step.title})"


def test_every_advance_key_is_known():
    allowed = set(TourController.SIGNAL_KEYS) | {"next"}
    for milestone, demo in demos.DEMOS.items():
        for step in demo.steps:
            assert step.advance in allowed, f"{milestone}: {step.advance!r}"


def test_expect_only_on_next_steps():
    for milestone, demo in demos.DEMOS.items():
        for step in demo.steps:
            if step.expect:
                assert step.advance == "next", f"{milestone}: {step.title}"


def test_every_on_enter_is_one_of_the_four():
    for milestone, demo in demos.DEMOS.items():
        for step in demo.steps:
            if step.on_enter:
                assert step.on_enter in TourController.ON_ENTER, \
                    f"{milestone}: {step.on_enter!r}"


def test_every_script_expects_its_own_official_args():
    """The review step's `expect` is what actually stops a reader running the demo at settings
    the milestone was never validated at -- so it has to agree with the catalog."""
    for milestone, demo in demos.DEMOS.items():
        expects = [s.expect for s in demo.steps if s.expect]
        assert len(expects) == 1, f"{milestone} should have exactly one review step"
        assert expects[0] == {
            "axis_combo": demo.args["axis"], "units_combo": demo.args["units"],
            "sections_spin": demo.args["sections"], "chord_tol_auto": False,
            "chord_tol_spin": demo.args["chord_tol"],
            "adaptive_check": demo.args["adaptive"]}, milestone


def test_every_script_runs_analyze_then_rebuild():
    for milestone, demo in demos.DEMOS.items():
        advances = [s.advance for s in demo.steps]
        assert advances.count("analyze_done") == 1, milestone
        assert advances.count("rebuild_done") == 1, milestone
        assert advances.index("analyze_done") < advances.index("rebuild_done"), milestone


def test_every_script_constructs_as_a_tour(window):
    """`TourController.__init__` validates advance keys, `expect` placement, on_enter names and
    branch indices -- so constructing all thirteen is the cheapest full-script check there is."""
    for milestone, demo in demos.DEMOS.items():
        TourController(window, demo.steps, demo.name)


def test_the_chord_tolerance_box_can_hold_every_demo_value(window):
    """M10's official 0.0125 mm is below what a two-decimal QDoubleSpinBox can represent; the box
    was widened to four decimals for exactly this. A regression there would make M10's review
    step permanently unsatisfiable."""
    for milestone, demo in demos.DEMOS.items():
        window.chord_tol_spin.setValue(demo.args["chord_tol"])
        assert window.chord_tol_spin.value() == pytest.approx(demo.args["chord_tol"]), milestone


# ---- M9: the branch, and the lesson ------------------------------------
def test_only_m9_branches_and_its_indices_are_in_range():
    for milestone, demo in demos.DEMOS.items():
        for step in demo.steps:
            if milestone != "M9":
                assert step.branch is None, f"{milestone}: {step.title}"
            elif step.branch is not None:
                assert len(step.branch) == 2
                assert all(0 <= j < len(demo.steps) for j in step.branch), step.branch


def test_m9_run_step_branches_to_a_pass_and_a_fail_path():
    steps = demos.DEMOS["M9"].steps
    run_index = next(i for i, s in enumerate(steps) if s.advance == "rebuild_done")
    branch = steps[run_index].branch
    assert branch is not None
    passed, failed = branch
    assert passed != failed
    # The pass branch acknowledges the surprise; the fail branch is the teaching sequence and is
    # the one that has to open on the amber Bounds Z row.
    assert "passed" in steps[passed].title.lower()
    assert "bounds z" in steps[failed].title.lower()
    assert steps[failed].on_enter == "select_outline_output"


def test_m9_pass_branch_rejoins_the_wrap_up():
    steps = demos.DEMOS["M9"].steps
    run_index = next(i for i, s in enumerate(steps) if s.advance == "rebuild_done")
    passed = steps[run_index].branch[0]
    rejoin = steps[passed].branch
    assert rejoin is not None and rejoin[0] == rejoin[1]
    assert "done" in steps[rejoin[0]].title.lower()


@pytest.mark.parametrize("verification_passed, expected_branch", [(False, 1), (True, 0)])
def test_m9_branch_follows_the_real_verification_result(window, verification_passed,
                                                        expected_branch):
    """The branch reads `MainWindow.rebuild_finished`'s bool, which is
    `_verification_all_passed(report)` -- and that returns False for a MISSING verification block
    as well as a failed check, deliberately, so "nothing was proven" lands on the teaching path
    rather than the reassuring one. Driven here by emitting the signal directly: the point is the
    branch, not a five-minute M9 rebuild."""
    steps = demos.DEMOS["M9"].steps
    run_index = next(i for i, s in enumerate(steps) if s.advance == "rebuild_done")
    controller = TourController(window, steps, "M9")
    controller.start()
    try:
        controller._show_step(run_index)
        window.rebuild_finished.emit(verification_passed)
        assert controller.index == steps[run_index].branch[expected_branch]
    finally:
        controller.cancel()


def test_m9_pass_path_reaches_the_wrap_up_without_the_teaching_steps(window):
    steps = demos.DEMOS["M9"].steps
    run_index = next(i for i, s in enumerate(steps) if s.advance == "rebuild_done")
    controller = TourController(window, steps, "M9")
    controller.start()
    try:
        controller._show_step(run_index)
        window.rebuild_finished.emit(True)
        passed_index = controller.index
        controller._advance()
        assert controller.index == len(steps) - 1        # the wrap-up, not the teaching sequence
        assert controller.index > passed_index + 1
    finally:
        controller.cancel()


def test_m9_teaches_the_real_lesson():
    """Lesson canary. The M9 demo exists to teach one specific thing -- that a failed Bounds Z on
    a coarse, noisy input is informational, that Deviation is the check to trust, and that no
    chord-tol value moves it (`pipeline/engine.py::_axial_bounds_hint`). If an edit ever softens
    that into "a check failed, try adjusting things", this fails."""
    text = " ".join(f"{s.title} {s.body}" for s in demos.DEMOS["M9"].steps)
    for fragment in ("Deviation", "informational", "chord-tol"):
        assert fragment in text, fragment
    assert "21 mm" in text and "2.3 mm" in text and "10 mm" in text
    assert "4.93" in text and "3.5" in text     # the values that were actually tried
    assert "trust Deviation" in text


def test_m9_run_step_is_at_the_official_settings():
    demo = demos.DEMOS["M9"]
    assert demo.args == {"axis": "z", "units": "mm", "sections": 80, "chord_tol": 5.0,
                         "adaptive": True}
    run = next(s for s in demo.steps if s.advance == "rebuild_done")
    assert "80" in run.body and "5 mm" in run.body


# ---- the picker and the start flow -------------------------------------
def test_picker_lists_all_thirteen_with_badges(qtbot, window):
    picker = demos.DemoPickerDialog(window)
    qtbot.addWidget(picker)
    assert isinstance(picker.list, QListWidget)
    assert picker.list.count() == 13
    picker.relayout_rows()
    for i, milestone in enumerate(MILESTONES):
        item = picker.list.item(i)
        assert item.data(0x0100) == milestone  # Qt.ItemDataRole.UserRole
        row = picker.list.itemWidget(item)
        assert milestone in row.title.text()
        if milestone in ("M9", "M13"):
            assert row.badge is not None and row.badge.text() == demos.HEAVY_BADGE[milestone]
        else:
            assert row.badge is None
        # The blurb has to have been given real height for its wrapped text, or it renders
        # clipped to one truncated line (the first screenshot pass, 2026-09-08).
        assert item.sizeHint().height() >= row.title.height() + row.blurb.height()


def test_picker_selection_reports_the_milestone(qtbot, window):
    picker = demos.DemoPickerDialog(window)
    qtbot.addWidget(picker)
    picker.list.setCurrentRow(3)
    assert picker.selected_milestone() == "M4"


def test_start_demo_refuses_while_a_run_is_in_flight(window, monkeypatch):
    warned = []
    monkeypatch.setattr(demos.QMessageBox, "warning",
                        lambda *a, **k: warned.append(a[-1]))
    window.cancel_btn.setEnabled(True)   # what `_set_running(True)` does
    try:
        assert window.start_demo("M1") is False
        assert window._tour is None
        assert warned, "the refusal must say why"
    finally:
        window.cancel_btn.setEnabled(False)


def test_start_demo_rejects_an_unknown_milestone(window):
    assert window.start_demo("M99") is False


def test_start_demo_warns_instead_of_generating_without_skimage(window, monkeypatch):
    """M9/M13's generators import scikit-image. Without it the demo must stop at a message box
    quoting the install command -- not part-generate and fail somewhere deeper."""
    warned = []
    monkeypatch.setattr(demos, "skimage_available", lambda: False)
    monkeypatch.setattr(demos.QMessageBox, "warning", lambda *a, **k: warned.append(a[-1]))
    started = []
    monkeypatch.setattr(demos, "begin_mesh_generation",
                        lambda *a, **k: started.append(a))
    assert window.start_demo("M9") is False
    assert not started
    assert warned and "scikit-image" in warned[0]


def test_start_demo_asks_before_a_heavy_generation(window, monkeypatch, tmp_path):
    """The confirmation only appears when the mesh is not already on disk -- otherwise every M9
    start would nag about a cost that is not going to be paid."""
    asked = []
    monkeypatch.setattr(demos, "skimage_available", lambda: True)
    monkeypatch.setattr(demos.os.path, "exists", lambda p: False)
    monkeypatch.setattr(demos.QMessageBox, "question",
                        lambda *a, **k: asked.append(a[-3]) or demos.QMessageBox.StandardButton.No)
    assert window.start_demo("M13") is False
    assert asked and "253 MB" in asked[0]
