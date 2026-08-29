"""Milestone specs: geometry params, region labels, gate thresholds, rebuild.py args, runtime caps.

One MilestoneSpec per milestone (M1–M5). All geometry in mm.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CHORD_TOL = 0.5  # mm — default scoring/generation tolerance (passed to rebuild.py)


@dataclass(frozen=True)
class RegionBand:
    label: str
    z_frac_lo: float  # inclusive, fraction of total axial length L
    z_frac_hi: float  # inclusive


@dataclass(frozen=True)
class MilestoneSpec:
    name: str
    description: str
    params: Dict               # geometry parameters (all in mm)
    regions: List[RegionBand]  # z-fraction bands with region labels
    rebuild_args: List[str]    # appended to: rebuild.py <input.stl> -o <out.step>
    gates: Dict                # check_name -> threshold (numeric) or True (boolean)
    runtime_cap_s: float
    closed_form_volume: Optional[float] = None  # mm³; None when no exact formula
    # gmsh gets its own budget: meshing is the scorer's cost, not the pipeline's, and M5's
    # runtime_cap_s is a *product* requirement (< 120 s) that must not throttle the check.
    mesh_timeout_s: float = 300.0


def _m1() -> MilestoneSpec:
    L, R_o, R_i = 10_000.0, 1_000.0, 300.0
    return MilestoneSpec(
        name="M1",
        description="Annular cylinder: L=10000, R_o=1000, R_i=300 (mm)",
        params=dict(L=L, R_o=R_o, R_i=R_i),
        regions=[RegionBand("cylinder", 0.0, 1.0)],
        rebuild_args=["--axis", "z", "--sections", "40", "--chord-tol", str(CHORD_TOL)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.05,            # < 0.05 %
            surface_deviation_p99_mm=0.8 * CHORD_TOL,   # < 0.4 mm
            surface_deviation_max_mm=1.2 * CHORD_TOL,   # < 0.6 mm
            face_count_max=8,
            bbox_err_pct=0.1,               # universal gate, MISSION §6
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=120.0,
        closed_form_volume=math.pi * (R_o**2 - R_i**2) * L,
    )


def _m2() -> MilestoneSpec:
    L, R_o, R_i = 10_000.0, 1_000.0, 300.0
    # 2:1 ellipsoidal dome: radial semi-axis = R_o, axial semi-axis = R_o/2
    dome_h = R_o / 2.0   # = 500 mm axial height per dome
    return MilestoneSpec(
        name="M2",
        description="M1 + 2:1 ellipsoidal domes on outer surface both ends; straight bore through",
        params=dict(L=L, R_o=R_o, R_i=R_i, dome_semi_axial=dome_h),
        regions=[
            RegionBand("fore_dome",  0.0,              dome_h / L),
            RegionBand("cylinder",   dome_h / L,       1.0 - dome_h / L),
            RegionBand("aft_dome",   1.0 - dome_h / L, 1.0),
        ],
        rebuild_args=["--axis", "z", "--sections", "40", "--chord-tol", str(CHORD_TOL)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.05,
            surface_deviation_p99_mm=0.8 * CHORD_TOL,
            surface_deviation_max_mm=1.2 * CHORD_TOL,
            dome_stations_min=8,
            # Generous cap: an exact M2 needs ~6 faces. Its job is to reject a *tessellated*
            # shell masquerading as a BRep solid (thousands of planar faces), not to grade style.
            face_count_max=40,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=180.0,
        closed_form_volume=None,  # dome geometry makes closed form non-trivial
    )


def _m3() -> MilestoneSpec:
    L, R_o = 10_000.0, 1_000.0
    R_valley, R_tip = 250.0, 450.0
    fillet_tip, fillet_valley = 30.0, 40.0
    return MilestoneSpec(
        name="M3",
        description=(
            "Outer cylinder R_o=1000; 6-point star bore R_valley=250, R_tip=450, "
            "tip fillet=30, valley fillet=40; full length"
        ),
        params=dict(
            L=L, R_o=R_o, R_valley=R_valley, R_tip=R_tip,
            n_star=6, fillet_tip=fillet_tip, fillet_valley=fillet_valley,
        ),
        regions=[RegionBand("cylinder", 0.0, 1.0)],
        rebuild_args=["--axis", "z", "--sections", "60", "--chord-tol", str(CHORD_TOL)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.1,
            surface_deviation_max_mm=2.0 * CHORD_TOL,
            face_count_max=100,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=300.0,
        closed_form_volume=None,
    )


def _m4() -> MilestoneSpec:
    L, R_o = 10_000.0, 1_000.0
    R_bore = 300.0
    fin_z_start = 6_000.0
    fin_w = 80.0
    fin_r_inner = 300.0
    fin_r_outer = 700.0
    fin_tip_r = 40.0
    n_fins = 8
    # dz_min from tol table: max(4*chord_tol, L/5000)
    topo_tol = max(4 * CHORD_TOL, L / 5_000)
    return MilestoneSpec(
        name="M4",
        description=(
            "Finocyl: circular bore R=300 fore; 8 fin slots (w=80, radial 300→700, "
            "tip_r=40) aft of z=6000 with flat fore wall"
        ),
        params=dict(
            L=L, R_o=R_o, R_bore=R_bore, fin_z_start=fin_z_start,
            fin_w=fin_w, fin_r_inner=fin_r_inner, fin_r_outer=fin_r_outer,
            fin_tip_r=fin_tip_r, n_fins=n_fins,
        ),
        regions=[
            RegionBand("fore_cylinder", 0.0,              fin_z_start / L),
            RegionBand("fin_zone",      fin_z_start / L,  1.0),
        ],
        rebuild_args=["--axis", "z", "--sections", "80", "--chord-tol", str(CHORD_TOL)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.2,
            topo_event_z_tolerance_mm=topo_tol,
            face_count_max=300,             # anti-tessellation guard (exact M4 needs ~50)
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=600.0,
        closed_form_volume=None,
    )


def _m5() -> MilestoneSpec:
    L, R_o = 10_000.0, 1_000.0
    R_bore = 300.0
    dome_h = R_o / 2.0
    fin_z_start = 6_000.0
    fin_w = 80.0
    fin_r_inner = 300.0
    fin_r_outer = 700.0
    fin_tip_r = 40.0
    n_fins = 8
    topo_tol = max(4 * CHORD_TOL, L / 5_000)
    return MilestoneSpec(
        name="M5",
        description="M2 domes + M4 fins combined; adaptive station efficiency gate",
        params=dict(
            L=L, R_o=R_o, R_bore=R_bore, dome_semi_axial=dome_h,
            fin_z_start=fin_z_start, fin_w=fin_w, fin_r_inner=fin_r_inner,
            fin_r_outer=fin_r_outer, fin_tip_r=fin_tip_r, n_fins=n_fins,
        ),
        regions=[
            RegionBand("fore_dome",     0.0,              dome_h / L),
            RegionBand("fore_cylinder", dome_h / L,       fin_z_start / L),
            RegionBand("fin_zone",      fin_z_start / L,  1.0 - dome_h / L),
            RegionBand("aft_dome",      1.0 - dome_h / L, 1.0),
        ],
        rebuild_args=["--axis", "z", "--sections", "40", "--adaptive", "--chord-tol", str(CHORD_TOL)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.2,
            surface_deviation_p99_mm=0.8 * CHORD_TOL,
            surface_deviation_max_mm=1.2 * CHORD_TOL,
            topo_event_z_tolerance_mm=topo_tol,
            adaptive_efficiency=0.5,   # stations_used <= 0.5 * uniform_count_needed
            face_count_max=400,        # anti-tessellation guard
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=120.0,
        closed_form_volume=None,
    )


MILESTONES: Dict[str, MilestoneSpec] = {
    s.name: s for s in [_m1(), _m2(), _m3(), _m4(), _m5()]
}


def get(name: str) -> MilestoneSpec:
    if name not in MILESTONES:
        raise KeyError(f"Unknown milestone {name!r}. Valid: {list(MILESTONES)}")
    return MILESTONES[name]
