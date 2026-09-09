"""QThread worker: runs `pipeline.engine.analyze`/`rebuild` off the UI thread (MISSION §12, G2
-- "the UI never blocks; progress and cancel are real")."""
import os

import pyvista as pv
from PySide6.QtCore import QObject, QThread, Signal

from pipeline import engine
from pipeline.io import parse_units


def _read_and_scale_mesh(path: str, units: str) -> pv.DataSet:
    """Read a preview mesh and scale it to millimetres -- an STL carries no units of its own, and
    the rebuilt solid's own preview (`result.stl_path`/`AnalyzeWorker`'s analysis) is ALWAYS mm
    (`pipeline.engine` converts to mm internally before building anything). Every raw `pv.read`
    of the INPUT file in this module used to skip that conversion, so an inches input rendered at
    1:1 next to an mm-scale solid was 25.4x too small -- a real bug (Brady, work-machine testing
    2026-09-09: the input mesh appeared as a giant sphere dwarfing a tiny rebuilt solid, purely a
    display-scale mismatch, not a reconstruction error). `parse_units` is the same mm-per-unit
    table `--units`/`RebuildOptions` already use, so this can never disagree with what the engine
    itself assumes."""
    mesh = pv.read(path)
    scale = parse_units(units)
    if scale != 1.0:
        mesh.scale([scale, scale, scale], inplace=True)
    return mesh


class PreviewWorker(QObject):
    """Loads a mesh file off the UI thread for immediate display -- used to show the input mesh
    the instant a file is chosen, before Analyze/Run ever runs (Brady, 2026-09-06: "no geometry
    is displayed but the input mesh should definitely be displayed so the user knows something
    was loaded in"). Deliberately separate from `AnalyzeWorker` (which ALSO preloads a preview,
    but only once Analyze actually completes) since this needs to fire immediately on file
    selection, independent of whether Analyze ever runs at all."""
    finished = Signal(object)   # pv.DataSet
    failed = Signal(str)        # message

    def __init__(self, path: str, units: str = "mm"):
        super().__init__()
        self.path = path
        self.units = units

    def run(self):
        try:
            mesh = _read_and_scale_mesh(self.path, self.units)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(mesh)


class AnalyzeWorker(QObject):
    progress = Signal(str, float, str)  # stage ("analyze"), frac, message
    finished = Signal(object, object)   # engine.Analysis, pv.DataSet | None (input mesh preview)
    failed = Signal(str, str)           # kind, message

    def __init__(self, input_path: str, axis: str, units: str):
        super().__init__()
        self.input_path = input_path
        self.axis = axis
        self.units = units

    def run(self):
        try:
            analysis = engine.analyze(
                self.input_path, self.axis, self.units,
                on_progress=lambda stage, frac, msg: self.progress.emit(stage, frac, msg))
        except Exception as exc:
            self.failed.emit(type(exc).__name__, str(exc))
            return
        # Preload the preview mesh here, off the UI thread -- doing it on the main thread right
        # after the caller stops the busy spinner meant the blocking read ran before Qt got a
        # chance to actually repaint the hide, so the spinner visibly looked frozen (Brady,
        # 2026-09-05 live testing). A failure here is preview-only, not an Analyze failure.
        try:
            mesh = _read_and_scale_mesh(self.input_path, self.units)
        except Exception:
            mesh = None
        self.finished.emit(analysis, mesh)


class RebuildWorker(QObject):
    progress = Signal(str, float, str)  # stage, frac, message
    # engine.Result, pv.DataSet | None (solid preview), pv.DataSet | None (input preview)
    finished = Signal(object, object, object)
    failed = Signal(str, str)           # kind, message

    def __init__(self, opts):
        super().__init__()
        self.opts = opts
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            result = engine.rebuild(
                self.opts,
                on_progress=lambda stage, frac, msg: self.progress.emit(stage, frac, msg),
                cancel=lambda: self._cancelled,
            )
        except engine.RebuildCancelled:
            self.failed.emit("Cancelled", "Rebuild cancelled by user")
            return
        except engine.RebuildError as exc:
            self.failed.emit(type(exc).__name__, str(exc))
            return
        except Exception as exc:
            self.failed.emit("Error", f"{type(exc).__name__}: {exc}")
            return
        # Same frozen-spinner fix as AnalyzeWorker: preload both previews here, off the UI
        # thread, instead of the main thread blocking on them right after the spinner is
        # stopped. The input preview is only needed by the caller for a Run-without-Analyze
        # (the input layer would otherwise never get loaded at all, main_window.py's
        # `_on_rebuilt`) -- reading it unconditionally here is cheap and lets that path share
        # the same off-thread fix instead of keeping its own main-thread `pv.read`.
        solid_mesh = None
        if result.stl_path and os.path.exists(result.stl_path):
            try:
                solid_mesh = pv.read(result.stl_path)
            except Exception:
                solid_mesh = None
        input_mesh = None
        if self.opts.input_stl and os.path.exists(self.opts.input_stl):
            try:
                input_mesh = _read_and_scale_mesh(self.opts.input_stl, self.opts.units)
            except Exception:
                input_mesh = None
        self.finished.emit(result, solid_mesh, input_mesh)


def run_in_thread(worker: QObject) -> QThread:
    """Standard QThread wiring: worker.run() on the thread, thread cleans itself up when the
    worker is done (whichever signal fired). Caller connects finished/failed/progress before
    calling this, then keeps a reference to the returned thread until it's finished."""
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    done_signals = [s for s in ("finished", "failed") if hasattr(worker, s)]
    for name in done_signals:
        getattr(worker, name).connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    return thread
