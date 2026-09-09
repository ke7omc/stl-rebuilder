"""CLI entry point. Contract in MISSION.md §5.3.

Thin argparse wrapper around `pipeline.engine._rebuild_with_refinement` (the pipeline itself
lives in `pipeline/engine.py` per MISSION §12/G1 — `engine.analyze()`/`engine.rebuild()` is the
API the GUI (`app/`) consumes; this module and `engine._rebuild_argparse` must stay
byte-identical in behaviour to before the G1 extraction). `_rebuild_with_refinement` is a
complete no-op wrapper around `_rebuild_argparse` whenever the pass-1 build's own verification
already passes (true for every M1-13 milestone except M6 as of 2026-09-05's Gate 0 measurement,
see PROGRESS.md) or `--refine-passes 0`, so byte-identical CLI behaviour holds in every case
that matters for the frozen scorer.
"""
import argparse
import sys
import traceback

from pipeline import engine


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="rebuild.py")
    p.add_argument("input_stl")
    p.add_argument("--axis", default="z")
    p.add_argument("--units", default="mm", choices=("mm", "in", "m"))
    p.add_argument("--sections", type=int, default=40)
    p.add_argument("--refine-bands", default=None)
    p.add_argument("--adaptive", action="store_true")
    p.add_argument("--chord-tol", type=float, default=0.5)
    p.add_argument("--roundness-tol", type=float, default=None,
                   help="out-of-roundness gate floor in mm (default: measured from the mesh; "
                        "0 disables -- classification gates then derive purely from "
                        "--chord-tol, as before the decoupled-roundness fix)")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--stl", default=None)
    p.add_argument("--refine-passes", type=int, default=1,
                   help="extra verify-and-refine retries beyond the first build, when the "
                        "input mesh vs. output solid deviation check fails (default 1; 0 "
                        "disables; capped internally at 1 regardless of the value given)")
    return p.parse_args(argv)


def _run(args) -> int:
    return engine._rebuild_with_refinement(args)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return _run(args)
    except Exception as exc:  # never let the harness see a raw crash (MISSION.md §5.2 step 8)
        tb = traceback.format_exc().splitlines()[-6:]
        print(f"rebuild.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("\n".join(tb), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
