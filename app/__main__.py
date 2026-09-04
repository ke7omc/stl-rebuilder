"""`python -m app` launches the window; `python -m app --smoke <outdir>` runs the headless
self-check (MISSION §12/G2)."""
import argparse
import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("--smoke", metavar="OUTDIR", default=None,
                         help="run headless self-check, writing screenshots + smoke.json here")
    args = parser.parse_args(argv)

    if args.smoke is not None:
        from app.smoke import run_smoke
        return run_smoke(args.smoke)

    from PySide6.QtWidgets import QApplication
    from app.main_window import MainWindow
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(offscreen=False)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
