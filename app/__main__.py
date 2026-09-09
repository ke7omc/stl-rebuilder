"""`python -m app` launches the window; `python -m app --smoke <outdir>` runs the headless
self-check (MISSION §12/G2)."""
# Import order workaround, found on a Windows/Python 3.12 machine 2026-09-09: PySide6's Shiboken
# support module installs an import hook when it loads. If `six` (a transitive dependency, pulled
# in by pyvista) then gets imported for the FIRST time afterward, Shiboken's hook inspects it and
# crashes with `AttributeError: '_SixMetaPathImporter' object has no attribute '_path'` -- six's
# own meta-path finder doesn't have the attribute a normal module does. Under the desktop
# shortcut (`pythonw.exe`, no console) this killed the process silently with zero on-screen
# feedback. Below, line 25 imports PySide6 before `app.main_window` (line 26) pulls in
# `app.viewport`'s matplotlib import, which is exactly the bad order -- importing `six` (and,
# since pyvista pulls both together, `matplotlib`) here, before anything else, means they're
# already cached in sys.modules by the time PySide6's hook is registered, so it never inspects
# them fresh. Bare `import matplotlib`, not `matplotlib.pyplot` -- pyplot would force an early
# GUI-backend choice, a real side effect this workaround has no business causing. `six` isn't a
# direct dependency of this project (nothing in WORK_SETUP.md installs it), so its absence must
# stay harmless rather than becoming a new crash this workaround introduces.
try:
    import six  # noqa: F401
except ImportError:
    pass
import matplotlib  # noqa: F401

import argparse
import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("--smoke", metavar="OUTDIR", default=None,
                         help="run headless self-check, writing screenshots + smoke.json here")
    parser.add_argument("--smoke-tour", metavar="OUTDIR", default=None,
                         help="render the help dialog, the demo picker and a scripted walk of "
                              "the M2 guided demo, writing PNGs + tour_smoke.json here")
    args = parser.parse_args(argv)

    if args.smoke is not None:
        from app.smoke import run_smoke
        return run_smoke(args.smoke)

    if args.smoke_tour is not None:
        from app.smoke import run_tour_smoke
        return run_tour_smoke(args.smoke_tour)

    from PySide6.QtWidgets import QApplication
    from app.main_window import MainWindow
    from app.theme import apply_theme
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow(offscreen=False)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
