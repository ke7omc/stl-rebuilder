# WORK_SETUP — running stl-rebuilder on a work computer (Windows, no admin rights)

**Audience: an AI assistant** (or a human) setting this tool up on a locked-down Windows
machine. Everything here runs as a normal user — no installers, no .exe, no admin. The same
steps work on macOS/Linux with the obvious path changes.

## What this repository is

`rebuild.py` converts a solid-rocket-motor **burnback surface mesh (STL)** into a true
**BRep solid (STEP)** suitable for SpaceClaim/Fluent. It was built and validated by an
autonomous loop against 13 synthetic ground-truth motors (see `HANDOFF.md` for the results
table, gates, and known limitations). The desktop GUI lives in `app/` (see §6).

Everything needed to *use and verify* the tool is pure Python with pip wheels:
- **Product**: `rebuild.py` + `pipeline/` (+ `app/` for the GUI).
- **Test-geometry generators**: `harness/generators.py` — regenerates every synthetic motor
  (mesh + analytic truth STEP) on demand, so no large files need to travel.
- The loop machinery (`loop.py`, `loop.sh`, `driver/`) is macOS-only and **not needed at work —
  ignore it.**

## 1. Install Python (no admin)

Install **Python 3.12 or 3.13** (3.14 also works as of Sep 2026) from https://python.org —
in the installer choose **"Install for me only"** (per-user, no admin) and check "Add to PATH".
If python.org is blocked, use the company software center's Python.

## 2. Get the code

Log into GitHub in a browser → `https://github.com/ke7omc/stl-rebuilder` (private — the owner
must be signed in) → green **Code** button → **Download ZIP** → extract to e.g.
`C:\Users\<you>\stl-rebuilder`. No git required.

## 3. Create the environment

From a terminal (cmd or PowerShell) in the repo folder:

```bat
py -3.12 -m venv .venv
.venv\Scripts\pip install numpy scipy shapely networkx rtree trimesh build123d
```

(~600 MB, one time; `build123d` pulls the OCCT geometry kernel. On mac/Linux the second line is
`.venv/bin/pip install ...`.) If a corporate proxy blocks pip, append
`--proxy http://<proxy>:<port>` or set `HTTPS_PROXY` first.

For the GUI, additionally:

```bat
.venv\Scripts\pip install PySide6 pyvista pyvistaqt pyqtgraph qtawesome
```

## 4. Generate test geometry and verify the install

The repo ships no meshes; it ships the **generators**. To create any milestone's input mesh and
its analytic truth (both land in `harness\truth\`):

```bat
.venv\Scripts\python -c "from harness import generators; generators.make('M2')"
```

Valid names: `M1`…`M13` (see `HANDOFF.md` §1 for what each geometry is). Notes:
- `M2` (domed cylinder) is the recommended quick check (~seconds).
- `M9` and `M13` are marching-cubes "dirty scan" inputs and additionally need
  `pip install scikit-image`; `M13` is 5.3M triangles and takes minutes — skip unless needed.
- Files regenerate deterministically when missing; delete `harness\truth\` any time.

Then rebuild the mesh and compare against the truth:

```bat
.venv\Scripts\python rebuild.py harness\truth\M2.stl --out M2_rebuilt.step
```

(Run `rebuild.py --help` for all flags; the exact per-milestone commands used during
validation are listed in `HANDOFF.md` §3.) Open `M2_rebuilt.step` and `harness\truth\M2.step`
in SpaceClaim/FreeCAD: each should import as **one solid** with matching volume. If so, the
install is good.

## 5. Run it on a real burnback STL

Follow `HANDOFF.md` §5 (the runbook). The short version for a typical Fluent-style motor
(x-axis, inches):

```bat
.venv\Scripts\python rebuild.py motor.stl --axis x --units in --out motor_solid.step
```

- `--axis auto` detects the axis if unsure; `--units` must be stated explicitly (STL files
  carry no units; the STEP output is always written in mm).
- Every run writes a report JSON next to the output — check `n_stations`, per-station
  diagnostics, `warnings`, and `topology_events_z_mm` before trusting the solid.
- On failure the report is still written, with an `error` field and partial stations —
  read it (and HANDOFF §5's failure-mode table) before retrying with different flags.

## 6. The GUI (run from source — no .exe)

```bat
.venv\Scripts\python -m app
```

Requires the GUI packages from §3. Features: input STL picker, output path/name, motor axis
(auto-detected, x/y/z/custom override), explicit units, detected axial extent, slice-fidelity
controls, adaptive refinement toggle, built-in 3D viewport (input mesh over the rebuilt
solid), progress + cancel, and a result manifest. A desktop shortcut can point at
`.venv\Scripts\pythonw.exe -m app` with "Start in" = the repo folder (pythonw = no console
window). Headless self-check: `python -m app --smoke out\gui` renders screenshots and exits 0.

## 7. Optional extras (only for re-running the scoring harness)

```bat
.venv\Scripts\pip install gmsh scikit-image pytest
```

Then:
- `python harness\selftest.py` — the harness's own test suite (~10 min warm).
- `python harness\score.py --milestone M2 --out score.json` — score the current pipeline
  against a milestone exactly as the build loop did.
- `python -m pytest tests\ -q` — unit tests.
None of this is needed to *use* the converter.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| pip times out / SSL errors | corporate proxy: `--proxy http://<proxy>:<port>` or `HTTPS_PROXY` env var; if a cert bundle is mandated, `pip config set global.cert <path>` |
| `import OCP` very slow first time | normal (large binary); subsequent imports are fast |
| `py` not found | use `python` instead, or reinstall with "Add to PATH" checked |
| GUI opens with a blank 3D view | update graphics driver if possible; otherwise set env `QT_OPENGL=software` before launching |
| Output STEP won't import in SpaceClaim | import with "facewise connections" enabled; then see HANDOFF §4 checklist and §6 limitations |
