"""QThread worker: runs `pipeline.engine.analyze`/`rebuild` off the UI thread (MISSION §12, G2
-- "the UI never blocks; progress and cancel are real")."""
from PySide6.QtCore import QObject, QThread, Signal

from pipeline import engine


class AnalyzeWorker(QObject):
    finished = Signal(object)   # engine.Analysis
    failed = Signal(str, str)   # kind, message

    def __init__(self, input_path: str, axis: str, units: str):
        super().__init__()
        self.input_path = input_path
        self.axis = axis
        self.units = units

    def run(self):
        try:
            analysis = engine.analyze(self.input_path, self.axis, self.units)
        except Exception as exc:
            self.failed.emit(type(exc).__name__, str(exc))
            return
        self.finished.emit(analysis)


class RebuildWorker(QObject):
    progress = Signal(str, float, str)  # stage, frac, message
    finished = Signal(object)           # engine.Result
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
        self.finished.emit(result)


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
