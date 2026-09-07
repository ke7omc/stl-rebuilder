"""Dashboard dial state-machine tests (2026-09-05): stage->dial wiring, done/failed handling.
See app/dashboard.py's module docstring -- this is the GUI-side half of the fix for M13-scale
runs looking stalled (the engine-side half, new "scan"/"build" progress stages, is covered by
tests/api/test_engine.py::test_rebuild_reports_progress_per_station).

`on_stage`'s `frac` argument is documented as whatever `on_progress` actually delivers -- for
every GUI run that's `_RefineProgressRelay`'s combined, cross-stage value, NOT a raw stage-local
0..1 (`refine_passes` defaults to 1, so the relay is always in front of a GUI rebuild). Most
tests below only assert dial STATE, which doesn't depend on the exact frac value, so any
in-range number works; the two that assert an exact needle `_target` route through the real
relay (`_relay_frac`) instead of hand-picking a value, so they'd catch a regression in either
side of that contract."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QTextEdit

from app.dashboard import Dashboard
from pipeline.engine import _RefineProgressRelay


def _relay_fracs(events, refine_pass_starts_at=None):
    """Feed `events` ([(stage, local_frac), ...]) through one real `_RefineProgressRelay` and
    return the resulting [(stage, global_frac), ...] -- i.e. exactly what `Dashboard.on_stage`
    receives in production. `refine_pass_starts_at`, if given, is the index at which
    `begin_refine_pass()` is called first (simulating a verify-and-refine retry)."""
    captured = []
    relay = _RefineProgressRelay(lambda s, f, m: captured.append((s, f)), refine_enabled=True)
    for i, (stage, local_frac) in enumerate(events):
        if refine_pass_starts_at is not None and i == refine_pass_starts_at:
            relay.begin_refine_pass()
        relay(stage, local_frac, "x")
    return captured


@pytest.fixture
def dashboard(qtbot):
    db = Dashboard(QTextEdit())
    qtbot.addWidget(db)
    return db


def _states(dashboard):
    return [d._state for d in dashboard.dials]


def test_reset_is_all_idle(dashboard):
    dashboard.on_stage("stations", 0.5, "x")
    dashboard.reset()
    assert _states(dashboard) == ["idle", "idle", "idle", "idle", "idle"]


def test_stage_progression_marks_earlier_dials_done(dashboard):
    # Dial 0 (ANALYZE) is fed by a separate operation (`engine.analyze()`, AnalyzeWorker); it
    # goes "done" as soon as any rebuild stage fires, same as any other earlier-than-current
    # dial -- this test never emits an "analyze" stage, so it's exercising that fall-through,
    # not ANALYZE's own progress.
    events = _relay_fracs([("load", 0.0), ("scan", 0.5), ("stations", 0.3), ("build", 0.1)])

    dashboard.on_stage(*events[0], "loading")
    assert _states(dashboard) == ["done", "active", "idle", "idle", "idle"]

    dashboard.on_stage(*events[1], "scanning")
    assert _states(dashboard) == ["done", "done", "active", "idle", "idle"]
    # The needle shows the SCAN dial's own local progress (0.5), not the tiny sliver of overall
    # run progress the relay squeezes "scan" into -- this is the exact regression this whole
    # test file guards: recovering local progress via `engine.stage_local_progress` is what
    # keeps the dial from flatlining once a real engine run reaches later stages.
    assert dashboard.dials[2]._target == pytest.approx(0.5)

    dashboard.on_stage(*events[2], "station 3/10")
    assert _states(dashboard) == ["done", "done", "done", "active", "idle"]
    assert dashboard.dials[3]._target == pytest.approx(0.3)

    dashboard.on_stage(*events[3], "boolean cut 1/3")
    assert _states(dashboard) == ["done", "done", "done", "done", "active"]
    assert dashboard.dials[4]._target == pytest.approx(0.1)


def test_refine_stage_shares_sectioning_dial(dashboard):
    events = _relay_fracs(
        [("load", 0.0), ("stations", 1.0), ("build", 1.0),
         ("load", 0.0), ("stations", 0.3), ("stations", 0.4)],
        refine_pass_starts_at=3)
    for stage, frac in events[:3]:
        dashboard.on_stage(stage, frac, "x")
    assert _states(dashboard) == ["done", "done", "done", "done", "active"]

    stage, frac = events[-1]
    assert stage == "refine"  # the retry pass's "stations" is relabelled by the relay
    dashboard.on_stage(stage, frac, "refine station 4/10")
    # BUILD (dial 4) is NOT reset to idle just because a retry revisited an earlier dial --
    # see the regression test below for why that matters.
    assert _states(dashboard) == ["done", "done", "done", "active", "active"]
    assert dashboard.dials[3]._target == pytest.approx(0.4)


def test_retry_pass_does_not_erase_later_dials_progress(dashboard):
    """Regression test for exactly the bug Brady reported live-testing M8 (2026-09-06): a
    verify-and-refine retry pass re-emits "load" (mapped to the LOAD dial), and dials AFTER it
    must NOT be wiped back to idle just because an earlier dial is active again -- that erased
    the SECTIONING/BUILD dials' completed state (dropping them to 0%) the instant a retry
    started, which is exactly the "erases usable feedback" complaint."""
    events = _relay_fracs(
        [("load", 0.0), ("stations", 1.0), ("build", 1.0), ("load", 0.0)],
        refine_pass_starts_at=3)
    for stage, frac in events[:3]:
        dashboard.on_stage(stage, frac, "x")
    assert _states(dashboard) == ["done", "done", "done", "done", "active"]

    # The retry pass's own "load" event fires -- SECTIONING and BUILD must stay exactly as they
    # were (done/active), not snap back to idle.
    stage, frac = events[3]
    assert stage == "load"
    dashboard.on_stage(stage, frac, "loading")
    assert _states(dashboard) == ["done", "active", "done", "done", "active"]


def test_unknown_stage_is_ignored(dashboard):
    dashboard.on_stage("stations", 0.5, "x")
    before = _states(dashboard)
    dashboard.on_stage("some_future_stage", 0.9, "y")
    assert _states(dashboard) == before


def test_on_done_marks_everything_done(dashboard):
    dashboard.on_stage("load", 0.0, "loading")
    dashboard.on_done()
    assert _states(dashboard) == ["done", "done", "done", "done", "done"]


def test_on_failed_marks_current_stage_error_and_later_idle(dashboard):
    dashboard.on_stage("stations", 0.5, "x")
    dashboard.on_failed("stations")
    assert _states(dashboard) == ["done", "done", "done", "error", "idle"]


def test_on_failed_with_unknown_last_stage_touches_nothing(dashboard):
    dashboard.on_stage("stations", 0.5, "x")
    before = _states(dashboard)
    dashboard.on_failed(None)
    assert _states(dashboard) == before


def test_done_dial_snaps_instantly_instead_of_animating(dashboard):
    """Regression test for Brady's 2026-09-06 report that the first three dials "seemed to be
    moving at the same time" on a fast milestone: a completed dial used to animate its catch-up
    to 100% over several ticks, so on a fast run the NEXT dial could already be moving before
    the PREVIOUS one's catch-up animation finished, making sequential phases look simultaneous.
    A dial marked done must show 100% on the very next paint, with no ticks in between."""
    dial = dashboard.dials[0]
    dial.set_state("active")
    dial.set_value(0.3)
    dial._tick()  # let it ease partway, so the "already at target" case isn't a no-op test
    assert dial._display < 0.3
    dial.set_state("done")
    assert dial._display == 1.0
    assert dial._target == 1.0


def test_analyze_stage_maps_to_its_own_dial(dashboard):
    """Brady, 2026-09-06: "add an Analyze dial on the left side of Load" -- Analyze is a
    separate operation (AnalyzeWorker/engine.analyze()) from a rebuild, fed through the same
    `on_stage` API under the "analyze" stage name."""
    dashboard.on_stage("analyze", 0.4, "orienting mesh")
    assert _states(dashboard) == ["active", "idle", "idle", "idle", "idle"]
    assert dashboard.dials[0]._target == pytest.approx(0.4)

    dashboard.on_stage("load", 0.0, "loading")
    assert _states(dashboard) == ["done", "active", "idle", "idle", "idle"]


def test_dial_creeps_forward_between_real_updates_while_active(dashboard, monkeypatch):
    """Regression test for Brady's 2026-09-06 report: Analyze on an M13-scale STL has only a
    couple of real checkpoints (one blocking `trimesh.load()` call with no progress hooks of its
    own) across tens of seconds, and with no way to fill that gap the dial just sat still --
    indistinguishable, to a user who's used to M8 finishing in ~2 seconds, from being stuck. The
    needle must keep visibly advancing even with zero new `set_value()` calls."""
    import app.dashboard as dashboard_mod

    dial = dashboard.dials[0]
    now = [1000.0]
    monkeypatch.setattr(dashboard_mod.time, "monotonic", lambda: now[0])
    dial.set_state("active")
    dial.set_value(0.1)
    dial._tick()
    after_first_tick = dial._display
    assert after_first_tick > 0.0

    # No new set_value() calls -- just time passing and more ticks, simulating a long silent
    # phase. The display must keep climbing, not sit flat at whatever the first tick reached.
    for _ in range(20):
        now[0] += 1.0
        dial._tick()
    assert dial._display > after_first_tick
    # But it must never claim near-completion on creep alone -- see _CREEP_CEILING.
    assert dial._display < 0.5


def test_creep_resets_and_does_not_apply_when_not_active(dashboard, monkeypatch):
    import app.dashboard as dashboard_mod

    dial = dashboard.dials[0]
    now = [2000.0]
    monkeypatch.setattr(dashboard_mod.time, "monotonic", lambda: now[0])
    dial.set_state("idle")
    for _ in range(10):
        now[0] += 5.0
        dial._tick()
    # Idle must stay pinned at 0 -- creep only ever applies to the "active" state.
    assert dial._display == 0.0


def test_display_never_moves_backward_when_a_real_checkpoint_undershoots_creep(
        dashboard, monkeypatch):
    """Regression test for a real bug found measuring an actual M13 Analyze run (2026-09-06):
    `_weld_by_radius` alone took 160 of 182 total seconds between the checkpoints at frac 0.38
    and 0.56 -- long enough that creep, aimed at a ceiling anchored to the 0.38 checkpoint,
    could visibly advance the needle PAST 0.56 before the real 0.56 checkpoint ever arrived.
    Applying that lower real value naively would have snapped the needle backward -- worse than
    the original "looks stuck" bug this animation exists to fix. The display must only ever
    hold or advance, never retreat."""
    import app.dashboard as dashboard_mod

    dial = dashboard.dials[0]
    now = [0.0]
    monkeypatch.setattr(dashboard_mod.time, "monotonic", lambda: now[0])
    dial.set_state("active")
    dial.set_value(0.38)
    # Let creep run far enough to climb past 0.56 (the real, but LOWER, next checkpoint).
    for _ in range(60):
        now[0] += 3.0
        dial._tick()
    crept_past_next_checkpoint = dial._display
    assert crept_past_next_checkpoint > 0.56

    # The real next checkpoint arrives, reporting a value LOWER than creep already reached.
    dial.set_value(0.56)
    dial._tick()
    assert dial._display >= crept_past_next_checkpoint  # held, not snapped backward


def test_mark_analyze_done_only_touches_the_analyze_dial(dashboard):
    dashboard.on_stage("analyze", 0.5, "orienting mesh")
    dashboard.mark_analyze_done()
    assert _states(dashboard) == ["done", "idle", "idle", "idle", "idle"]


def test_reset_rebuild_dials_leaves_analyze_alone(dashboard):
    """Brady, 2026-09-06: chaining Analyze into Run (the "auto chord-tol needs Analyze first"
    path) must not blink the just-completed ANALYZE dial back to idle the instant the rebuild's
    own dials reset -- its "done" status is still true and still useful context."""
    dashboard.on_stage("analyze", 1.0, "analyze complete")
    dashboard.mark_analyze_done()
    dashboard.on_stage("stations", 0.5, "station 5/10")  # a stale dial state from a PRIOR run
    dashboard.reset_rebuild_dials()
    assert _states(dashboard) == ["done", "idle", "idle", "idle", "idle"]


def test_gauge_dial_reserves_room_for_the_caption_band(dashboard):
    """Regression test for a real bug (2026-09-07 design review): the caption line (drawn at
    rect.bottom()+12..+26) fell partly or fully past the widget's own minimum height, so a
    stage's status message was invisible during real runs. The dial's minimum height must leave
    enough room below the dial face for BOTH the percentage readout and the caption line."""
    dial = dashboard.dials[0]
    dial.set_state("active")
    dial.set_caption("scanning cross-sections 84/200")
    min_h = dial.minimumSizeHint().height() if dial.minimumSizeHint().height() > 0 else \
        dial.minimumSize().height()
    dial.resize(dial.width(), min_h)
    # rect.bottom() (dial face bottom) + 12 (caption gap) + 14 (caption line height) must fit
    # inside the widget's own minimum height -- this is exactly the arithmetic that used to
    # overflow (dial_size = height-34 left only ~16px below the face for a ~28px caption band).
    dial_size = max(min(dial.width() - 8, min_h - 62), 20)
    caption_bottom = 18 + dial_size + 12 + 14
    assert caption_bottom <= min_h


def test_mission_clock_starts_freezes_and_resets(dashboard, monkeypatch):
    import app.dashboard as dashboard_mod
    now = [1000.0]
    monkeypatch.setattr(dashboard_mod.time, "monotonic", lambda: now[0])
    clock = dashboard.mission_clock
    clock.start()
    now[0] += 5.0
    assert clock._current_elapsed() == pytest.approx(5.0)
    clock.freeze()
    now[0] += 100.0  # elapsed must not keep advancing once frozen
    assert clock._current_elapsed() == pytest.approx(5.0)
    clock.reset()
    assert clock._current_elapsed() == 0.0


def test_mission_clock_start_is_idempotent_across_a_chained_analyze_into_rebuild(dashboard, monkeypatch):
    """A Run that silently chains Analyze->Rebuild calls `Dashboard.mission_start()` at both
    entry points (main_window.py's run_analyze and _start_rebuild) -- the second call must NOT
    reset the clock, since it's one mission (one elapsed-time reading), not two."""
    import app.dashboard as dashboard_mod
    now = [2000.0]
    monkeypatch.setattr(dashboard_mod.time, "monotonic", lambda: now[0])
    dashboard.mission_start()
    now[0] += 30.0
    dashboard.mission_start()  # the chained rebuild's own call
    assert dashboard.mission_clock._current_elapsed() == pytest.approx(30.0)


def test_status_strip_reflects_stage_done_and_failure(dashboard):
    dashboard.on_stage("scan", 0.4, "scanning cross-sections")
    assert "SCAN" in dashboard.status_strip.text()
    dashboard.on_done()
    assert dashboard.status_strip.text() == "NOMINAL"
    dashboard.on_failed("build", "GeometryError")
    assert "FAULT" in dashboard.status_strip.text()
    assert "GeometryError" in dashboard.status_strip.text()


def test_status_strip_labels_a_cancel_as_aborted_not_a_fault(dashboard):
    """A user-requested Cancel isn't an engine fault -- distinct, less alarming wording."""
    dashboard.on_failed("build", "Cancelled")
    assert "ABORTED" in dashboard.status_strip.text()
    assert "Cancelled" not in dashboard.status_strip.text()


def test_sections_are_independently_resizable_via_splitter(dashboard):
    """Brady, 2026-09-06: "can the user independently size the various boxes... make the log
    box smaller and the geometry box bigger". The dial cluster/log must be a real QSplitter
    (not a plain fixed-ratio stacked layout) so dragging a handle reallocates space between
    them. (A third pane, a verification readout, briefly lived here too but was dropped the
    same day -- it duplicated the Details dock's Output page.)"""
    splitter = dashboard.findChild(QSplitter)
    assert splitter is not None
    assert splitter.count() == 2
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.isCollapsible(0) is False  # dials must stay legible, not collapse to 0px

    dashboard.resize(1400, 600)
    splitter.setSizes([120, 400])
    sizes = splitter.sizes()
    # Exact pixel sizes aren't guaranteed (minimum-size constraints can adjust them), but the
    # requested RATIO -- log much bigger than the dial cluster -- must survive.
    assert sizes[1] > sizes[0]
