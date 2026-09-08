"""Headless self-check. MISSION §12/G2: `python -m app --smoke <outdir>` loads
`harness/truth/M2.stl` (generating it if missing), runs analyze + rebuild through the real
worker path, and writes `<outdir>/smoke.json` + >= 3 PNG screenshots >= 20 KB. Must work under
`QT_QPA_PLATFORM=offscreen` -- see `app/viewport.py` for why the 3D view can't use the normal
`QtInteractor` path there."""
import json
import os
import sys
import traceback

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.worker import AnalyzeWorker, RebuildWorker, run_in_thread
from pipeline.engine import RebuildOptions

MIN_SCREENSHOT_BYTES = 20 * 1024


def _wait(signal):
    loop = QEventLoop()
    signal.connect(loop.quit)
    loop.exec()


def _run_worker_sync(worker, thread, done_signal_names):
    results = {}

    def _capture(name):
        def _cb(*args):
            results[name] = args
        return _cb

    for name in done_signal_names:
        getattr(worker, name).connect(_capture(name))
    thread.start()
    loop = QEventLoop()
    for name in done_signal_names:
        getattr(worker, name).connect(loop.quit)
    loop.exec()
    thread.wait(30000)
    return results


def run_smoke(outdir: str) -> int:
    os.makedirs(outdir, exist_ok=True)
    smoke_json_path = os.path.join(outdir, "smoke.json")
    screenshots = []

    app = QApplication.instance() or QApplication([])
    from app.theme import apply_theme
    apply_theme(app)
    try:
        truth_stl = os.path.join("harness", "truth", "M2.stl")
        if not os.path.exists(truth_stl):
            from harness import generators
            generators.make("M2")

        window = MainWindow(offscreen=True)
        window.input_path_edit.setText(truth_stl)
        window.output_path_edit.setText(os.path.join(outdir, "M2_rebuilt.step"))
        window.axis_combo.setCurrentText("z")
        window.units_combo.setCurrentText("mm")
        window.show()
        app.processEvents()

        shot1 = os.path.join(outdir, "01_launch.png")
        window.grab().save(shot1)
        screenshots.append(shot1)

        analyze_worker = AnalyzeWorker(truth_stl, "z", "mm")
        analyze_thread = run_in_thread(analyze_worker)
        result = _run_worker_sync(analyze_worker, analyze_thread, ["finished", "failed"])
        if "failed" in result:
            raise RuntimeError(f"analyze failed: {result['failed']}")
        analysis, preview_mesh = result["finished"]
        window._on_analyzed(analysis, preview_mesh)
        app.processEvents()

        shot2 = os.path.join(outdir, "02_analyzed.png")
        window.grab().save(shot2)
        screenshots.append(shot2)

        opts = RebuildOptions(
            input_stl=truth_stl, output=window.output_path_edit.text(), axis="z", units="mm",
            sections=40, chord_tol=analysis.suggested_chord_tol_mm,
            report=os.path.join(outdir, "M2_rebuilt.report.json"),
            stl=os.path.join(outdir, "M2_rebuilt.preview.stl"),
        )
        rebuild_worker = RebuildWorker(opts)
        rebuild_thread = run_in_thread(rebuild_worker)
        result = _run_worker_sync(rebuild_worker, rebuild_thread, ["finished", "failed"])
        if "failed" in result:
            raise RuntimeError(f"rebuild failed: {result['failed']}")
        rebuild_result, solid_mesh, input_mesh = result["finished"]
        window._on_rebuilt(rebuild_result, solid_mesh, input_mesh)
        app.processEvents()

        shot3 = os.path.join(outdir, "03_rebuilt.png")
        window.grab().save(shot3)
        screenshots.append(shot3)

        window.outline.setCurrentItem(window.node_stations)
        app.processEvents()
        shot4 = os.path.join(outdir, "04_stations.png")
        window.grab().save(shot4)
        screenshots.append(shot4)

        for path in screenshots:
            size = os.path.getsize(path)
            if size < MIN_SCREENSHOT_BYTES:
                raise RuntimeError(f"screenshot {path} too small ({size} bytes)")

        payload = {"ok": True, "screenshots": screenshots, "report": rebuild_result.report}
        with open(smoke_json_path, "w") as f:
            json.dump(payload, f, indent=2)
        return 0
    except Exception as exc:
        tb = traceback.format_exc()
        payload = {"ok": False, "screenshots": screenshots, "error": f"{type(exc).__name__}: {exc}", "traceback": tb}
        with open(smoke_json_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"smoke test failed: {exc}", file=sys.stderr)
        print(tb, file=sys.stderr)
        return 1


# Bubble/picker grabs are small widgets, not full 1400x900 windows -- the 20 KB floor that
# catches a blank main-window frame would reject a perfectly good 340px bubble.
MIN_WIDGET_BYTES = 1024


def _grab(widget, outdir, name, screenshots):
    from PySide6.QtWidgets import QApplication
    QApplication.instance().processEvents()
    path = os.path.join(outdir, name)
    widget.grab().save(path)
    screenshots.append(path)
    return path


def run_tour_smoke(outdir: str) -> int:
    """Screenshot pass for the help/demo system: the manual, the demo picker, the coachmark
    bubble in its three content states, and a REAL scripted walk of the M2 guided demo.

    The M2 walk is not a mock -- `start_demo` generates/loads the mesh and builds the tour, the
    config steps are advanced through the controller's own `_advance` (so the `expect` corrector
    has to be satisfied for real), and analyze/rebuild run through the same synchronous worker
    helpers `run_smoke` uses, which is what emits `analyze_finished`/`rebuild_finished` and
    advances the signal-advanced steps. A step-numbering or targeting mistake in the M2 script
    therefore fails HERE rather than in front of a reader.

    Note for whoever reads the PNGs: the bubble and halo are translucent top-level windows, so
    `grab()` renders their transparent margin as black. That is the capture, not the widget."""
    os.makedirs(outdir, exist_ok=True)
    json_path = os.path.join(outdir, "tour_smoke.json")
    screenshots = []

    app = QApplication.instance() or QApplication([])
    from app.theme import apply_theme
    apply_theme(app)
    try:
        from app.demos import DEMOS, DemoPickerDialog
        from app.help import HelpDialog
        from app.tour import CoachmarkBubble

        window = MainWindow(offscreen=True)
        window.show()
        app.processEvents()

        help_dialog = HelpDialog(window)
        help_dialog.show_page("00_quick_start")
        help_dialog.resize(900, 640)
        help_dialog.show()
        _grab(help_dialog, outdir, "help_dialog.png", screenshots)

        picker = DemoPickerDialog(window)
        picker.show()
        app.processEvents()
        _grab(picker, outdir, "demo_picker.png", screenshots)
        # Thirteen rows do not fit at 560x620, and the two heavy-milestone badges are on the two
        # rows furthest down -- so the first frame cannot show them. Scroll to the end and take a
        # second one, or the badges go unreviewed.
        picker.list.setCurrentRow(picker.list.count() - 1)
        picker.list.scrollToBottom()
        app.processEvents()
        _grab(picker, outdir, "demo_picker_bottom.png", screenshots)
        picker.close()
        help_dialog.close()

        bubble = CoachmarkBubble(window)
        bubble.set_step(
            "Adaptive stations",
            "Turn adaptive stations ON.\n\nAdaptive placement clusters stations where the "
            "geometry changes — domes, fillets, slot ends — instead of spreading them evenly.",
            4, 14)
        bubble.set_pointer("left", 60)
        bubble.show()
        _grab(bubble, outdir, "bubble_right.png", screenshots)

        bubble.set_step("Step 9 — Run", "Click Run.\n\nThis rebuilds the solid and writes a real "
                        "STEP file to the Output STEP path.", 8, 14,
                        waiting="waiting — click Run to continue")
        _grab(bubble, outdir, "bubble_waiting.png", screenshots)

        bubble.set_step("Step 6 — Check the settings", "Before running, the settings should "
                        "read: axis z, 40 sections, 0.5 mm.", 5, 14)
        bubble.show_correction(["Sections: 40 → set to 80", "Adaptive: off → set to on",
                                "Chord tol: 0.5 → set to 5"])
        _grab(bubble, outdir, "bubble_correction.png", screenshots)
        bubble.close()

        # ---- the real M2 walk ------------------------------------------
        demo = DEMOS["M2"]
        window.start_demo("M2")
        loop = QEventLoop()
        window.input_loaded.connect(loop.quit)
        loop.exec()
        app.processEvents()
        controller = window._tour
        if controller is None:
            raise RuntimeError("start_demo did not produce a tour controller")

        args = demo.args
        window.axis_combo.setCurrentText(args["axis"])
        window.units_combo.setCurrentText(args["units"])
        window.sections_spin.setValue(args["sections"])
        window.chord_tol_auto.setChecked(False)
        window.chord_tol_spin.setValue(args["chord_tol"])
        window.adaptive_check.setChecked(args["adaptive"])
        window.output_path_edit.setText(os.path.join(outdir, "M2_rebuilt.step"))

        # Walk forward until the tour is waiting on Analyze. `_advance` is the controller's own
        # next-button path, so the review step's `expect` really is evaluated here.
        for _ in range(len(demo.steps)):
            if demo.steps[controller.index].advance != "next":
                break
            controller._advance()
            app.processEvents()
        if controller.bubble is not None and controller.bubble.in_correction:
            raise RuntimeError(
                f"the M2 script's expect check rejected its own settings: "
                f"{controller.check_expectations(demo.steps[controller.index])}")
        if demo.steps[controller.index].advance != "analyze_done":
            raise RuntimeError(
                f"expected to stop on the Analyze step, stopped on "
                f"{demo.steps[controller.index].title!r}")

        truth_stl = window.input_path_edit.text().strip()
        analyze_worker = AnalyzeWorker(truth_stl, args["axis"], args["units"])
        result = _run_worker_sync(analyze_worker, run_in_thread(analyze_worker),
                                  ["finished", "failed"])
        if "failed" in result:
            raise RuntimeError(f"analyze failed: {result['failed']}")
        window._on_analyzed(*result["finished"])
        app.processEvents()

        while demo.steps[controller.index].advance == "next":
            controller._advance()
            app.processEvents()

        opts = RebuildOptions(
            input_stl=truth_stl, output=window.output_path_edit.text(), axis=args["axis"],
            units=args["units"], sections=args["sections"], chord_tol=args["chord_tol"],
            adaptive=args["adaptive"],
            report=os.path.join(outdir, "M2_rebuilt.report.json"),
            stl=os.path.join(outdir, "M2_rebuilt.preview.stl"),
        )
        rebuild_worker = RebuildWorker(opts)
        result = _run_worker_sync(rebuild_worker, run_in_thread(rebuild_worker),
                                  ["finished", "failed"])
        if "failed" in result:
            raise RuntimeError(f"rebuild failed: {result['failed']}")
        window._on_rebuilt(*result["finished"])
        app.processEvents()

        # `_on_rebuilt` emits rebuild_finished, so the tour is now on the read-the-verification
        # step -- the one worth a picture, since it is where the bubble and the Output page have
        # to be legible together.
        step = demo.steps[controller.index]
        if step.on_enter != "select_outline_output":
            raise RuntimeError(f"expected the verification step after the rebuild, got "
                               f"{step.title!r}")
        _grab(window, outdir, "m2_demo_mid.png", screenshots)
        _grab(controller.bubble, outdir, "m2_bubble_mid.png", screenshots)
        controller.cancel()
        app.processEvents()

        for path in screenshots:
            size = os.path.getsize(path)
            floor = MIN_SCREENSHOT_BYTES if path.endswith("m2_demo_mid.png") else MIN_WIDGET_BYTES
            if size < floor:
                raise RuntimeError(f"screenshot {path} too small ({size} bytes)")

        with open(json_path, "w") as f:
            json.dump({"ok": True, "screenshots": screenshots,
                       "steps": len(demo.steps)}, f, indent=2)
        return 0
    except Exception as exc:
        tb = traceback.format_exc()
        with open(json_path, "w") as f:
            json.dump({"ok": False, "screenshots": screenshots,
                       "error": f"{type(exc).__name__}: {exc}", "traceback": tb}, f, indent=2)
        print(f"tour smoke failed: {exc}", file=sys.stderr)
        print(tb, file=sys.stderr)
        return 1
