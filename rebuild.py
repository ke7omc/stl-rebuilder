#!/usr/bin/env python3
"""CLI entry point: STL burnback → BRep solid (STEP). Contract in MISSION.md §5.3."""
import sys

from pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
