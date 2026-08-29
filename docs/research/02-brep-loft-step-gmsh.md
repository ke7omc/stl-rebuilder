# Research: build123d / OCP / CadQuery lofting, booleans, validity, STEP export, gmsh meshability check

_Verified research dump, 2026-08-28. Treat as reference, not gospel: re-verify any API against the installed package version before relying on it._

FINDINGS — Python BRep/CAD layer for STL→loft→STEP pipeline (verified against live PyPI/conda/source, 2026-08-28)

## 1. Installability TODAY

**cadquery-ocp (the OCP/OCCT binding both CadQuery and build123d use)** — PyPI `cadquery-ocp` 7.9.3.1.1 (released 2026-05-28), wraps **OCCT 7.9.3**. Requires-Python `>=3.10,<3.15`. Wheels for **cp310–cp314** on: `macosx_11_0_arm64`, `macosx_11_0_x86_64`, `win_amd64`, `manylinux_2_31_x86_64/aarch64`. So **Python 3.14 IS supported by the kernel binding** (cp314 wheels exist), and pip-install on Apple Silicon works out of the box now (historically conda-only; issue gumyr/build123d#646 is obsolete). Variant `cadquery-ocp-novtk` 7.9.3.1.1 (no VTK dep, same wheel matrix) — this is what build123d pulls.

**build123d** — PyPI 0.11.1 (2026-07-02). Requires-Python `>=3.10,<3.15` → **3.14 OK**. Deps: `cadquery-ocp-novtk >=7.9,<8.0`, numpy>=2, scipy, scikit-learn, ezdxf, ocpsvg, ocp_gordon, lib3mf, etc. `pip install build123d` works on macOS arm64 + Windows.

**CadQuery** — PyPI `cadquery` 2.8.0 (2026-06-21). Requires-Python `>=3.11` (no upper pin, but cadquery-ocp caps at <3.15). Deps: `cadquery-ocp <8.0,>=7.9.3.1`, **numba, casadi, nlopt, multimethod, runtype, trame stack**. Numba 0.63.0 (2025-12-08) added Python 3.14 support, so 3.14 is plausible, but casadi/nlopt cp314 wheel availability is unverified — **3.12/3.13 is the safe floor for CadQuery; 3.14 is safe for build123d/raw OCP**. Host machine's Homebrew Python 3.14.5 fits cadquery-ocp's `<3.15` — but recommend a dedicated venv/conda env on 3.13 to keep all three options open.

**pythonocc-core (raw bindings, tpaviot)** — NOT on PyPI as wheels; conda-forge only: `conda install -c conda-forge pythonocc-core=7.9.3` (last updated 2026-02-21), platforms osx-arm64/osx-64/win-64/linux-64/linux-aarch64, pinned exactly to conda-forge `occt 7.9.3`. Feedstock has no python skip → built for conda-forge's current python matrix (3.10–3.13; 3.14 as migrated). Note pythonocc-core API is a *different* binding than OCP (SWIG vs pybind11; e.g. static methods are `Interface_Static_SetCVal(...)` vs OCP's `Interface_Static.SetCVal_s(...)`), so code is not portable between them. **Recommendation: use build123d and drop to its OCP layer (`from OCP.* import *`) for anything low-level — one env, pip-only, no conda needed.**

## 2. Lofting closed planar wires → SOLID

**Underlying OCCT (identical via OCP or pythonocc):**
```cpp
BRepOffsetAPI_ThruSections(isSolid=False, ruled=False, pres3d=1.0e-06)
```
- `isSolid=True` → caps first/last sections with planar faces → SOLID. `ruled=True` → straight (linear) surfaces between consecutive sections; `False` → one smoothed B-spline surface through all sections.
- Methods: `AddWire(TopoDS_Wire)`, `AddVertex(TopoDS_Vertex)` (only first/last position), `CheckCompatibility(check=True)`, `SetSmoothing(bool)`, `SetParType(Approx_ParametrizationType)` (`Approx_ChordLength`/`Approx_Centripetal`/`Approx_IsoParametric`), `SetContinuity(GeomAbs_Shape)`, `SetCriteriumWeight(W1,W2,W3)`, `SetMaxDegree(int)`, `Build()`, `Shape()`, `FirstShape()`/`LastShape()` (bottom/top caps), `GeneratedFace(edge)`.
- **Twist/seam problem:** with `CheckCompatibility(True)` (default) OCCT runs `BRepFill_CompatibleWires` which recomputes wire origins/orientations and equalizes edge counts to avoid twist (picks correspondence minimizing twist, re-selects if twist >45°) — **this heuristic often fails on many-section lofts and star profiles** (documented OCCT forum complaints; FreeCAD hit it too — "Lofting edges get swapped over", FreeCAD#5650). Robust recipe: make every section wire yourself with (a) SAME number of edges, (b) same orientation (all CCW about +Z motor axis), (c) seam/start vertex at the same angular ray (e.g. θ=0), then call `CheckCompatibility(False)` so OCCT uses your correspondence verbatim. For single-edge periodic-B-spline wires, the seam vertex = curve origin → build each section's point list starting at θ=0; alternatively relocate a periodic curve's seam with `Geom_BSplineCurve::SetOrigin(index)`. For exact circles, construct each `Geom_Circle` with the same `gp_Ax2` X-direction so parameter-0 points the same way.
- Ruled loft between adjacent-station pairs is far more robust than one smoothed loft through 100 stations; for a smoothed loft use `SetParType(Approx_ChordLength)` and modest `SetMaxDegree` (default 8). `SetSmoothing(True)` switches to variational approximation using `SetCriteriumWeight`.

**build123d** (`operations_part.py`):
```python
loft(sections: Face|Sketch|Iterable[Vertex|Face|Sketch]|None = None,
     ruled: bool = False, clean: bool = True, mode: Mode = Mode.ADD) -> Part
```
Returns a solid Part. **Faces with holes ARE supported**: it lofts outer wires via `Solid.make_loft` (→ `BRepOffsetAPI_ThruSections(filled=True, ruled)`, i.e. isSolid=True), lofts each hole's wires separately, then **boolean-cuts** the hole lofts from the outer loft. Inner wires are matched across sections by minimal total distance between wire centers (order-independent heuristic; all sections must have equal inner-wire counts; can mismatch if holes cross/cluster). Has a recovery path: if the result `is_valid` fails, it rebuilds `Solid(Shell(faces + section faces))`. **Caveat: build123d's `_make_loft` never calls `CheckCompatibility(False)` or any smoothing controls** — OCCT's twist heuristic is always on; for full control drop to OCP directly. `Solid.make_loft(objs: Iterable[Vertex|Wire], ruled=False)` is exposed for wire-level lofting.

**CadQuery** — two APIs:
- `Workplane.loft(ruled=False, combine=False, clean=True)`; `Solid.makeLoft(listOfWire: list[Wire], ruled=False)` → hardcodes `BRepOffsetAPI_ThruSections(True, ruled)`, wires only, no holes, no compat control.
- **Free-function API (`from cadquery.func import loft, cut, spline, circle, wire, face, ...`)** — the most complete wrapper found:
```python
loft(s: Sequence[Shape], cap: bool = False, ruled: bool = False,
     continuity: Literal["C1","C2","C3"] = "C2",
     parametrization: Literal["uniform","chordal","centripetal"] = "uniform",
     degree: int = 3, compat: bool = True, smoothing: bool = False,
     weights: tuple[float,float,float] = (1,1,1), ...) -> Shape
```
  `compat=False` → `CheckCompatibility(False)`. Accepts Faces (uses `outerWire()` + `innerWires()`): lofts holes with **zip-order matching (positional, NOT geometric)** and assembles the solid by sewing side faces + subtracted caps (`solid(side, *sides, top, bot)`), not boolean cut. For wires-only input, `cap=True` gives a solid.

## 3. Wires from fitted curves

- **Periodic interpolating B-spline through points (exact fit):** OCC `GeomAPI_Interpolate(TColgp_HArray1OfPnt, PeriodicFlag: bool, Tolerance: float)` → `.Perform()` → `.Curve()` → `BRepBuilderAPI_MakeEdge(curve).Edge()` → `BRepBuilderAPI_MakeWire(edge).Wire()`. Do NOT repeat the first point when periodic=True.
- **Approximating B-spline (smoothing, fewer poles — better for noisy STL slice points):** `GeomAPI_PointsToBSpline(pnts, DegMin=3, DegMax=8, Continuity=GeomAbs_C2, Tol3D=1e-3)`; **no periodic flag** — for closed sections either interpolate a decimated point set periodically, or approximate with wrapped overlap and expect a seam-continuity kink (C0/C1 only at closure). build123d equivalents: `Edge.make_spline(points, tangents=None, periodic=False, parameters=None, scale=True, tol=1e-06)` (uses GeomAPI_Interpolate, **has `periodic=`**) and `Edge.make_spline_approx(points, tol=1e-3, smoothing=None, min_deg=1, max_deg=6)` (GeomAPI_PointsToBSpline, **no periodic**). CadQuery: `cadquery.func.spline(*pts, tol=1e-6, periodic=False)` / `spline(pts, tgts=None, params=None, tol=1e-6, periodic=False, scale=True)` (GeomAPI_Interpolate).
- **Exact circle:** build123d `Edge.make_circle(radius, plane=Plane.XY, start_angle=360.0, end_angle=360, angular_direction=CCW)`, `Wire.make_circle(radius, plane=Plane.XY)`; cq `func.circle(r)`; raw: `BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(center, normal, xdir), r))` — set `xdir` consistently per §2 seam control.
- **Planar face with holes:** build123d `Face.make_from_wires(outer: Wire, inner: Iterable[Wire]|None) -> Face` (internally: verifies closure + coplanarity via `BRepLib_FindSurface(..., OnlyPlane=True)`, runs `ShapeFix_Shape` on each wire, `BRepBuilderAPI_MakeFace(outer_wire, True)` + `.Add(inner_wire)`, then `ShapeFix_Face.FixOrientation()` — inner wires must be opposite orientation to outer; the ShapeFix pass handles it). Raw OCC: `BRepBuilderAPI_MakeFace(wire, OnlyPlane=True)` then `.Add(hole_wire.Reversed())`. CadQuery: `func.face(outer, *inner)`.

## 4. Booleans, validity, healing

- **Cut:** raw `BRepAlgoAPI_Cut(shape, tool)` → `.IsDone()`, `.HasErrors()`, `.Shape()`; for tolerance-tolerant booleans use `.SetFuzzyValue(tol)` + `.SetRunParallel(True)` (via SetArguments/SetTools lists). build123d: `outer_solid - bore_solid` (algebra) or `shape.cut(*tools)`. CadQuery: `Workplane.cut()`, or `func.cut(s1, s2, tol=0.0, glue=None)` — `tol` is the fuzzy value (uses `BOPAlgo_BOP`).
- **Validity:** raw `BRepCheck_Analyzer(shape)` → `.IsValid()`. build123d: property `Shape.is_valid` (BRepCheck_Analyzer with `SetParallel(True)`). CadQuery: `Shape.isValid() -> bool`. NOTE: BRepCheck_Analyzer validity ≠ meshability/importability — also run the gmsh check (§6) and `BOPAlgo_ArgumentAnalyzer` for boolean-readiness if debugging.
- **Healing:** raw `ShapeFix_Shape(shape); sf.Perform(); sf.Shape()` (build123d `Shape.fix()` does exactly this; cadquery `Shape.fix()` likewise). Per-face `ShapeFix_Face.FixOrientation()`; wire order/gaps `ShapeFix_Wire` (`FixReorder/FixConnected/FixClosed`). After lofting per-segment ruled pairs, merge coplanar/co-surface faces with `ShapeUpgrade_UnifySameDomain(shape, UnifyEdges=True, UnifyFaces=True, ConcatBSplines=True)` — important to kill the "hundreds of tiny faces" problem if you loft station-pairs. **Sewing:** `BRepBuilderAPI_Sewing(tolerance)` `.Add(face)...Perform(); .SewedShape()` — sewing tolerance must exceed the max gap between section-fit curves at shared stations; then `BRepBuilderAPI_MakeSolid(shell)` + check orientation with `BRepClass3d_SolidClassifier` (or `ShapeFix_Solid` which also fixes shell orientation). Symptom of too-small sewing tol: sewed shape stays a Shell/Compound, not a closed shell.

## 5. STEP export

- OCCT default schema is **AP214IS** (`write.step.schema` default since OCCT 6.9; options `"AP203"`, `"AP214CD"`, `"AP214DIS"`, `"AP214IS"`, `"AP242DIS"`). SpaceClaim reads AP203/AP214/AP242 — default AP214 is fine.
- **build123d:** `from build123d.exporters3d import export_step`; `export_step(to_export: Shape, file_path, unit=Unit.MM, write_pcurves=True, precision_mode=PrecisionMode.AVERAGE, *, timestamp=None) -> bool`. Schema NOT exposed (source sets only `write.surfacecurve.mode` and `write.precision.mode`) → AP214IS.
- **CadQuery:** `Shape.exportStep(fileName, unit="MM", outputUnit=None, **kwargs)` (kwargs: `write_pcurves`, `precision_mode`) or `cq.exporters.export(shape, "out.step", opt={...})`. Schema not exposed → AP214IS.
- **Force AP203 (raw OCP/pythonocc):**
```python
from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
from OCP.Interface import Interface_Static
w = STEPControl_Writer()
Interface_Static.SetCVal_s("write.step.schema", "AP203")
w.Model(True)   # REQUIRED after changing schema for it to take effect
w.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
w.Write("out.step")   # returns IFSelect_RetDone on success
```
(pythonocc spelling: `Interface_Static_SetCVal("write.step.schema","AP203")`.)

## 6. gmsh headless meshability check

PyPI `gmsh` 4.15.2 (2026-03-24), wheels: `macosx_12_0_arm64`, `macosx_10_15_x86_64`, `manylinux_2_24_x86_64`, `win_amd64`; no requires-python pin (ctypes binding — version-agnostic, 3.14 fine). Wheels are the full gmsh SDK with **OpenCASCADE compiled in** (7.8.x-era; exact minor unverified) — STEP import works with zero extra installs, independent of the cadquery-ocp OCCT.

```python
import gmsh
def mesh_check(step_path, hmax):
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.logger.start()
    try:
        gmsh.model.add("chk")
        dimtags = gmsh.model.occ.importShapes(step_path, highestDimOnly=True)
        gmsh.model.occ.synchronize()
        n_solids = sum(1 for d, _ in dimtags if d == 3)
        if n_solids == 0:
            return {"ok": False, "reason": "STEP contains no solid (surfaces only)"}
        gmsh.option.setNumber("Mesh.MeshSizeMax", hmax)
        gmsh.option.setNumber("Mesh.MeshSizeMin", hmax / 10)
        try:
            gmsh.model.mesh.generate(3)        # raises on hard failure
        except Exception as e:
            return {"ok": False, "reason": f"mesh generate raised: {e}"}
        types, tags, _ = gmsh.model.mesh.getElements(3)
        all_tags = [t for grp in tags for t in grp]
        if not all_tags:
            return {"ok": False, "reason": "0 tets (silent 3D failure)"}
        q = gmsh.model.mesh.getElementQualities(all_tags, "minSICN")
        errs = [l for l in gmsh.logger.get() if l.startswith("Error")]
        return {"ok": min(q) > 0 and not errs, "n_tet": len(all_tags),
                "min_quality": min(q), "errors": errs}
    finally:
        gmsh.finalize()
```
Key API facts: `gmsh.model.occ.importShapes(fileName, highestDimOnly=True, format="") -> dimtags`; `gmsh.model.mesh.getElementQualities(elementTags, qualityName="minSICN")` (other names: `"gamma"`, `"minSIGE"`, `"minIsotropy"`; minSICN<0 ⇒ inverted element). Failure detection needs BOTH the exception path AND the 0-tet/logger check — gmsh sometimes completes `generate(3)` after only surface meshing when the volume mesher fails, logging Error lines without raising. Optional healing before synchronize: `gmsh.option.setNumber("Geometry.OCCFixDegenerated"/"Geometry.OCCFixSmallEdges"/"Geometry.OCCFixSmallFaces"/"Geometry.OCCSewFaces", 1)` (must be set before importShapes), or `gmsh.model.occ.healShapes()`. For a 10 m motor exported in mm, set `Mesh.MeshSizeMax` explicitly or meshing will be huge/slow.

**Bottom line for the planner:** build123d 0.11.1 + cadquery-ocp-novtk 7.9.3.1.1 on Python 3.13 (or 3.14) via plain pip covers everything: high-level `loft()` with hole-face support + `is_valid`/`fix()` + `export_step`, with raw `OCP.BRepOffsetAPI_ThruSections` (CheckCompatibility(False), seam-aligned periodic splines from `GeomAPI_Interpolate`) available in the same process for the twist-critical star/finocyl lofts. gmsh 4.15.2 via pip as the independent meshability oracle. pythonocc-core only needed if avoiding pip entirely (conda-forge 7.9.3); CadQuery adds `cadquery.func.loft(compat=, smoothing=, parametrization=)` — the only high-level loft exposing ThruSections tuning — at the cost of a heavier dep stack (numba/casadi/nlopt) and shakier 3.14 support.

Sources: [cadquery-ocp PyPI](https://pypi.org/project/cadquery-ocp/), [build123d PyPI](https://pypi.org/project/build123d/), [cadquery PyPI JSON](https://pypi.org/pypi/cadquery/json), [pythonocc-core conda-forge](https://anaconda.org/conda-forge/pythonocc-core), [pythonocc-core feedstock](https://github.com/conda-forge/pythonocc-core-feedstock), [build123d loft docs](https://build123d.readthedocs.io/en/latest/operations.html), [build123d import/export docs](https://build123d.readthedocs.io/en/latest/import_export.html), [build123d source (topology/utils.py, operations_part.py, shape_core.py, one_d.py, exporters3d.py, pyproject.toml)](https://github.com/gumyr/build123d), [CadQuery source shapes.py/func.py](https://github.com/CadQuery/cadquery), [OCCT BRepOffsetAPI_ThruSections refman](https://occt3d.com/dev/doc/refman/html/class_b_rep_offset_a_p_i___thru_sections.html), [OCCT ThruSections wire-reversal forum thread](https://dev.opencascade.org/content/brepoffsetapithrusections-reverses-direction-half-wires), [FreeCAD loft edge-swap issue](https://github.com/FreeCAD/FreeCAD/issues/5650), [OCCT STEP user guide](https://github.com/Open-Cascade-SAS/OCCT/wiki/step), [pythonocc AP203 export demo](https://github.com/tpaviot/pythonocc-demos/blob/master/examples/core_export_step_ap203.py), [gmsh PyPI JSON](https://pypi.org/pypi/gmsh/json), [gmsh docs](https://gmsh.info/doc/texinfo/gmsh.html), [numba 0.63.0 notes (py3.14)](https://numba.readthedocs.io/en/stable/release/0.63.0-notes.html), [Create Python 3.14 wheel issue, ocp-build-system#56](https://github.com/CadQuery/ocp-build-system/issues/56), [build123d Apple Silicon pip issue #646](https://github.com/gumyr/build123d/issues/646)