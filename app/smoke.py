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
        analysis = result["finished"][0]
        window._on_analyzed(analysis)
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
        rebuild_result = result["finished"][0]
        window._on_rebuilt(rebuild_result)
        app.processEvents()

        shot3 = os.path.join(outdir, "03_rebuilt.png")
        window.grab().save(shot3)
        screenshots.append(shot3)

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
