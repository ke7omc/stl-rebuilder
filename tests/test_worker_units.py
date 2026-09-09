"""`app/worker.py::_read_and_scale_mesh` -- the input-mesh preview must be scaled to mm the same
way `pipeline.io.parse_units`/`RebuildOptions.units` already scale everything else, or an inches
(or metre) input renders at 1:1 next to the always-mm rebuilt solid: a real bug found on a real
motor STL (Brady, work-machine testing 2026-09-09) where the input mesh appeared as a giant
sphere dwarfing a tiny rebuilt solid -- a pure display-scale mismatch, not a reconstruction error."""
import pytest

from app.worker import _read_and_scale_mesh

M1_STL = "harness/truth/M1.stl"


def test_mm_units_leave_the_mesh_unscaled():
    mesh_mm = _read_and_scale_mesh(M1_STL, "mm")
    import pyvista as pv
    mesh_raw = pv.read(M1_STL)
    assert mesh_mm.bounds == mesh_raw.bounds


def test_inches_units_scale_the_mesh_up_by_25_4x():
    mesh_mm = _read_and_scale_mesh(M1_STL, "mm")
    mesh_in = _read_and_scale_mesh(M1_STL, "in")
    # Same file read as if its coordinates were inches must come out 25.4x LARGER once
    # converted to mm -- exactly `pipeline.io.parse_units("in")`. Tolerance is relative, not
    # exact equality: the mesh's own vertex storage is float32, so scaling by a large factor
    # accumulates a little rounding noise well below anything geometrically meaningful.
    for lo_mm, hi_mm, lo_in, hi_in in zip(mesh_mm.bounds[0::2], mesh_mm.bounds[1::2],
                                          mesh_in.bounds[0::2], mesh_in.bounds[1::2]):
        assert (hi_in - lo_in) == pytest.approx((hi_mm - lo_mm) * 25.4, rel=1e-5)


def test_metre_units_scale_the_mesh_up_by_1000x():
    mesh_mm = _read_and_scale_mesh(M1_STL, "mm")
    mesh_m = _read_and_scale_mesh(M1_STL, "m")
    for lo_mm, hi_mm, lo_m, hi_m in zip(mesh_mm.bounds[0::2], mesh_mm.bounds[1::2],
                                        mesh_m.bounds[0::2], mesh_m.bounds[1::2]):
        assert (hi_m - lo_m) == pytest.approx((hi_mm - lo_mm) * 1000.0, rel=1e-5)
