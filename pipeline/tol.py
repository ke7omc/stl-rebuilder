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
