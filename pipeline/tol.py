"""Tolerance table, MISSION.md §5.4. Every value is derived from `chord_tol` (mm)."""
import math


def eps_end(chord_tol: float, L: float) -> float:
    return max(2.0 * chord_tol, 1e-4 * L)


def eps_cut(chord_tol: float) -> float:
    return 10.0 * chord_tol


def fuzzy(chord_tol: float) -> float:
    return chord_tol


def sew_tol(chord_tol: float) -> float:
    return 2.0 * chord_tol


def a_min(chord_tol: float) -> float:
    return math.pi * (5.0 * chord_tol) ** 2


def circle_max_resid(chord_tol: float) -> float:
    return 1.5 * chord_tol


def dz_min(chord_tol: float, L: float) -> float:
    return max(4.0 * chord_tol, L / 5000.0)


def topo_tol(chord_tol: float, L: float) -> float:
    return max(dz_min(chord_tol, L), 4.0 * chord_tol)


def rdp_profile_eps(chord_tol: float, r_ref: float) -> float:
    """RDP epsilon for the (z, R) meridian of a revolve, `r_ref` the run's largest radius.

    `chord_tol` bounds the *input's* fidelity; it is not a licence to add that much error again
    in the reconstruction. A straight run's RDP chord is pinned to its two end anchors, and in
    `build_revolve_solid` those anchors are the neighbouring dome window's boundary points, which
    sit just inside the dome curvature at a smaller radius — so an epsilon of 0.5*chord_tol
    collapses a whole cylindrical barrel onto the *shoulder* radius. Measured on M13 (chord_tol
    8, so eps 4 mm): the outer face came out a single cylinder at R=997.881 against station
    circle fits of 998.66-998.73, spending 0.30% of a 0.5% volume gate on simplification alone.
    Volume error from a radial bias is 2*dR/R, so the bound has to be a fraction of the radius.
    """
    return min(0.5 * chord_tol, 2.0e-4 * r_ref) if r_ref > 0.0 else 0.5 * chord_tol


def sat_min_span(chord_tol: float) -> float:
    """Minimum axial span for a standalone satellite cutter (cylinder or prism). A chain whose
    birth-to-death window is narrower than this is a sub-tolerance flicker: at the user's own
    stated chord_tol the feature is inside the input's noise floor, and a cutter that thin is
    thinner than (or comparable to) the boolean fuzzy value it must survive — BOPAlgo either
    fails outright or 'succeeds' into a corrupt BRep (observed on M8 at chord_tol 26.675 with
    --adaptive: 6.5-18 mm sliver windows vs fuzzy 26.675 -> RuntimeError from BRepAlgoAPI_Cut,
    or a final solid failing BRepCheck_Analyzer). The surrounding merged-ring path already
    covers the same z range as one combined hole, so dropping the sliver costs at most ~this
    much un-cut axial span — the same argument as the <3-station ring-chain guard in engine."""
    return 2.0 * chord_tol
