"""`python -m app` launches the window; `python -m app --smoke <outdir>` runs the headless
self-check (MISSION §12/G2)."""
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
