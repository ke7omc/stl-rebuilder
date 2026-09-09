"""Milestone specs: geometry params, region labels, gate thresholds, rebuild.py args, runtime caps.

One MilestoneSpec per milestone (M1–M13, MR). All geometry in mm unless a spec's `frame.units`
says otherwise (the truth is still generated/stored in mm; `frame.units` describes what unit the
milestone's *input STL* is written in, per MISSION §6.2/§7.2).
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CHORD_TOL = 0.5  # mm — default scoring/generation tolerance (passed to rebuild.py)

#: Half-width (mm) of the `fore_wall` / `aft_wall` feature bands that `station_bands` gates.
#: MISSION §6.2 sizes this deliberately: "at n=80 the Round 1 warp spaces mid-barrel stations
#: ≈ 310 mm apart, so a 300 mm feature band gets 0–1 stations". A band must therefore be ~300 mm
#: wide to force feature-aware placement. Defining `fore_wall` as *everything between the dome
#: and the slots* — 5293 mm on M8 — made the gate toothless: cosine end-clustering at n=80 puts
#: 32 stations in it and passes, which is exactly the algorithm M8 exists to reject.
WALL_BAND_MM = 150.0


@dataclass(frozen=True)
class RegionBand:
    label: str
    z_frac_lo: float  # inclusive, fraction of total axial length L
    z_frac_hi: float  # inclusive


@dataclass(frozen=True)
class Frame:
    """Where the milestone's input STL sits relative to the truth's canonical mm/+z frame.

    `axis` is the motor axis as a unit vector *in input coordinates* (Round 1 milestones are
    all canonical: axis=+z, origin at 0, units mm). `scale_to_mm` converts a length in `units`
    to mm (in=25.4, m=1000, mm=1).
    """
    axis: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    origin_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    units: str = "mm"

    @property
    def scale_to_mm(self) -> float:
        return {"mm": 1.0, "in": 25.4, "m": 1000.0}[self.units]


@dataclass(frozen=True)
class InputSpec:
    """How the milestone's input STL is synthesised from the analytic truth solid.

    kind="analytic": clean tessellation of the truth BRep at chord_tol (Round 1 behaviour).
    kind="voxel": narrow-band exact-SDF marching-cubes surface (harness/voxelize.py), optionally
    perturbed with noise / unwelded / flipped facets / disconnected islands, per MISSION §6.2.
    """
    kind: str = "analytic"                     # "analytic" | "voxel"
    spacing_mm: Optional[Tuple[float, float, float]] = None  # voxel grid pitch (x,y,z), mm
    noise_sigma_mm: float = 0.0                 # gaussian normal-direction noise, seed 7
    unweld_jitter_mm: float = 0.0               # per-facet vertex jitter (unwelds the mesh)
    flip_frac: float = 0.0                      # fraction of facets with reversed winding
    islands: int = 0                            # disconnected noise islands to inject
    watertight_expected: bool = True             # whether input_watertight gate should hold


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

    # --- Round 2 additions (MISSION §7.2). Defaults reproduce Round 1 (M1-M5) behaviour exactly:
    # canonical mm frame, clean analytic input, single solid, no station/topology count caps.
    chord_tol: float = CHORD_TOL
    frame: Frame = field(default_factory=Frame)
    input: InputSpec = field(default_factory=InputSpec)
    station_bands: Dict[str, int] = field(default_factory=dict)   # region label -> min station count
    n_stations_max: Optional[int] = None
    topo_events_z_mm: List[float] = field(default_factory=list)   # expected event z (truth frame)
    topo_events_max: Optional[int] = None
    n_solids: int = 1
    optional: bool = False       # True only for MR: pass:true,"skipped" when no input is present
    input_glob: Optional[str] = None   # e.g. "real_inputs/*.stl" for MR
    # Per-solid closed-form volumes (mm³) in ascending z-centroid order, for milestones with
    # n_solids > 1 (M11). None when n_solids == 1 or no closed form exists per-solid.
    per_solid_closed_form_volumes: Optional[Tuple[float, ...]] = None


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
            # MISSION §6's M4 row lists no deviation gate, but volume+bbox+face-count alone let a
            # plain annular cylinder with a fudged radius pass M4 with no fins at all. This is the
            # anti-gaming floor, deliberately looser than M5's 1.2*chord_tol on the same fins:
            # it only has to catch geometry that is grossly wrong (a missing slot is ~400 mm off).
            surface_deviation_max_mm=2.0 * CHORD_TOL,
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
            dome_stations_min=8,       # MISSION §6: M5 must hold "all M2 and M4 gates"
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


def _topo_tol(L: float, ct: float) -> float:
    return max(4.0 * ct, L / 5_000.0)


def _m6() -> MilestoneSpec:
    """Per-window loft: cylinder minus a ruled loft between the M3 star at z=-10 and the
    same star scaled x1.5 at z=L+10 (scale linear in z). MISSION §6.2 M6."""
    L, R_o, ct = 10_000.0, 1_000.0, CHORD_TOL
    R_valley0, R_tip0, fillet_tip0, fillet_valley0 = 250.0, 450.0, 30.0, 40.0
    scale1 = 1.5
    R_valley1, R_tip1 = R_valley0 * scale1, R_tip0 * scale1
    fillet_tip1, fillet_valley1 = fillet_tip0 * scale1, fillet_valley0 * scale1
    return MilestoneSpec(
        name="M6",
        description=(
            "Cylinder minus a ruled loft between the M3 star (250/450, fillets 30/40) at "
            "z=-10 and the same star scaled x1.5 (375/675, fillets 45/60) at z=L+10"
        ),
        params=dict(
            L=L, R_o=R_o, n_star=6,
            loft_z0=-10.0, loft_z1=L + 10.0,
            R_valley0=R_valley0, R_tip0=R_tip0,
            fillet_tip0=fillet_tip0, fillet_valley0=fillet_valley0,
            R_valley1=R_valley1, R_tip1=R_tip1,
            fillet_tip1=fillet_tip1, fillet_valley1=fillet_valley1,
        ),
        regions=[RegionBand("cylinder", 0.0, 1.0)],
        rebuild_args=["--axis", "z", "--sections", "60", "--chord-tol", str(ct)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.2,
            surface_deviation_p99_mm=0.8 * ct,
            surface_deviation_max_mm=2.0 * ct,
            face_count_max=200,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=300.0,
        closed_form_volume=None,  # filleted-star cross-section area has no trivial closed form
        chord_tol=ct,
    )


def _m7() -> MilestoneSpec:
    """Central bore + 6 satellite perforations dying into a flat end wall at z=7000.
    MISSION §6.2 M7 — loop classification + cross-station matching, N cutters."""
    L, R_o, ct = 10_000.0, 1_000.0, CHORD_TOL
    R_bore = 300.0
    n_sat, R_sat, r_sat = 6, 100.0, 600.0
    sat_z_end = 7_000.0
    topo_tol = _topo_tol(L, ct)
    V = math.pi * (R_o**2 - R_bore**2) * L - n_sat * math.pi * R_sat**2 * sat_z_end
    return MilestoneSpec(
        name="M7",
        description=(
            "Cylinder, flat ends; central bore R=300 through; 6 satellite perforations "
            "R=100 at r=600 every 60 deg, z=0..7000, flat end wall (6 chain deaths at 7000)"
        ),
        params=dict(
            L=L, R_o=R_o, R_bore=R_bore, n_sat=n_sat, R_sat=R_sat, r_sat=r_sat,
            sat_z_end=sat_z_end,
        ),
        regions=[
            RegionBand("satellite_zone", 0.0, sat_z_end / L),
            RegionBand("bore_only", sat_z_end / L, 1.0),
        ],
        rebuild_args=["--axis", "z", "--sections", "60", "--chord-tol", str(ct)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.1,
            surface_deviation_p99_mm=0.8 * ct,
            surface_deviation_max_mm=1.2 * ct,
            topo_events=topo_tol,
            face_count_max=60,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=300.0,
        closed_form_volume=V,
        chord_tol=ct,
        topo_events_z_mm=[sat_z_end],
        topo_events_max=2,
    )


def _m8() -> MilestoneSpec:
    """Feature-aware adaptive stations on a mid-burn grain: M5 cavity dilated by web w=150.
    MISSION §6.2 M8."""
    L, R_o, ct = 10_000.0, 1_000.0, CHORD_TOL
    dome_h = R_o / 2.0
    R_bore = 450.0
    n_slots, slot_half_w, slot_outer_r = 8, 190.0, 850.0
    slot_z_lo, slot_z_hi, slot_fillet = 5_850.0, 9_650.0, 150.0
    topo_tol = _topo_tol(L, ct)
    return MilestoneSpec(
        name="M8",
        description=(
            "M5 capsule minus cavity dilated by web=150: bore R=450 through; 8 obround slots "
            "half-width 190, outer r 850, z=[5850,9650], end fillets r=150"
        ),
        params=dict(
            L=L, R_o=R_o, dome_semi_axial=dome_h, R_bore=R_bore,
            n_slots=n_slots, slot_half_width=slot_half_w, slot_outer_r=slot_outer_r,
            slot_z_lo=slot_z_lo, slot_z_hi=slot_z_hi, slot_fillet=slot_fillet,
            dilation_w=150.0, slot_overlap=50.0,
        ),
        regions=[
            RegionBand("fore_dome", 0.0, dome_h / L),
            RegionBand("barrel", dome_h / L, (slot_z_lo - WALL_BAND_MM) / L),
            RegionBand("fore_wall", (slot_z_lo - WALL_BAND_MM) / L,
                       (slot_z_lo + WALL_BAND_MM) / L),
            RegionBand("slot_zone", slot_z_lo / L, slot_z_hi / L),
            RegionBand("aft_wall", (slot_z_hi - WALL_BAND_MM) / L,
                       (slot_z_hi + WALL_BAND_MM) / L),
            RegionBand("aft_dome", (L - dome_h) / L, 1.0),
        ],
        rebuild_args=["--axis", "z", "--sections", "80", "--adaptive", "--chord-tol", str(ct)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.2,
            surface_deviation_p99_mm=0.8 * ct,
            surface_deviation_max_mm=2.0 * ct,
            dome_stations_min=8,
            station_bands=True,
            n_stations_max=True,
            topo_events=topo_tol,
            face_count_max=300,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=600.0,
        closed_form_volume=None,
        chord_tol=ct,
        station_bands={"fore_wall": 10, "aft_wall": 10},
        n_stations_max=80,
        topo_events_z_mm=[slot_z_lo, slot_z_hi],
        topo_events_max=3,
    )


def _m9() -> MilestoneSpec:
    """M8 truth, but the input STL is a noisy skewed marching-cubes surface on an anisotropic
    grid (10,10,40) mm. MISSION §6.2 M9."""
    m8 = _m8()
    h_xy, h_z = 10.0, 40.0
    return MilestoneSpec(
        name="M9",
        description="M8 truth reused; input is a noisy, skewed marching-cubes surface (grid 10x10x40mm)",
        params=dict(m8.params),
        regions=m8.regions,
        rebuild_args=["--axis", "z", "--sections", "80", "--adaptive", "--chord-tol", "5"],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.5,
            surface_deviation_p99_mm=0.5 * h_xy,
            surface_deviation_max_mm=0.75 * h_z,
            dome_stations_min=8,
            station_bands=True,
            n_stations_max=True,
            topo_events=h_z,
            face_count_max=300,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=600.0,
        closed_form_volume=None,
        chord_tol=m8.chord_tol,
        station_bands=m8.station_bands,
        n_stations_max=m8.n_stations_max,
        topo_events_z_mm=m8.topo_events_z_mm,
        topo_events_max=m8.topo_events_max,
        input=InputSpec(
            kind="voxel", spacing_mm=(h_xy, h_xy, h_z), noise_sigma_mm=0.5,
            watertight_expected=True,
        ),
    )


def _m10() -> MilestoneSpec:
    """Frame normalisation: M8 truth scaled x1/40, axis rotated to +x, off-origin, STL in
    inches. MISSION §6.2 M10."""
    m8 = _m8()
    s = 1.0 / 40.0
    ct = CHORD_TOL * s
    scaled = {k: (v * s if k != "n_slots" and isinstance(v, (int, float)) else v)
              for k, v in m8.params.items()}
    frame = Frame(axis=(1.0, 0.0, 0.0), origin_mm=(254.0, -76.2, 101.6), units="in")
    return MilestoneSpec(
        name="M10",
        description="M8 truth scaled x1/40, rotated to +x axis, translated, STL written in inches",
        params=scaled,
        regions=m8.regions,
        rebuild_args=["--axis", "auto", "--units", "in", "--sections", "80", "--adaptive",
                      "--chord-tol", str(ct)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.2,
            surface_deviation_p99_mm=0.8 * ct,
            surface_deviation_max_mm=2.0 * ct,
            dome_stations_min=8,
            station_bands=True,
            n_stations_max=True,
            topo_events=_topo_tol(m8.params["L"] * s, ct),
            face_count_max=300,
            frame_axis_err_deg=0.1,
            axial_extent_err_mm=4.0 * ct,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=300.0,
        closed_form_volume=None,
        chord_tol=ct,
        frame=frame,
        station_bands=m8.station_bands,
        n_stations_max=m8.n_stations_max,
        topo_events_z_mm=[z * s for z in m8.topo_events_z_mm],
        topo_events_max=m8.topo_events_max,
    )


def _m11() -> MilestoneSpec:
    """Segmented BATES, 3 annular segments with gaps and flat ends, one STL / 3 shells.
    MISSION §6.2 M11."""
    R_o, ct = 1_000.0, CHORD_TOL
    segments = [
        dict(name="A", z_lo=0.0, z_hi=3_000.0, R_i=300.0),
        dict(name="B", z_lo=3_500.0, z_hi=6_500.0, R_i=450.0),
        dict(name="C", z_lo=7_000.0, z_hi=10_000.0, R_i=300.0),
    ]
    L_env = 10_000.0
    solid_volumes = [math.pi * (R_o**2 - s["R_i"]**2) * (s["z_hi"] - s["z_lo"]) for s in segments]
    return MilestoneSpec(
        name="M11",
        description="Segmented BATES: 3 annular segments (A R_i=300, B R_i=450, C R_i=300), gapped, flat ends",
        params=dict(R_o=R_o, L_env=L_env, segments=segments),
        regions=[RegionBand(s["name"], s["z_lo"] / L_env, s["z_hi"] / L_env) for s in segments],
        rebuild_args=["--axis", "z", "--sections", "40", "--chord-tol", str(ct)],
        gates=dict(
            n_solids=3,
            brep_valid=True,
            volume_err_pct=0.05,
            per_solid_volume_err_pct=0.05,
            surface_deviation_p99_mm=0.8 * ct,
            surface_deviation_max_mm=1.2 * ct,
            face_count_max=24,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=300.0,
        closed_form_volume=sum(solid_volumes),
        chord_tol=ct,
        n_solids=3,
        # segments is already ascending by z_lo (A, B, C), matching solid_volumes' order.
        per_solid_closed_form_volumes=tuple(solid_volumes),
    )


def _m12() -> MilestoneSpec:
    """Near-end-of-burn cavity decomposition: M5 cavity dilated by w=250, slots break through
    the dome, multi-outer-loop stations. MISSION §6.2 M12."""
    L, R_o, ct = 10_000.0, 1_000.0, CHORD_TOL
    dome_h = R_o / 2.0
    R_bore = 550.0
    n_slots, slot_half_w, slot_outer_r = 8, 290.0, 950.0
    slot_z_lo, slot_z_hi, slot_fillet = 5_750.0, 9_750.0, 250.0
    breakthrough_z = 9_656.0
    topo_tol = _topo_tol(L, ct)
    return MilestoneSpec(
        name="M12",
        description=(
            "M5 capsule minus cavity dilated by web=250: bore 550, 8 obround slots half-width "
            "290, outer r 950 (50mm web), z=[5750,9750], fillets r=250; aft slots break through "
            "the dome past z=9656; bore exits dome near z~83/9917"
        ),
        params=dict(
            L=L, R_o=R_o, dome_semi_axial=dome_h, R_bore=R_bore,
            n_slots=n_slots, slot_half_width=slot_half_w, slot_outer_r=slot_outer_r,
            slot_z_lo=slot_z_lo, slot_z_hi=slot_z_hi, slot_fillet=slot_fillet,
            breakthrough_z=breakthrough_z, dilation_w=250.0,
        ),
        regions=[
            RegionBand("fore_dome", 0.0, dome_h / L),
            RegionBand("barrel", dome_h / L, (slot_z_lo - WALL_BAND_MM) / L),
            RegionBand("fore_wall", (slot_z_lo - WALL_BAND_MM) / L,
                       (slot_z_lo + WALL_BAND_MM) / L),
            RegionBand("slot_zone", slot_z_lo / L, breakthrough_z / L),
            # MISSION §6.2 gates the breakthrough band as [9600, 9800]; the previous
            # [breakthrough_z, slot_z_hi] = [9656, 9750] was a stricter 94 mm window.
            RegionBand("breakthrough", 9_600.0 / L, 9_800.0 / L),
            RegionBand("aft_dome", (L - dome_h) / L, 1.0),
        ],
        rebuild_args=["--axis", "z", "--sections", "120", "--adaptive", "--chord-tol", str(ct)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.3,
            surface_deviation_p99_mm=0.8 * ct,
            surface_deviation_max_mm=2.0 * ct,
            dome_stations_min=8,
            station_bands=True,
            n_stations_max=True,
            topo_events=topo_tol,
            min_edge_mm=0.1,
            face_count_max=400,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=600.0,
        closed_form_volume=None,
        chord_tol=ct,
        station_bands={"fore_wall": 10, "breakthrough": 10},
        n_stations_max=120,
        topo_events_z_mm=[slot_z_lo, breakthrough_z, slot_z_hi],
        topo_events_max=5,
    )


def _m13() -> MilestoneSpec:
    """Capstone: M12 truth rotated/translated, input a real-STL-shaped voxel surface
    (noisy, unwelded, flipped facets, disconnected islands). MISSION §6.2 M13."""
    m12 = _m12()
    h = 8.0
    frame = Frame(axis=(1.0, 0.0, 0.0), origin_mm=(2_500.0, -700.0, 1_300.0), units="in")
    return MilestoneSpec(
        name="M13",
        description=(
            "M12 truth rotated to +x, translated (2500,-700,1300); input is isotropic h=8mm "
            "exact-SDF marching cubes, noisy, unwelded, 2% flipped facets, 3 noise islands"
        ),
        params=dict(m12.params),
        regions=m12.regions,
        rebuild_args=["--axis", "auto", "--units", "in", "--sections", "120", "--adaptive",
                      "--chord-tol", str(h)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.5,
            surface_deviation_p99_mm=0.5 * h,
            surface_deviation_max_mm=1.5 * h,
            dome_stations_min=8,
            station_bands=True,
            n_stations_max=True,
            topo_events=8.0,
            min_edge_mm=0.1,
            face_count_max=400,
            frame_axis_err_deg=0.1,
            axial_extent_err_mm=8.0,
            bbox_err_pct=0.1,
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
        ),
        runtime_cap_s=900.0,
        mesh_timeout_s=600.0,
        closed_form_volume=None,
        chord_tol=h,
        frame=frame,
        station_bands=m12.station_bands,
        n_stations_max=m12.n_stations_max,
        topo_events_z_mm=[m12.params["slot_z_lo"], m12.params["slot_z_hi"]],
        topo_events_max=5,
        input=InputSpec(
            kind="voxel", spacing_mm=(h, h, h), noise_sigma_mm=0.8, unweld_jitter_mm=1e-5,
            flip_frac=0.02, islands=3, watertight_expected=False,
        ),
    )


def _m14() -> MilestoneSpec:
    """Real burnback scenario Brady hit (2026-09-08, off the driver ladder): a 6-point star
    grain a few seconds from complete burnout, at BOTH domes. Family: the M2/M5/M8/M12 capsule
    (2:1 domes) with an M3-style star bore (n_star=6, R_valley/R_tip, fillet_tip/fillet_valley),
    dilated so far into the burn that the tip lobes' web (R_o - R_tip) has already burned through
    to the case surface well inside each dome, while the valley lobes' web (R_o - R_valley) has
    not -- severing the single annular station into `n_star` disjoint outer loops. This is the
    `TopologyError: expected 1 outer loop ... (unsupported topology)` Brady hit at station 1/300
    on his real motor.

    No z-dependent star dilation is needed: the star bore is a CONSTANT cross-section prism (same
    construction as `_star_bore_cutter`/M3), and the outer capsule surface is itself a body of
    revolution whose radius R_case(z) shrinks smoothly to 0 at each apex (`_ellipse_dome_edge`'s
    2:1 ellipse: R_case(z) = R_o*sqrt(1-((dome_h-z)/dome_h)**2) for z in [0, dome_h], mirrored for
    the aft dome). Cutting the constant star against that shrinking envelope does the topology
    work for free: wherever R_valley < R_case(z) < R_tip, the tip regions have already been cut
    away by the case boundary but the valley regions have not, giving `n_star` disjoint islands.

    `island_clip_margin` stops the truth solid a little short of the exact z where
    R_case(z) == R_valley: at that precise z each island's cross-section pinches to a single
    point (a genuine zero-width cusp), which is a valid BRep (`BRepCheck_Analyzer` accepts it)
    but not a *meshable* one -- gmsh's 3D generator raised "Invalid boundary mesh (overlapping
    facets)" on the unclipped solid regardless of fillet size (tried 40/50, 25/25, 15/20, 5/5:
    all failed identically), because the pinch is a topological feature of the crossing itself,
    not a tessellation-quality artefact. Trimming a flat cap `island_clip_margin` mm inside the
    crossing (confirmed meshable from a 10 mm margin already; 30 mm used here for headroom)
    removes the singular point while leaving the 6-disjoint-island cross-section intact and
    clearly visible right up to the cap -- exactly the topology the pipeline needs to handle,
    without the geometrically-unrelated "does gmsh tolerate an exact cusp" question attached.

    Gates (real station/topology grading, wired by `docs/plans/m14_multilobe_dome_breakthrough.md`
    Phase D, on top of the universal sanity set the geometry task originally shipped this
    milestone with -- n_solids, brep_valid, volume_err_pct, step_roundtrip_vol_err, bbox_err_pct,
    face_count_max): `surface_deviation_p99_mm`/`_max_mm` at M12's own 0.8·ct / 2·ct ratios;
    `dome_stations_min`/`station_bands`/`n_stations_max` tied together the same way M8+ already
    are; `topo_events` at `_topo_tol(L, ct)` for the two severed<->single-loop transition planes.
    Deliberately still ABSENT: `gmsh_min_sicn` -- the island tips are intrinsically thin (min
    SICN ~0.02 even well clear of the clip), an honest, MEASURED property of this geometry
    (§Risks in the plan), not a defect to gate on. `topo_events_z_mm` below is the MEASURED
    severing plane (bisecting `len(polys) > 1` on the real truth STL), not the analytic
    `z_tip_fore`/`z_tip_aft` computed above from the unfilleted crossing -- the star's tip fillet
    pulls the true plane 31.6 mm inboard (250.422 measured vs. 282.055 predicted), far outside
    `topo_tol`'s 2 mm, so the unfilleted value is unusable as gate config; re-measure (the plan's
    §D.1 one-shot script) and update both constants below if `_make_m14`'s params ever change.
    `regions` are fractions of the truth's own CANONICAL BBOX EXTENT `[z_clip_lo, z_clip_hi]`
    (matching how `score.py::_region_at` actually maps `z_frac`), not of `[0, L]`.
    """
    L, R_o, ct = 10_000.0, 1_000.0, CHORD_TOL
    dome_h = R_o / 2.0
    n_star = 6
    # R_valley/R_tip spread wide (rather than M3-scale 250/450) for a second reason beyond
    # dilation state: the exposed island band (z_tip .. z_clip_lo) must clear
    # rebuild.py's own station-placement end-inset (`station_eps` in pipeline/engine.py,
    # min(max(eps_end, 200*chord_tol), 0.02*L) -- 100 mm at this milestone's default
    # chord_tol=0.5) or every station lands past the island band and the crash never
    # reproduces. Measured empirically: R_valley=700 left only an 89 mm exposed band (too
    # narrow, silently swallowed by the 100 mm inset -- confirmed by a rebuild that ran clean
    # instead of crashing); R_valley=550 leaves ~170 mm, comfortably clear with headroom for a
    # somewhat coarser --chord-tol than the default.
    R_valley, R_tip = 550.0, 900.0
    fillet_tip, fillet_valley = 40.0, 50.0
    island_clip_margin = 30.0

    def _z_at_case_radius(r: float) -> float:
        return dome_h * (1.0 - math.sqrt(1.0 - (r / R_o) ** 2))

    z_tip_fore = _z_at_case_radius(R_tip)          # single-loop -> island-band transition (fore)
    z_valley_fore = _z_at_case_radius(R_valley)    # exact (unclipped) island pinch-to-a-point, fore
    z_tip_aft = L - z_tip_fore
    z_valley_aft = L - z_valley_fore
    z_clip_lo = z_valley_fore + island_clip_margin  # truth solid's actual fore end (flat cap)
    z_clip_hi = z_valley_aft - island_clip_margin   # truth solid's actual aft end (flat cap)

    # MEASURED severing plane (M14 fix plan §D.1: bisect `pipeline.slicing.slice_station` on
    # `len(polys) > 1` against `harness/truth/M14.stl` at commit 75e8377) -- 31.6 mm inboard of
    # the unfilleted `z_tip_fore`/`z_tip_aft` crossing above, because the star's tip FILLET pulls
    # the true tip-vs-case crossing in from the sharp-cornered analytic prediction. This is what
    # `topo_events_z_mm` and the island region bands below are built from, not `z_tip_fore`/
    # `z_tip_aft` themselves (those remain useful only for the human-readable description and as
    # the "before fillets" reference point the docstring above cites).
    z_severed_fore = 250.422
    z_severed_aft = 9_749.578
    S = z_clip_hi - z_clip_lo  # canonical bbox extent -- `score.py::_region_at` maps z_frac over
                               # [z_min, z_max] of the truth's OWN bbox, not [0, L]
    topo_tol = _topo_tol(L, ct)

    return MilestoneSpec(
        name="M14",
        description=(
            f"M5-family capsule (2:1 domes) minus a 6-point star bore (R_valley={R_valley:.0f}, "
            f"R_tip={R_tip:.0f}, fillet_tip={fillet_tip:.0f}, fillet_valley={fillet_valley:.0f}), "
            "dilated to a near-total-burn state: constant cross-section, so the shrinking dome "
            "envelope alone severs the ring into 6 disjoint islands over "
            f"z=[{z_clip_lo:.1f},{z_tip_fore:.1f}] (fore) and z=[{z_tip_aft:.1f},{z_clip_hi:.1f}] "
            f"(aft); the solid is capped with flat end faces {island_clip_margin:.0f} mm short of "
            "each side's exact zero-width island pinch"
        ),
        params=dict(
            L=L, R_o=R_o, dome_semi_axial=dome_h,
            n_star=n_star, R_valley=R_valley, R_tip=R_tip,
            fillet_tip=fillet_tip, fillet_valley=fillet_valley,
            island_clip_margin=island_clip_margin, z_clip_lo=z_clip_lo, z_clip_hi=z_clip_hi,
        ),
        regions=[
            # Fractions of the truth's own CANONICAL BBOX EXTENT [z_clip_lo, z_clip_hi] (span S),
            # not of [0, L] -- `score.py::_region_at` maps z_frac over the truth's real bbox.
            # Island bands listed before the overlapping dome bands they sit inside of, so a
            # failure hint's `_region_at` lookup (first-hit-wins) localizes to the more specific
            # label. `dome_stations_min` requires a "dome"-labelled band to exist at all.
            RegionBand("fore_islands", 0.0, (z_severed_fore - z_clip_lo) / S),
            RegionBand("fore_dome", 0.0, (dome_h - z_clip_lo) / S),
            RegionBand("barrel", (dome_h - z_clip_lo) / S, (L - dome_h - z_clip_lo) / S),
            RegionBand("aft_dome", (L - dome_h - z_clip_lo) / S, 1.0),
            RegionBand("aft_islands", (z_severed_aft - z_clip_lo) / S, 1.0),
        ],
        rebuild_args=["--axis", "z", "--sections", "300", "--adaptive", "--chord-tol", str(ct)],
        gates=dict(
            n_solids=1,
            brep_valid=True,
            volume_err_pct=0.3,
            step_roundtrip_vol_err=1e-6,
            face_count_max=100,
            bbox_err_pct=0.1,
            # --- station/topology grading, wired by the M14 fix plan's Phase D on top of the
            # universal sanity gates above (own commit; see this function's docstring) ---
            surface_deviation_p99_mm=0.8 * ct,    # M12's own ratio: 0.4 mm
            surface_deviation_max_mm=2.0 * ct,    # M12's own ratio: 1.0 mm
            dome_stations_min=8,
            station_bands=True,
            n_stations_max=True,
            topo_events=topo_tol,
        ),
        runtime_cap_s=600.0,
        closed_form_volume=None,
        chord_tol=ct,
        station_bands={"fore_islands": 10, "aft_islands": 10},
        n_stations_max=300,
        topo_events_z_mm=[z_severed_fore, z_severed_aft],
        topo_events_max=4,   # 2 expected (fore/aft severing plane) + headroom against gaming
    )


def _mr() -> MilestoneSpec:
    """Real-STL slot: optional, self-referential (no analytic truth). MISSION §6.2 MR.
    Scorer emits pass:true,"skipped": "no real input" when real_inputs/ is empty."""
    return MilestoneSpec(
        name="MR",
        description="First real_inputs/*.stl (+ optional matching .json) — self-referential checks only",
        params={},
        regions=[],
        rebuild_args=["--axis", "auto", "--adaptive", "--sections", "120"],
        gates=dict(
            n_solids=1,   # minimum; checked as >= 1, not ==
            brep_valid=True,
            volume_err_pct=0.5,
            surface_deviation_p99_mm=True,   # threshold computed at runtime as 1.0*ct_est
            surface_deviation_max_mm=True,   # threshold computed at runtime as 4.0*ct_est
            step_roundtrip_vol_err=1e-6,
            gmsh_min_sicn=0.1,
            min_edge_mm=0.1,
        ),
        runtime_cap_s=1_800.0,
        closed_form_volume=None,
        optional=True,
        input_glob="real_inputs/*.stl",
    )


MILESTONES: Dict[str, MilestoneSpec] = {
    s.name: s for s in [
        _m1(), _m2(), _m3(), _m4(), _m5(),
        _m6(), _m7(), _m8(), _m9(), _m10(), _m11(), _m12(), _m13(),
        _m14(),
        _mr(),
    ]
}


def get(name: str) -> MilestoneSpec:
    if name not in MILESTONES:
        raise KeyError(f"Unknown milestone {name!r}. Valid: {list(MILESTONES)}")
    return MILESTONES[name]
