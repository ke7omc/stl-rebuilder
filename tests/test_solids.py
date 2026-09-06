"""Tests for pipeline/solids.py. `build_filleted_wedge_solid`'s degeneracy fix (2026-09-05): a
fillet radius clamped to exactly half the wedge's radial/axial span made two meridian wire corners
coincide, and the unguarded `BRepBuilderAPI_MakeEdge` on that zero-length edge crashed with
`Standard_Failure: BRep_API: command not done` -- this was the M13/no-adaptive/50-section crash
from Brady's live testing. See PROGRESS.md and the plan for the fallback-ladder half of the fix
(pipeline/engine.py's `_build_slot_wedges`)."""
from OCP.BRepCheck import BRepCheck_Analyzer

from pipeline import solids


def test_wedge_survives_fillet_at_exactly_half_radial_span():
    # r_out - r_in = 20, so f_lo/f_hi = 10 is exactly the old crash boundary.
    shape = solids.build_filleted_wedge_solid(
        z_lo=0.0, z_hi=100.0, r_in=90.0, r_out=110.0,
        f_lo=10.0, f_hi=10.0, theta_c=0.0, theta_half=0.3)
    assert shape is not None
    assert BRepCheck_Analyzer(shape).IsValid()


def test_wedge_survives_fillet_at_exactly_half_axial_span():
    # z_hi - z_lo = 20, so f_lo/f_hi = 10 is exactly the old crash boundary on the other axis.
    shape = solids.build_filleted_wedge_solid(
        z_lo=0.0, z_hi=20.0, r_in=50.0, r_out=200.0,
        f_lo=10.0, f_hi=10.0, theta_c=0.0, theta_half=0.3)
    assert shape is not None
    assert BRepCheck_Analyzer(shape).IsValid()


def test_wedge_survives_fillet_requested_past_half_span():
    # A caller-requested fillet larger than the clamp allows must still clamp cleanly, not crash.
    shape = solids.build_filleted_wedge_solid(
        z_lo=0.0, z_hi=100.0, r_in=90.0, r_out=110.0,
        f_lo=50.0, f_hi=50.0, theta_c=0.0, theta_half=0.3)
    assert shape is not None
    assert BRepCheck_Analyzer(shape).IsValid()
